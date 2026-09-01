# Where the operating systems differ

*The exceptions to [How operating systems manage memory](os-memory.md), and what
ComfyUI and comfy-env actually do about each one.*

*Last verified against ComfyUI `b133e483` (2026-08-26) and comfy-env `bda45b7`.*

The general form of these differences, including how the graphics driver sits
differently on each system and whether AMD, Intel and Apple use different
concepts, is [Kernel and driver differences](kernel-differences.md).

This page is the other half: what ComfyUI and comfy-env actually **do** about
each one. Every row below is something the code branches on.

## The eleven differences

<div class="num-col" markdown>

| # | Difference | Linux | Windows | macOS | Why you care |
|---|---|---|---|---|---|
| 1 | Page size | 4 KiB | 4 KiB | **16 KiB** on Apple Silicon | The unit memory is handed out in. Bigger pages mean fewer faults and more waste per page. |
| 2 | Asking for more than exists | Says yes, then the OOM killer picks a victim later | Says **no** immediately and the allocation fails | Says yes, then pressures applications to free | Linux writes cheques it may not honour. Windows refuses at the counter. |
| 3 | Whether swap exists | Often not, especially on servers | Effectively always, there is a pagefile | Always, created on demand | Without swap, running short kills a process instead of slowing everything down. |
| 4 | Compressed memory | Off unless zram or zswap is configured | On by default | On by default, before it will swap | Two of the three squash pages before writing them out. Linux only if you asked. |
| 5 | Pinned memory ceiling | May count swap toward the budget, up to about 90% of RAM | Capped near 40%, because the OS limits locked pages | No pinning path at all | Roughly 2.3 times more pinnable memory on Linux than Windows, same hardware. |
| 6 | Who owns GPU memory | The driver reports it and nobody moves it | The display driver arbitrates it and can page another process out | Unified with system RAM | On Windows your GPU memory can be taken while you are using it. |
| 7 | Per process GPU memory | Visible through NVML | **Not visible** under WDDM | Not applicable | On Windows you cannot ask which process is holding the card's memory. |
| 8 | Evicting GPU memory to RAM | Frees VRAM | Frees VRAM | **Frees nothing** | On Apple Silicon both halves of the ledger are the same bytes. |
| 9 | Container limits | cgroups enforce them and most tools ignore them | A different model | Not applicable | Inside a container every "available memory" reading describes the host. |
| 10 | Sharing memory between processes | `memfd`, anonymous and cleaned up on crash | named segments that leak on crash | named segments | Linux can hand over a nameless block of memory across a socket. |
| 11 | Zero copy GPU handoff | CUDA IPC works | Unproven on consumer cards | Not applicable | Everywhere except Linux, a tensor crossing a process boundary is copied through host memory. |

</div>

Rows 6 and 7 cause the most confusion in practice, because both fail silently.
Nothing raises an error. The machine simply gets slow.

## What ComfyUI does about them

### It reserves more VRAM on Windows

`EXTRA_RESERVED_VRAM` is 400 MB by default, 600 MB on Windows, and 700 MB on a
Windows machine with more than 15 GB of VRAM. The comment in the source names the
reason: *"Windows is higher because of the shared vram issue"*, which is row 6.

So the floor below which ComfyUI will not fill the card is 1.2 GB on Linux and
macOS, 1.4 GB on Windows, and 1.5 GB on Windows with a large card. `--reserve-vram`
overrides all of it.

### It computes the pinned memory budget differently

```python
if WINDOWS:
    MAX_PINNED_MEMORY = ram * 0.40   # Windows limit is apparently 50%
else:
    MAX_PINNED_MEMORY = max(ram * 0.40,
                            min(ram * 0.90, ram - 4 GiB,
                                ram + get_disk_swap_total() - 16 GiB))
```

Windows gets a flat 40% of RAM because the OS itself caps locked pages near half.
Everywhere else the budget may reach 90%, and it may count swap, on the reasoning
that the rest of the system has somewhere to spill.

`get_disk_swap_total()` reads `/proc/swaps` and returns zero if that file is
missing, so it is Linux only by construction rather than by an OS check. It also
explicitly skips zram devices, because compressed RAM is not real backing store
and must not inflate the budget.

### It decides when to evict pins differently

On Linux and macOS, any shortfall at all triggers pin eviction. On Windows a
shortfall is not enough:

```python
if not WINDOWS:
    return True
if psutil.virtual_memory().available < 512 MB:
    return True
return psutil.swap_memory().percent >= 5.0
```

Windows reports low available memory as a normal steady state, so the shortfall
signal alone would evict pins constantly. Pagefile usage is used as the real
distress signal instead. If reading swap usage raises, which it can on Windows,
the code falls back to the Linux behaviour rather than to doing nothing.

### On macOS it mostly does not apply

macOS reaches almost none of this machinery:

* No pinned memory. The budget is gated on the GPU being NVIDIA or AMD, so it
  stays disabled and there is no pin eviction and no `--fast-disk` path.
* No aimdo. The library supports Windows and Linux only, and logs so at startup,
  which means the entire default memory manager is unavailable and macOS always
  takes the legacy path.
* No asynchronous transfers. `device_supports_non_blocking` returns false for
  MPS, so every host to device copy is synchronous.
* No fp8 weights, so a model occupies twice the memory of the same checkpoint on
  a CUDA machine.
* `synchronize()` has no MPS branch and silently does nothing.
* On macOS 14.5 and later, attention is forced to fp32 because of a rendering
  bug, which doubles the attention working set.

Free and total memory on MPS both answer from `psutil`, so "VRAM" and "system
RAM" are the same number, which is row 8 stated in code.

### Accelerators other than NVIDIA

Pinning exists for any device that reads host memory by itself, because the
requirement comes from how that reading works rather than from a vendor. AMD and Intel both have the
equivalent call. ComfyUI enables its pinned budget for NVIDIA and AMD, and leaves
Intel XPU out of it. XPU is also excluded from non blocking transfers, with the
comment that it is *"slower on iGPUs for some reason"*.

The genuine exception is unified memory. On Apple Silicon nothing crosses a bus,
so there is no staging copy to avoid and no reason to pin.

## What comfy-env does about them

### Zero copy is Linux only

CUDA IPC is gated on `sys.platform == "linux"` on both sides of the boundary, and
even on Linux it must pass a live test first, because the two ends often run
different allocators. Everywhere else a GPU tensor crossing the process boundary
is copied device to host, into shared memory, then back again.

### Shared memory works differently

On Linux comfy-env creates an anonymous `memfd`, passes the file descriptor over
the socket, and reads it back through `/proc`. There is no filesystem object, no
name to collide, and nothing to clean up after a crash. On Windows and macOS it
falls back to named segments, which leak if the process dies.

The transport differs too. Linux binds an abstract namespace socket with no
filesystem entry. macOS uses a filesystem socket carrying the pid, so a startup
reaper can tell a live instance from one a crash left behind. Windows has no unix
sockets and uses TCP on loopback, which also means the peer credential check
Linux performs is unavailable there.

### It corrects for row 7 by hand

This is the largest piece of platform specific work in the project. Because
`mem_get_info` reports the calling process's budget rather than the device total
on Windows, comfy-env measures true device free through NVML, falls back to
`nvidia-smi`, and finally to its own ledger. It then adds the difference to every
eviction request, because otherwise ComfyUI computes a negative shortfall and
frees nothing.

The measurement in its source is worth quoting, since it is the clearest
statement of row 7 anywhere: a sibling process allocated 13.0 GiB, `nvidia-smi`
free fell by 13,443 MB, and the parent's own `mem_get_info` fell by **75 MB**.

The worker performs the mirror image correction on itself, and the comment there
names the symptom: without it the worker would size itself against a card it
believes is empty and overload into driver managed system memory, which is *"the
unexplained 10x slowdowns"*.

## What neither of them does

**Neither reads a cgroup limit.** A full search of both repositories for
`cgroup`, `memory.max` and `memory.limit_in_bytes` returns nothing. Every memory
reading in both projects comes from `psutil`, which reports the host.

Inside a container with a memory limit this means the pinned budget is computed
against memory the process cannot use, the cache eviction thresholds never fire,
and the container is killed before any of the careful arithmetic above gets a
chance to act. Row 9 is not mitigated anywhere.

**WSL is treated as Linux.** There is a helper that detects it, and nothing calls
it. So WSL takes the Linux branch of every decision on this page, including the
swap aware pin budget and unconditional pin eviction, while running on the
Windows display driver underneath.
