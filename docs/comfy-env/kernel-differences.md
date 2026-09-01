# Kernel and driver differences

*What Linux, Windows and macOS do differently with memory, and what the graphics
driver does differently on each of them. Nothing here is specific to ComfyUI.*

Read [How operating systems manage memory](os-memory.md) first. That page
describes what all three do. This one is the exceptions, and
[Where the operating systems differ](os-memory-differences.md) is what ComfyUI
and comfy-env do about them.

## What is actually shared

Quite a lot, and it is worth saying so before the table of exceptions makes it
look otherwise.

All three give every process a private address space, work in fixed size pages,
map those pages through a table the processor consults, fault when you touch
something not currently mapped, keep file contents in spare memory, let you map
a file into your address space, write pages out under pressure, and let a program
pin pages so hardware can read them directly.

Those are not conventions that happened to converge. They are what the memory
management unit in the processor makes possible, so the designs had nowhere else
to go. The differences below are what the three chose on top of that.

## The differences

| # | Difference | Linux | Windows | macOS | In plain terms |
|---|---|---|---|---|---|
| 1 | Page size | 4 KiB | 4 KiB | **16 KiB** on Apple Silicon | The unit memory is handed out in. Larger pages mean fewer faults and more waste per page. |
| 2 | Asking for more than exists | Says yes, then kills a process later | Says **no** immediately | Says yes, then leans on applications to free | Linux writes cheques it may not honour. Windows refuses at the counter. |
| 3 | Whether swap exists | Often not, especially on servers | Effectively always | Always, created on demand | Without swap, running short kills something instead of slowing everything down. |
| 4 | Compressed memory | Off unless configured | On by default | On by default | Squashing pages rather than writing them out. Two of the three do it without being asked. |
| 5 | Pinning ceiling | May count swap toward it, up to roughly 90% of RAM | Near 40%, because the OS caps locked pages | Not applicable for the GPU | The same program can pin about twice as much on Linux as on Windows. |
| 6 | Who owns graphics memory | The driver | **The operating system** | Shared with the CPU | On Windows your allocation can be moved without you being told. |
| 7 | Per process graphics memory | Visible | **Not visible** | Not applicable | On Windows you cannot ask which process is using the card. |
| 8 | Moving GPU memory to RAM | Frees the card | Frees the card | **Frees nothing** | On Apple Silicon they are the same memory. |
| 9 | Container limits | cgroups, widely ignored by tooling | A different model | Not applicable | Inside a container the usual answers describe the host. |
| 10 | Sharing memory between processes | Anonymous, cleaned up on crash | Named, leaks on crash | Named | Linux can hand over a nameless block of memory across a socket. |
| 11 | Handing a GPU buffer to another process | Works | Unproven on consumer cards | Not applicable | Elsewhere it round trips through host memory. |

## The ones worth understanding properly

### 2. What happens when you ask for too much

This is the difference most likely to make code behave differently rather than
just perform differently.

**Linux overcommits.** It will promise memory it does not have, on the reasonable
bet that most programs never touch everything they allocate. Allocation succeeds.
The reckoning arrives later, when you touch the pages and there is nothing to
give you, and the kernel picks a process and kills it. The policy is one file:

```
$ cat /proc/sys/vm/overcommit_memory
0        # 0 = heuristic, 1 = always allow, 2 = strict accounting
```

Under the default heuristic, an allocation succeeding tells you very little.

**Windows does not overcommit.** Every commitment is charged against RAM plus
pagefile, and when the charge cannot be met the allocation fails then and there.
A program that carefully handles a failed allocation is handling something that
mostly happens on Windows.

**macOS sits between**, allowing the allocation and then applying pressure
through notifications that well behaved applications respond to by freeing
caches.

### 4. Compressed memory

Writing a page to disk to reclaim its memory is expensive, so two of the three
try something cheaper first: compress the page and keep it in RAM. Pages full of
zeroes and repetition squash well.

macOS has done this by default for years, before it will resort to swapfiles at
all. Windows has done it by default since Windows 10. On Linux it is opt in, as
zram, which is a compressed block device used as swap, or zswap, which compresses
first and only writes out when the compressed pool fills.

The state is genuinely distinct from both of the others. The page never went to
disk, so it is not swapped. The processor cannot read compressed bytes, so it is
not usable either. Touching it faults exactly as a swapped page does, and the
kernel resolves it by decompressing rather than by reading a disk.

### 5. The pinning ceiling

Pinned pages cannot be moved or swapped, so they come out of the whole machine's
flexibility rather than out of the program that asked for them. Every system caps
how many there can be, and the caps differ by roughly a factor of two.

Windows itself limits locked pages to around half of RAM, so anything sensible
stays below that. Linux is more generous and can be told to count swap toward the
ceiling, on the reasoning that the rest of the system has somewhere to spill to.

The consequence is that identical code on identical hardware gets materially more
pinnable memory on Linux, and therefore keeps more of its working set in the fast
tier.

### 9. Container limits

A memory limit on a container is enforced by the kernel through cgroups, and the
enforcement is real: exceed it and the process is killed.

The problem is that the usual ways of asking how much memory exists do not read
that limit. They read the host's figures. So a program inside a container with a
two gigabyte limit, running on a machine with two hundred, will believe it has
two hundred, size itself accordingly, and be killed while its own accounting
still reports plenty of room.

This is not obscure. Very little software reads cgroup limits, and almost
everything reads the host numbers.

## The graphics driver sits in a different place on each system

Rows 6 and 7 are not really memory differences. They are a difference about who
owns the card, and it is the largest divergence on this page.

### Linux

The driver is a kernel module with a userspace library on top, and **the driver
owns the card's memory**. When a process allocates, that allocation belongs to it
until it frees it. Nothing arbitrates between processes and nothing relocates an
allocation behind your back.

The consequence for measurement is that the free memory figure the driver reports
is the device's real free memory, and one process can see how much of the card
another process is using.

### Windows

The display driver model puts **the operating system in charge instead**. A video
memory manager sits between every process and the card. It decides who gets what,
and it may move an allocation out to system memory to serve something else, at
any time, without telling the process that owns it.

Two consequences follow, and both are the sort of thing that costs an afternoon:

* The free memory figure a process reads is **its own budget, not the device
  total**. Another process can fill the card and your reading barely moves.
* Over committing the card does not produce an error. The allocation still
  succeeds, the memory is simply no longer on the card, and the symptom is that
  everything becomes mysteriously slow.

NVIDIA's datacenter cards can be switched into a mode that bypasses the display
driver model entirely and behaves like Linux. Consumer cards cannot.

### macOS

The question does not arise. NVIDIA stopped shipping macOS drivers years ago and
there is no CUDA on macOS at all. Apple's own stack is the only option, and it
works differently enough to need its own section.

## Do the other vendors use different concepts?

Less than you would expect, with one genuine exception.

### AMD is deliberately a near clone

HIP is designed to be source compatible with CUDA, call for call: `hipMalloc`,
`hipMemcpy`, `hipHostRegister`. The concepts map one to one because the hardware
arrangement is the same, a discrete card across a bus with its own memory and its
own copy engine.

In PyTorch it goes further than resemblance. On a ROCm build, AMD **is** CUDA
from the caller's side: `torch.cuda.is_available()` returns true, and the
`torch.cuda` calls work. The only way to tell is which version string is set:

```python
torch.version.cuda    # set on NVIDIA
torch.version.hip     # set on AMD
```

So code written against CUDA mostly runs on AMD without a separate path, and
where a difference is needed it is because the hardware or the driver differs,
not because the model does.

### Intel is a different API for the same arrangement

Level Zero and SYCL are shaped differently from CUDA, and in PyTorch they live in
their own namespace, so every call site needs its own branch: `torch.xpu.empty_cache()`,
`torch.xpu.synchronize()`. That makes supporting Intel more work than supporting
AMD.

But the underlying model is unchanged. A card across a bus, with its own memory,
reached by a copy engine that reads physical addresses. Pinning, staging and
transfers all mean what they mean everywhere else. The work is in the plumbing
rather than in the ideas.

### Apple Silicon is the real exception

The processor and the graphics hardware address **the same physical memory**.
There is no bus to cross.

That removes the entire vocabulary rather than renaming it. There is no transfer
to accelerate, so nothing to stage, so no reason to pin. Moving a model "to the
CPU" frees nothing, because it was never anywhere else. The distinction between
device memory and host memory, which is the axis everything else on these pages
is organised around, does not exist.

Apple's own model replaces it with storage modes on a buffer, describing who may
see the memory rather than where it lives.

## The one line summary

The concepts are shared because the hardware forced them. The policies differ
because each system made its own call about who to trust: Linux trusts programs
not to use what they asked for, Windows trusts nobody and charges up front, and
Apple removed the boundary that most of the difficulty lives on.
