# How aimdo manages weights

*The demand-paging VRAM allocator that ComfyUI uses by default, and the seam
where ComfyUI hands it control.*

*Last verified 2026-09-07 against ComfyUI `15eb748` (2026-09-06), which pins
`comfy-aimdo==0.5.2`, and against comfy-aimdo's own C source at that tag.*

Read [ComfyUI memory management background](comfyui-memory.md) first. This page
covers the manager that page defers to.

## What it is

`comfy-aimdo`, "AI Model Dynamic Offloader", is a pinned dependency of
ComfyUI (`requirements.txt`), GPLv3, source at
[Comfy-Org/comfy-aimdo](https://github.com/Comfy-Org/comfy-aimdo). It is a
PyTorch VRAM allocator that offloads model weights on demand when the primary
allocator comes under pressure.

aimdo's README claims NVIDIA only. ComfyUI enables it on NVIDIA, and on AMD
from ROCm 7.14, and the wheel ships `aimdo_rocm.so` beside `aimdo.so`.
PyTorch 2.8+, CUDA 12.8+, Windows 11 and Linux.

!!! note "What this page is read from"
    The wheel ships **seven readable Python modules**, `control.py`,
    `model_vbar.py`, `host_buffer.py`, `vram_buffer.py`, `model_mmap.py`,
    `torch.py`, plus `aimdo.so` and `aimdo_rocm.so`.

    Most of this page comes from those shims, from ComfyUI's own call sites,
    or from aimdo's README, **quoted and attributed**. The pressure-sensing
    section is different: it is read from aimdo's **C source**
    (`src/control.c`, `src-win/shmem-detect.c`, `src-cuda/dispatch.c`), which
    is public in the same repository, and cites it by file and symbol. An
    earlier version of this page reasoned about the compiled object from the
    outside and got that section wrong. Where aimdo's behaviour and this page
    disagree, aimdo is right.

## The mechanism

The README states the contract directly:

> The pytorch application creates a Virtual Base Address Register (**VBAR**)
> for a model. Creating a VBAR doesn't cost any VRAM, only GPU virtual address
> space (which is pretty much free).
>
> The pytorch application allocates tensors for model weights within the VBAR.
> These tensors are initially un-allocated and **will segfault if touched**.
>
> The pytorch application faults in the tensors using the `fault()` API at the
> time the tensor is needed. This is where VRAM actually gets allocated.

So a "loaded" model, on this path, is mostly a promise. The address space
exists; the pages behind it appear when a layer actually runs.

!!! warning "These words do not mean what they mean on the background page"
    Two terms collide, and the collision is unfortunate.

    | Term | On [the OS page](os-memory.md) | Here |
    |---|---|---|
    | **fault** | the processor traps because you touched memory that was not mapped, and the kernel resolves it | an API call the application makes on purpose, asking for VRAM to be committed |
    | **pin** / **unpin** | locking host pages so the kernel may not swap or move them | marking device memory as currently in use, or releasing it to be reclaimed |

    So a page fault happens *to* a program, while an aimdo fault is something a
    program *does*. And pinning here is about VRAM residency, not about host
    pages being locked down.

    The costs are nothing alike either. Locking host pages is slow, roughly
    1.7 GiB/s measured on this machine. aimdo's commit and release are device
    side virtual memory calls and are effectively instantaneous, on the order of
    tens of microseconds for half a gigabyte. Committing VRAM is cheap;
    page locking host RAM is not.

### The fault cycle

Per weight, per forward pass:

1. `fault(alloc, size)`, commit VRAM for this weight.
2. If the returned **signature** changed or is unknown, copy the weight data in
   and remember the signature. If it is unchanged, the data is already there.
3. The layer uses the tensor.
4. `unpin()`, mark it reclaimable again.

A failed `fault()` is **not an error**. The README:

> The application allocates a temporary regular GPU tensor… uses `_copy` to
> populate weight data on the GPU… the layer uses the temporary as the weight…
> Pytorch garbage collects the temp when the layer is finished.

An offloaded model still runs. It runs slower, streaming weights per layer,
which is the behaviour that used to require `--lowvram`.

### Priority is address order, not recency

This is the part that is genuinely unlike the legacy ledger:

> The most recent VBARs are the highest priority and lower addresses in the
> VBAR take priority over higher addresses. Applications should order their
> tensor allocations in the VBAR in load-priority order with the lowest
> addresses for the highest priority weights.

So eviction order is a property of *where a weight was placed at allocation
time*, decided by the application, not of when it was last used. ComfyUI lays
out a model's weights in the order it will need them.

### Watermarks stop the thrash

> Having a weight evicted sets that VBAR's watermark to that weight's level.
> Any weights in the same VBAR above the watermark automatically fail the
> `fault()` API. This avoids constantly faulting in all weights each model
> iteration while allowing the application to just blindly call `fault()` every
> layer and check the results. **There is no need for the application to manage
> any VRAM quotas or watermarks.**

That last sentence is the design's whole thesis: the application stops doing
admission control and simply asks, every time.

`prioritize()` pushes an existing VBAR back to top priority and resets its
watermark, which is how using the same model twice in one workflow avoids
re-streaming it.

### The backend

> VBAR allocation is done with `cuMemAddressReserve()`, faulting with
> `cuMemCreate()` and `cuMemMap()`… For consistency with VBAR memory
> management, main pytorch allocator plugin is also implemented with
> `cuMemAddressReserve` -> `cuMemCreate` -> `cuMemMap`.

Weights are **CUDA virtual-memory allocations, not `cudaMalloc`**. That is the
single most consequential fact on this page, see [what it means for
measurement](#what-tools-can-and-cannot-see) below.

!!! note "aimdo does *not* replace torch's allocator in ComfyUI as shipped"
    The README describes a pluggable allocator mode, and aimdo implements it, but
    `get_torch_allocator()` has **zero callers** in ComfyUI, and aimdo's own
    code logs *"Aimdo+CUDAPluggableAllocator is experimental and unsupported"*
    with a comment explaining that torch MemPools prevent the garbage collection
    a high-pressure allocator needs. Torch keeps `cudaMallocAsync`; aimdo runs
    its VMM reservations alongside it.

## The seam

ComfyUI hands decisions across to aimdo in about twenty-five places. The seam
is in-tree and greppable even though the module is not:

| Where | What crosses |
|---|---|
| `main.py` | init per device, `--vram-headroom`, pressure mode, swapping in `ModelPatcherDynamic` |
| `model_patcher.py` | `ModelVBAR(model_size() * 10, device)` per model; `get_free_memory` adds `vbars_analyze()` |
| `ops.py` | the fault/unpin cycle around each weight use |
| `execution.py` | `cleanup_prefetch_queues()` and cast-buffer reset, per node |
| `model_management.py` | cast buffers, pinned host buffers, offload device selection |
| `memory_management.py` | `read_file_to_device()`, the file to VRAM path |

### The one number ComfyUI reads back

`vbars_analyze(device)` returns how much aimdo could reclaim if asked.
`ModelPatcher.get_free_memory` adds it to the driver's free number:

```python
return comfy.model_management.get_free_memory(device) + aimdo_mem
```

That is "free, plus what I could get by evicting", deliberately, so batching
decisions prefer a bigger batch over keeping weights resident. It is also the
reason two functions named "get free memory" return different numbers for the
same device in the same step.

## What tools can and cannot see

Measured on an RTX 3090 by reproducing aimdo's exact allocation sequence
(`cuMemAddressReserve` → `cuMemCreate` → `cuMemMap`) and watching every counter:

| After committing 1 GiB of VBAR | change |
|---|---|
| `torch.cuda.mem_get_info()` free | **−1024 MB** |
| NVML device used | **+1024 MB** |
| `torch.cuda.memory_allocated()` | **unchanged** |
| `torch.cuda.memory_reserved()` | **unchanged** |
| after `empty_cache()` | **unchanged** |

!!! danger "Torch's own counters are blind to model weights on this path"
    A 1 GiB VMM tensor that torch is actively holding as a `torch.Tensor` moves
    none of `memory_allocated`, `memory_reserved`, or `memory_stats()`. This is
    the documented contract, those track *the caching allocator*, and aimdo's
    weights were never in it.

    Any dashboard, log line or admission check rooted in `memory_reserved()`
    will read **0 MB** for a 12 GB resident model. `mem_get_info` and NVML see
    it 1:1 and are the numbers to trust.

`empty_cache()` also cannot touch VBAR pages, ever, they are not allocator
segments. That is separate from, and additional to, the fact that
`empty_cache()` returns approximately nothing when live tensors pin segments.

## How it senses pressure

**The poll is a different function on each platform, and NVML exists on
exactly one of them.** The whole NVML block in `src-cuda/dispatch.c` -- the
`nvml.dll` load, `nvmlDeviceGetHandleByUUID`, `nvmlDeviceGetMemoryInfo` -- is
inside `#if defined(_WIN32) || defined(_WIN64)`. On Linux it is not compiled
at all, which is why `aimdo.so` links only `libdl`/`libpthread`/`libc` and
`dlopen`s only `libcuda`. That observation was right; the conclusion drawn
from it -- that aimdo does not use NVML -- was wrong for the platform where
it matters.

**Linux** (`src/control.c`): one source. `cuMemGetInfo`, giving
`deficit = VRAM_HEADROOM - free`, and the debug log reports `prevailing
method cuMemGetInfo`. (An integrated-GPU branch above it reads
`/proc/meminfo` `MemAvailable` instead and reports `prevailing method
/proc/meminfo (integrated RAM)`.)

**Windows** (`src-win/shmem-detect.c`): the larger of **two** deficits,
recomputed at most every 2 s.

| Term | Source | Deficit |
|---|---|---|
| WDDM budget | `IDXGIAdapter3::QueryVideoMemoryInfo`, `DXGI_MEMORY_SEGMENT_GROUP_LOCAL` | `aimdo's own recorded usage + WDDM_BUDGET_HEADROOM - Budget` |
| device free | NVML if the device handle initialized, else `cuMemGetInfo` | `headroom - free`, with `headroom` = `NVML_BUDGET_HEADROOM` or `CUDA_BUDGET_HEADROOM / 2` |

The second term wins only if it is larger, and the debug log names which
did: `WDDM budget`, `NVML (Windows)`, or `cuMemGetInfo (Windows)`.

!!! info "What this means for seeing another process"
    On **Linux**, `cuMemGetInfo` is device-wide, so the parent's aimdo
    genuinely sees another process's VRAM and will shed its own weights under
    that pressure. That is what makes `--vram-headroom`'s *"even counting VRAM
    from other apps"* true.

    On **Windows/WDDM**, `cuMemGetInfo` is per-process --
    [measured](windows-blind-spot.md): a sibling holding 10 GiB moved the
    observing process's reading by 0 MiB. But NVML is **not** per-process, and
    on Windows aimdo reads it directly (`LoadLibraryExW("nvml.dll")`, its own
    handle, no `pynvml` dependency). So when the NVML handle initializes,
    aimdo's Windows poll does see a sibling process, through the NVML term and
    through DXGI's adapter-wide `Budget`.

    `--disable-nvml-pressure` turns the NVML term off, leaving WDDM budget and
    a per-process `cuMemGetInfo`. That flag is therefore Windows-only in
    effect; on Linux there is no NVML term for it to disable.

## Reading weights from the file

`--fast-disk` prefers re-reading a weight from the checkpoint over keeping it in
ordinary host memory. Its help text says *"Prefer disk-backed dynamic loading and
offload over unpinned RAM. Can be faster for users with fast NVME"*.

The implementation is a ring of pinned host buffers. The reader allocates slots
with `cuMemAllocHost` at a fixed window size, `pread`s a window of the file into
a free slot, sends that slot to the card, and retires it so reading and sending
overlap.

!!! warning "It is not GPUDirect Storage, and the bytes do pass through host RAM"
    There is no `cuFile`, no `nvidia-fs` and no `O_DIRECT` in the library. What
    the flag removes is the persistent host copy, not the host entirely: instead
    of a weight occupying ordinary memory between uses, it occupies a small
    reused pinned window for the duration of one transfer.

    The trade is therefore repeated reads against held memory, which is why the
    help text conditions it on fast storage.

## The caveat aimdo states about itself

Quoted in full, because it is the most operationally important paragraph in the
README:

> There is no real way for this allocator to tell the difference between high
> usage and bad fragmentation in the pytorch caching allocator. As we always
> return success to the pytorch caching allocator it experiences no pressure
> while weights are being offloaded which means it can run in an extremely
> fragmented mode. The assumption is model weight access patterns are
> reasonably regular over blocks or iterations and it finds a good set of
> sizes to cache. What you should generally do though, is **completely flush
> the pytorch caching allocator before each new model run**, which avoids
> completely un-used reservations from taking priority over the next models
> weights.

Read that twice if you operate a long-lived ComfyUI process. The failure mode
is not an OOM, it is a slow slide into fragmentation while every layer of
accounting reports success, because torch never feels pressure.

## What this changes for comfy-env

The topology used to be **asymmetric**: workers never execute `main.py`, so a
worker's `control.lib` stayed `None` and its models used the legacy
`ModelPatcher` while the parent paged. comfy-env closes that at worker start:
`maybe_enable_aimdo` initialises aimdo whenever the wheel imports and a CUDA
device is visible, mirroring the parent's headroom and refusing on a PROTOCOL
difference rather than a version difference. Both sides normally page. The
asymmetry that remains is deliberate and narrow: CPU workers, failed init, and
an explicit level below `paged` stay on the ledger, and comfy-env reports
whichever way each worker resolved. See
[comfy-env's memory management](memory-approach.md).

Three consequences, measured against comfy-env `bda45b7` and re-checked at `f1f8260`:

- **The eviction bridge is the stand-in, and it is on.** comfy-env's stand-in
  answers `is_dynamic()` with `False` deliberately, so upstream's
  dynamic-model bypass does not skip it, and the worker's model stays an entry
  upstream can actually evict. This is the only path by which upstream's own
  code takes VRAM from another process, and comfy-env registers one for every
  worker model unconditionally.
- **Evicting a host model here is expensive.** comfy-env's request takes
  `for_dynamic=False`, which hard-unloads aimdo models rather than letting them
  shed pages. An eviction sets that VBAR's watermark, so the host model can stay
  in partial-offload until its next `prioritize()`.
- **Memory pinned in the parent is memory aimdo cannot reclaim.** comfy-env's
  IPC retention caches hold caching-allocator tensors in the aimdo process, so
  all pressure lands on host weights instead, visible as a slow model rather
  than an error.

!!! note "comfy-env initialises aimdo, matches protocols, and reports per worker"
    comfy-env initialises aimdo in each worker, injects the wheel at the
    host's pin, and reports which manager every worker resolved to. It also
    moves aimdo's headroom at runtime: `set_simple_vram_headroom` is live at
    the next page fault, measured, and comfy-env forwards its published
    reserve into it on every publish. An earlier draft of this page said the
    headroom was fixed once devices initialise; that was wrong, and the
    experiment behind it used plain `nn.Linear` modules which never page.
    What is fixed once devices initialise is `init_devices` itself: a second
    call returns `False`, and a second `control.init` segfaults the process.

## Things worth knowing before you debug

- **`--lowvram` is inert here.** Upstream's own help text says so.
- **Hooks are unimplemented.** `ModelPatcherDynamic.patch_hooks` raises
  `RuntimeError("Hooks not implemented in ModelPatcherDynamic")`. Some `--fast`
  arguments will refuse to run.
- **GGUF is the documented reason to turn this off**, and upstream would rather
  you didn't: the deprecation warning recommends keeping dynamic VRAM enabled
  and using native ComfyUI model formats instead.
- **The init protocol has changed twice.** `main.py` carries three call shapes
  behind `TypeError` fallbacks. Treat the interface as unstable and this page as
  perishable.

## How this page goes stale

- aimdo's README is versioned with the wheel. The quotations above are from
  **0.5.2**; re-read it on upgrade rather than trusting this page.
- A fourth init protocol in `main.py` means the seam moved.
- If `aimdo.so` gains a Python-visible interface for residency or priority,
  the remaining hedging on this page can be replaced with measurement.
- The pressure section cites C source by file and symbol, not by line, so a
  refactor moves it without breaking it -- but a change to which terms the
  Windows `max` combines, or to the platform guards around the NVML block,
  invalidates it outright. Those are the two things to re-read.
