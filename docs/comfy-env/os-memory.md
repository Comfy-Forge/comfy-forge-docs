# How operating systems manage memory

*The vocabulary [ComfyUI memory management](comfyui-memory.md) borrows. If page
faults, swap, pinning and memory mapping are already familiar, you do not need
this page.*

Nothing here is specific to ComfyUI or to machine learning. Every term belongs to
the operating system rather than to any application, which is why it is kept
separate: it is true regardless of what upstream does next.

Two subpages go further. [Kernel and driver differences](kernel-differences.md)
covers where Linux, Windows and macOS diverge and how the graphics driver sits
differently on each. [Where the operating systems differ](os-memory-differences.md)
covers what ComfyUI and comfy-env actually do about those divergences.

## The kernel

The kernel is the part of the operating system that owns the hardware. 

Your program never touches physical memory. It asks the kernel for memory, the kernel
decides what to give it, and the kernel remains free to change its mind later.

There is one on every machine you will run ComfyUI on.

Linux, the Windows NT kernel, and Darwin on macOS all do the things below, with different names and
different defaults. Where those defaults diverge enough to matter is
[Kernel and driver differences](kernel-differences.md).

## Virtual memory, pages and faults

A **process** is one running program.

ComfyUI is a process. Your browser is another, and so is every terminal you have open.

The kernel keeps them apart: one process cannot read another's memory, cannot see its variables, and cannot free its memory. When a process exits, everything it was holding is returned,
which is why restarting an application sometimes fixes so many memory problems.

That separation is why comfy-env exists, and it is worth holding on to. It runs
node code in a separate process, so that code gets its own isolated memory and
its own set of installed packages.

The cost is that the two processes cannot see each other's memory, and they are both using the same graphics card and RAM.

The kernel shows every process a private address space that has nothing to do with where the
bytes really are. The kernel keeps a table mapping your addresses to physical
locations, and it works in fixed size chunks called **pages**, usually four
kilobytes, though [not on Apple Silicon](kernel-differences.md#the-differences).

An address in your space might be backed by several different things:

* physical RAM, either yours alone or shared with another process
* nothing yet, because address space is reserved separately from memory (though hardly a problem for 64-bit systems)
* swap, if the kernel wrote the page to disk to reclaim the RAM
* a file, if the page came from one and the kernel dropped it knowing it can
  read it again
* a single shared page of zeroes, which is what untouched memory usually reads
  from until you write to it
* squashed down and still in RAM, which is what macOS does by default and what
  Linux does if zram or zswap is set up
* memory on another device entirely, which is how a graphics card's memory
  appears in your address space

When you touch an address that is not currently mapped to usable memory, the
processor stops your program and hands control to the kernel. That is a **page
fault**, and the name is misleading because it is a normal event rather than an
error.

Two kinds matter, and the difference is everything:

* A **minor fault** means the data is already in RAM and the kernel only has to
  update its table. This is fast, and it is what happens the first time you touch
  a freshly allocated page.
* A **major fault** means the data is not in RAM and has to be read from disk
  first. This is slow enough to dominate anything else your program was doing.

Reserving address space is therefore cheap, because address space is just
bookkeeping. Physical memory is the scarce thing, and you do not get it until you
touch the address.

What happens when you ask for more physical memory than exists is the sharpest
difference between the three systems: Linux agrees and kills something later,
Windows refuses on the spot. See
[asking for more than exists](kernel-differences.md#2-what-happens-when-you-ask-for-too-much).

## Swap

When physical RAM runs short, the kernel picks pages that have not been used
recently, writes them to disk, and reuses the RAM. Touching one of those pages
later causes a major fault and the kernel reads it back.

Linux calls this **swap** and keeps it in a swap partition or file, or has none
at all, which is common on servers and
[changes what running short means](kernel-differences.md#the-differences).
Windows calls it the **pagefile**. macOS keeps dynamic swapfiles and, before resorting to them,
compresses inactive pages in RAM instead.

The effect on you is the same everywhere. Memory your program believes it has can
quietly be sitting on a disk, and the first access after that pays for a read.
Nothing in your program is told when this happens.

## The page cache

Spare RAM does not go unused. When the kernel reads a file it keeps the contents
in RAM in case someone reads them again, and it holds on to those pages until
something needs the memory more.

This is the **page cache**, and it is why a
freshly booted machine reports a lot of free memory and a machine that has been
working reports very little.

That is not a leak, and it matters here for one specific reason. Tools that ask
the kernel how much memory is available get an answer that includes reclaimable
page cache. Inside a container the same question has a
[worse answer still](kernel-differences.md#9-container-limits): it describes the
host rather than the limit you are actually bound by.

Memory that is being used, and would be given back under pressure,
counts as available.

## Memory mapping a file

Instead of reading a file into a buffer, a program can ask the kernel to **map**
it into its address space. After that, reading the file is just reading memory.
The kernel loads pages when they are touched, and can drop them again when RAM is
needed, because it knows it can always fetch them from the file.

This is how large model files are usually loaded. The benefit is that the program
never allocates a second copy: the pages are the file, and if two processes map
the same file they share the same physical pages. The cost is that the memory is
not really the program's, and the kernel can take it back at any point.

### Compressed memory

Writing a page to disk to reclaim its RAM is expensive, so both macOS and Linux
will do something cheaper first where they can: compress the page and keep it in
memory.

Pages full of zeroes and repetition squash well, so a four kilobyte page
might take one kilobyte afterwards. Three quarters of it comes back without any
disk involved.

The cost is processor time rather than disk time, and processors are so much
faster than disks that this is usually the better trade. macOS has done it by
default for years.

On Linux it is zram, which is a compressed block device used
as swap, or zswap, which compresses pages first and only writes them out if the
compressed pool fills up. Which systems do this without being asked is
[a default rather than a capability](kernel-differences.md#4-compressed-memory).

It is worth naming as its own state because a compressed page sits between the
other two.

It never went to disk, so it is not swapped. But the processor cannot
read compressed bytes, so it is not usable either. Touching it faults exactly as
a swapped page does, and the kernel resolves the fault by decompressing rather
than by reading a disk, which is a completely different cost for the same event.

## Memory that is not memory

The last item in that list is worth separating out, because it is how graphics
cards appear to a program at all.

An address in your process can map to memory on a device rather than to system
RAM. Reading it does not touch the machine's memory at any point; the processor
issues a transaction across the bus instead. This is why a pointer to memory on
a card looks like an ordinary pointer and behaves nothing like one.

Who owns that memory, and whether it can be taken from you while you are using
it, is
[decided by the operating system rather than by you](kernel-differences.md#the-graphics-driver-sits-in-a-different-place-on-each-system).

It is also why reserving address space and committing memory to it are separate
operations for devices too. An address can be reserved and mapped to nothing, in
which case it is a valid address that no hardware answers for. Touching one of
those does not produce a normal page fault that the kernel can resolve, because
there is nothing to resolve it with.

## Pinned memory

Everything above assumes the kernel may move your pages around or write them to
disk. Sometimes it must not.

**Pinning** a page, also called page locking, tells the kernel to leave it alone:
do not swap it, do not relocate it. The page's physical address then stays valid
until it is unpinned.

This matters for hardware that reads memory on its own, without the processor's
help, which includes every modern GPU.

Normally when data moves, the processor does the moving: read a chunk, write it
elsewhere, repeat, and it is occupied for the whole duration. A card with its own
copy engine does the moving itself, so the processor hands over a starting
address and a length and then goes and does something useful. The name for that
arrangement is **direct memory access**, and it is the reason a large transfer
does not cost you a busy processor for its whole length.

The catch is that the copy engine is not the processor, so it does not use the
processor's address translation. It works in raw physical addresses and needs one
to stay valid for the whole transfer. Every trick on this page is therefore
invisible to it: it cannot be told that a page moved.

Ordinary pages offer no such guarantee, so the driver keeps a small pinned buffer
of its own, in ordinary system memory, copies your data into that, and transfers
from there. Two operations rather than one.

The staging buffer is on the host side, not on the card. If it were already on
the card there would be nothing left to do.

!!! note "This is hardware, not an operating system feature"
    The copy engine, the physical addressing and the staging buffer work the same
    way on Linux, Windows and macOS, because they belong to the card and the bus.
    What differs between systems is only how much you are permitted to pin, and
    on Apple Silicon whether there is a transfer to perform at all. Both are in
    [Kernel and driver differences](kernel-differences.md).

!!! note "Pinned and ordinary memory are both real RAM"
    The difference is not where the bytes are. It is whether the kernel has
    promised not to move them.

### How a program actually pins something

The operating system has a primitive for this on its own: `mlock` on Linux and
macOS, `VirtualLock` on Windows. That stops the pages being swapped, and for a
device it is not enough on its own, because the driver also has to know the
memory exists and prepare it for the copy engine to reach.

So every accelerator vendor exposes its own call that does both. There are two
shapes of it, and the difference matters:

| Approach | CUDA | What it does |
|---|---|---|
| Allocate it already pinned | `cudaHostAlloc` | you get a new buffer that was pinned from birth |
| Pin memory you already have | `cudaHostRegister` | the pages you are holding are locked in place |

The second is cheaper when you already have the data, because the first means
allocating somewhere new and copying into it. Measured on this machine, pinning
512 MiB:

```
allocate a new pinned buffer and copy into it     0.410 s
pin the buffer already in hand, in place          0.171 s
allocate one that is born pinned                  0.257 s
```

None of those are fast. Pinning runs at a few gigabytes per second at best,
which is slower than the transfer it is meant to accelerate. It is worth doing
only for memory you will send many times.

AMD has the same pair under `hipHostMalloc` and `hipHostRegister`, and Intel's
Level Zero has host allocations that serve the same purpose. The requirement is
not a vendor's idea; it comes from the copy engine reading physical addresses,
so anything with a copy engine needs it.
[How far the vendors really diverge](kernel-differences.md#do-the-other-vendors-use-different-concepts)
is less than most people expect.

The exception is unified memory, as on
[Apple Silicon](kernel-differences.md#apple-silicon-is-the-real-exception), where the processor and
the graphics hardware address the same physical memory. Nothing crosses a bus,
so there is nothing to stage and no reason to pin.

Pinning is not free, and the cost falls on the whole machine rather than on the
program doing it. Pages the kernel cannot move or swap are pages it cannot use to
relieve pressure anywhere else. A program that pins without limit makes every
other program on the machine worse, which is why anything doing it seriously
keeps a budget. How much you are allowed to pin differs by roughly a factor of
two between systems, which is
[the pinning ceiling](kernel-differences.md#5-the-pinning-ceiling).

## Where the systems differ

Everything above is how it works on all three, near enough. The differences are
real but they are details rather than a different design. Eleven are worth
knowing:

1. Page size
2. What happens when you ask for more than exists
3. Whether swap exists at all
4. Whether compressed memory is on by default
5. The pinning ceiling
6. Who owns graphics memory
7. Whether per process graphics memory is visible
8. Whether moving GPU memory to RAM frees anything
9. Container limits
10. How processes share memory
11. Whether a GPU buffer can be handed to another process

Numbers 6 and 7 are the reason this documentation exists at all. On Windows the
display driver can take graphics memory from a running process, and no process
can see how much of the card another one holds. Both fail silently: nothing
raises an error, the machine simply gets slow.

All eleven are set out, along with how the graphics driver sits differently on
each system and whether AMD, Intel and Apple use different concepts, in
[Kernel and driver differences](kernel-differences.md). What ComfyUI and
comfy-env actually do about them is
[Where the operating systems differ](os-memory-differences.md).

## Why all of this shows up in the ComfyUI pages

Model weights are large, they move between the graphics card and host memory
constantly, and the moving is what costs time. Every term above turns up in that
story: weights are mapped from files, evicted into ordinary memory, pinned to
transfer quickly, and lost to swap when the machine is under pressure. The
question of how much memory is free turns out to have several answers depending
on which of these you count.

Back to [ComfyUI memory management](comfyui-memory.md).
