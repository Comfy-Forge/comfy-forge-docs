# Working around OS differences

*The exceptions to [How operating systems manage memory](os-memory.md), and what
ComfyUI and comfy-env actually do about each one.*

*Last verified against ComfyUI `b133e483` (2026-08-26) and comfy-env `bda45b7`.*

The general form of these differences, including how the graphics driver sits
differently on each system and whether AMD, Intel and Apple use different
concepts, is [Kernel and driver differences](kernel-differences.md).

This page is the other half: what ComfyUI and comfy-env actually **do** about
each one. Every row below is something the code branches on.

## The eleven differences

The table is on [Kernel and driver differences](kernel-differences.md).
Rows 6 and 7 (who owns GPU memory, and whether per process GPU memory is
visible) cause the most confusion in practice, because both fail silently.
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

Zero copy GPU transfer is Linux only and shared memory differs per platform;
both are on [the process boundary](process-boundary.md). The correction for
row 7, measuring true device free through NVML and adding the difference to
every eviction request, is on [admission and the reserve](admission.md), and
the measurement behind it is [why Windows needs its own branch](windows-blind-spot.md).

## What neither of them does

**ComfyUI reads a cgroup limit. comfy-env does not.** This section used to say
neither did, and that a search for `cgroup`, `memory.max` and
`memory.limit_in_bytes` returned nothing across both repositories. That stopped
being true on 2026-08-27, when ComfyUI merged `comfy/system_memory.py`, which
reads all three and clamps total and available RAM to the container's limit.
Everything downstream now honours it: the pin ceiling, the pin budget floor,
the Windows swap gate, CPU free memory, and both cache eviction targets.

comfy-env's own memory readings still come from `psutil`, which reports the
host, so inside a container the two sides disagree; that is
[row 8](upstream-ask.md#row-8) of the memory table.

**WSL is treated as Linux.** There is a helper that detects it, and nothing calls
it. So WSL takes the Linux branch of every decision on this page, including the
swap aware pin budget and unconditional pin eviction, while running on the
Windows display driver underneath.
