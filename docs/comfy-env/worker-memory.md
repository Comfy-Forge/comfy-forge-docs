# Inside a worker

*A worker is a real ComfyUI process running the same memory manager on its
own models. What comfy-env adds inside it, and the one thing it takes away.*
{: .subtitle }

This page assumes [what survives isolation](memory-approach.md), which
assumes [ComfyUI's memory management](comfyui-memory.md) and
[how aimdo manages weights](comfyui-aimdo.md).

## The pager follows the host

A worker never runs `main.py`, and inside ComfyUI `aimdo_enabled` is set in
exactly one place, `main.py`, defaulting to `False`. Left alone every worker
would resolve to the legacy ledger. comfy-env closes that gap at worker
start: `maybe_enable_aimdo` (`memory_manager.py`) reads
`COMFY_ENV_WORKER_AIMDO`, which the parent exports as `1` exactly when its
own `aimdo_enabled` is True, and initialises the pager with the host's
headroom and pressure policy. A host on the ledger, for any reason, puts
every worker on the ledger too. A pack's `[env_vars]` or the operator's
shell can set the variable either way and outranks the host. A comfy-aimdo
version difference is reported and proceeds: compatibility is judged on the
protocol level the wheel supports, read from `control.init`'s signature and
`init_devices`' source, never on the version string, because comfy-aimdo
ships about three releases a month.

The wheel is there because comfy-env puts it there. The host's ComfyUI
imports `comfy_aimdo` unguarded, so a worker without it cannot import
`comfy.model_management` at all, and the same is true of `comfy-kitchen`.
Both are injected into every worker manifest at the host's own pin, whether
or not the pack asked; comfy-aimdo is skipped on CPU stacks.

Three consequences. The eviction bridge is [the stand-in model](stand-in-model.md),
answering `is_dynamic()` False so upstream's dynamic bypass does not skip it.
Evicting a host model from a worker's ask is expensive: the request takes
`for_dynamic=False`, which hard unloads paged host models, and an eviction
sets that model's watermark so it stays partially offloaded until its next
`prioritize()`. And memory pinned in the parent is memory the pager cannot
reclaim: comfy-env's IPC retention caches hold caching allocator tensors in
the host, so pressure lands on host weights, visible as a slow model rather
than an error.

!!! warning "Decided per pack, not per install"
    `wrap.py` falls back to an in process import in five cases (no ComfyUI
    base, no `comfy-env.toml`, a stamp refusal, an unmaterialised env, main
    process directories with no config). One run can execute pack A under
    the ledger and pack B under the pager on the same device, because A's
    environment was built and B's was not. comfy-env prints a WARNING once
    per environment when a worker resolved differently from the host, with
    the worker's own reason.

A CPU worker is on the ledger and that is correct: aimdo has no CPU path,
`ModelPatcherDynamic._vbar_get` returns `None` for a CPU load device. That
is the one host versus worker difference that is a fact rather than an
accident, and why comfy-env cannot treat the pager as the only path.

## The node boundary

Inside ComfyUI `reset_cast_buffers` has one caller, `execution.py`, and a
worker does not run ComfyUI's executor. comfy-env mirrors the release at its
own boundary: `release_node_boundary` runs in a `finally` around every
worker call whenever the pager is live in that worker. A worker on the
ledger gets a coarser version: `cast_epoch_boundary` runs at the start of
every request and resets the cast buffers whenever the prompt epoch has
changed, so they ratchet to `NUM_STREAMS` times the largest weight cast
within one prompt and are released before the next prompt's first node,
rather than held for the worker's life (measured before it existed: 2 x
512 MiB held through unloads and four small model nodes).

One thing makes the worker's cast path unlike the host's. `cuda_malloc.py`
sets `args.cuda_malloc` True in the host and `ops.py` skips the torch cast
buffer under that flag. A worker parses an empty argv, never imports
`cuda_malloc.py`, and the flag is not mirrored, so the worker takes the cast
buffer path a default host never does. That is why the ratchet above is a
worker problem at all.

## The release ladders

Three commands the host can send a worker, all handled in
`memory_manager.py`: `full_release` (everything: models to CPU, cast
buffers, pins, the allocator cache; the receipt it returns is what lets the
published reserve shrink), `partial_release` (a shortfall in bytes, planned
per worker by `state_sync.plan_pressure_release`), and `release_pins`. The
host sends `full_release` from the idle sweep described on the
[worker lifecycle](worker-lifecycle.md) page, after a worker has sat idle
past `IDLE_RELEASE_SECONDS`, or early when the card is tight and the
worker's last prompt is over.

The pin census is live and the lever is not. Each worker reports
`TOTAL_PINNED_MEMORY` and `MAX_PINNED_MEMORY` into the census the host
ingests, and wraps `free_model_pins` to count bytes evicted per victim (the
wrapper calls the original and changes no decision). But
`broadcast_pin_release` has no caller: comfy-env can see exactly how much
each worker has pinned and cannot make a worker let go.

## Pinned memory is per process

`MAX_PINNED_MEMORY` is computed at import from total system RAM: 40 percent
on Windows, up to 90 percent elsewhere. Every process that imports
`comfy.model_management` computes its own and none knows about the others,
so N workers plus the host promise N plus one times that fraction of one
machine's RAM. The floors differ too: the host's pin eviction floor is
`max(RAM_CACHE_HEADROOM / 2, 2 GiB)` with the headroom set by its executor
per prompt, while a worker runs no executor, so its headroom stays 0 and its
floor is exactly 2 GiB. Unrelated to the pager and true today.

## What comfy-env writes in a worker

Seven assignments. Six are inside the worker, on the worker's own copy of
the module; the `EXTRA_RESERVED_VRAM` write is the only one that happens in
the host process, and it is a value written into a knob `--reserve-vram`
already writes ([admission](admission.md)).

| Name | Why |
|---|---|
| `EXTRA_RESERVED_VRAM` | the host adds what workers hold, so its own loader backs off; the worker receives the same value so its view stops being a lie |
| `vram_state` | forced to match the parent's mode |
| `load_models_gpu` | wrapped, so a worker load negotiates a budget with the parent before it happens |
| `aimdo_enabled` | set when the worker brings the pager up, so upstream's own aimdo branches take the right path |
| `free_model_pins` | wrapped for per victim eviction counting; the wrapper calls the original and changes no decision |
| `comfy.model_patcher.CoreModelPatcher` | set to `ModelPatcherDynamic` when the pager comes up, the same assignment `main.py` makes in the host |
| `comfy.cli_args.args.<flag>` | every mirrored host flag is `setattr` onto the worker's args object (`mirrored_args.apply_host_args`), since a worker parses an empty argv and would otherwise resolve every dtype and memory flag to its default |

`mirrored_args.py` decides which host CLI flags reach a worker, which is what
decides a worker's dtype, so it is the answer to "why is my worker fp16 when
my host is fp8". The three vram flags are deliberately not mirrored;
`vram_state` crosses per call on the budget request instead.

## The allocator backend

ComfyUI turns on PyTorch's stream ordered allocator (`cudaMallocAsync`) at
startup, and a worker inherits it through the `PYTORCH_CUDA_ALLOC_CONF` the
host exported. It is only a default: a pack's `[env_vars]` commonly sets its
own (`expandable_segments:True` is the usual one), and that wins in the
worker, so the two ends of one call routinely run different allocators.

Two things follow. Handles from the async backend will not export, so a GPU
tensor crossing the boundary is copied through host memory rather than
shared; the mechanism that would make it zero copy, and the driver bug in
the way, are on the [zero-copy CUDA transfer](zero-copy-ipc.md) page. And
torch's own counters (`memory_allocated`, `memory_reserved`) are blind to
weights the pager holds, because those are CUDA virtual memory allocations
that were never in the caching allocator; `mem_get_info` and NVML see them
1:1. Any dashboard rooted in `memory_reserved()` reads 0 for a 12 GB paged
model, which is why the worker's census reports `max(aimdo, torch)`.

## The rows

### 10. Cast buffers { #row-10 }

**What ComfyUI does.** Cast buffers. Per offload stream VRAM scratch, sized to the largest weight cast so far, plus an aimdo `VRAMBuffer` reserving 16 GiB of device address space per stream and committing in 16 MiB chunks. Allocated on the fault path, released only by `reset_cast_buffers` at the node boundary.

**Today.** <span class="v v-partial">partial</span>: every process pays for its own and they are never shared. comfy-env books what the INCOMING load will want (`num_streams` times its largest tensor) into the admission ask, since those bytes exist neither in NVML nor in any measured field at admission time. What it cannot do is see or reclaim a sibling's. `torch.cuda.memory_reserved` sees the torch buffer and not the aimdo one, which is why the census takes `max(aimdo, torch)`

**With the upstream hook.** <span class="v v-partial">partial</span>: needs a per device budget rather than a per process one

### 11. Partial load budget (`lowvram_model_memory`) { #row-11 }

**What ComfyUI does.** Partial load budget (`lowvram_model_memory`): load only as much of a model as fits after the reserve and keep the rest in RAM. The pager ignores this and decides page by page.

**Today.** <span class="v v-no">no</span>, on both paths, for two unrelated reasons. On the legacy path the plumbing is complete and never invoked: the stand-in implements `partially_load`, the worker performs it, and nothing calls it. `partially_load` is reached only through `LoadedModel.model_use_more_vram`, whose callers are `model_load` and `use_more_memory`; the second is dead (one line in the tree, its own `def`) and the first runs only from `load_models_gpu`, which comfy-env deliberately never uses, inserting its entries by hand instead. The one route left was a node handing the stand-in back through row 38's leak, and that closed on 2026-09-06. Under aimdo the budget dies further downstream and would die anyway: it travels host to stand-in to IPC to the worker's real patcher, and `ModelPatcherDynamic.partially_load` accepts `extra_memory` and never passes it on (`mp.py`), calling `self.load(device_to)` and letting the pager decide residency page by page at fault time. So the budget is not a lever on either path; the lever is the pager's headroom, which is why row 13 exists. One thing runs the other way: upstream returns `None` from that method, with a comment saying it has no number to give, while the stand-in still returns a measured one, because the worker reads residency before and after rather than trusting the return

**With the upstream hook.** <span class="v v-yes">yes</span>

### 15. The inactive cache tier { #row-15 }

**What ComfyUI does.** The INACTIVE cache tier, drained at every node. A second headroom (`ram_inactive`, default all of RAM capped at 128 GB) is passed to `ram_release` after every node. Because available RAM is essentially never above that target, the previous workflow's cached outputs are evicted unconditionally, with no pressure test at all.

**Today.** <span class="v v-yes">yes</span> for outputs, in the sense that it happens to host held worker results like any other cache entry. Nothing coordinates it with a worker

**With the upstream hook.** <span class="v v-partial">partial</span>

### 16. Per-layer fault and aimdo's C-side eviction { #row-16 }

**What ComfyUI does.** Per-layer fault and aimdo's C-side eviction: each layer is fetched onto the card when needed and the pager decides for itself what to drop, from device-wide pressure. Torch never sees these pages.

**Today.** <span class="v v-yes">yes</span>, with no coordination and none possible from Python. The pager's pressure is `MAX` of a per process term and a polled device term, and only the second is cross process. The two platforms are not the same mechanism. On Linux there is no chooser: the NVML branch is inside a `#if defined(_WIN32)` block, so `cuMemGetInfo` is the only source, `set_nvml_pressure` is dead code and ComfyUI's `--disable-nvml-pressure` does nothing there. Measured on the 3090: a sibling taking 4 GiB moved the pager's reading by 4400 MiB, the tensor plus the sibling's own context, fully visible. On Windows the poll is instead the LARGER of two deficits, a DXGI WDDM budget term and a device free term whose source is NVML when its handle initialized and `cuMemGetInfo` otherwise (`src-win/shmem-detect.c`), and only the NVML spelling of the second term is cross process. Measured rather than read out of the C (2026-09-06, RTX 4060 Ti, WDDM): with a sibling holding 8 GiB, the NVML reading fell 8311 MiB and tracked `nvidia-smi` to within 1 MiB, while the SAME process's `cuMemGetInfo` moved zero; with a 4 GiB sibling the DXGI budget line was byte identical to its idle value, so the WDDM budget path is exactly as blind as the CUDA one. The library defaults `nvml_pressure` to False and ComfyUI passes it True, so the pager sees the card because ComfyUI asks it to, not by nature. Run again with NVML disabled, aimdo's reading was byte identical to `cuMemGetInfo` and also moved zero, so the flag is the whole difference. It is a default, not a guarantee: `--disable-nvml-pressure` or a failed NVML init drops it back to the blind figure, and there is no log line when the flag turns it off, only the absence of one. Two limits the row never stated, both measured on Linux. The reactive term regulates to a hardcoded 256 MiB `VRAM_HEADROOM`, not to the headroom comfy-env forwards, so a sibling can take 10.7 GiB and the pager will not react at all provided it leaves 300 MiB on the table. And the pager yields only AT A FAULT: a sibling held the card at 4 MiB free for three seconds while aimdo sat on 12,288 MiB and did nothing. The poll is also cached for 2 s, so the reading can be that stale. In the legacy cells nothing polls at all: with no `init_devices` there is no pager to read anything

**With the upstream hook.** <span class="v v-partial">partial</span>: needs a cross-process priority signal nobody has proposed

### 18. OOM branch in `execution.py` { #row-18 }

**What ComfyUI does.** OOM branch in `execution.py`: on out-of-memory the host logs a summary, clears every model and stops the run. No retry AT THIS LEVEL, which is not the same as no retry: eleven sites below it catch the same exception and shrink their own work first, and this branch is only reached when all eleven have given up. See row 19.

**Today.** <span class="v v-yes">yes</span>: a worker OOM crosses as the real class (the worker stamps it from ComfyUI's own `is_oom`, the host rebuilds `torch.cuda.OutOfMemoryError`) so the branch fires, and it calls `unload_all_models`, so worker models are freed by the same path as row 9

**With the upstream hook.** <span class="v v-partial">partial</span>: only a holder adds its own line to the summary

### 23. Async offload streams { #row-23 }

**What ComfyUI does.** Async offload streams. `NUM_STREAMS` defaults to 2, set by `--async-offload N` or disabled by `--disable-async-offload`. Each stream gets its own cast buffer.

**Today.** <span class="v v-yes">yes</span>: mirrored to workers as a resolved value, and read LIVE rather than from the mirror when booking cast buffers, because it must be the number the cast path will actually use. It is the multiplier on the cast buffer row

**With the upstream hook.** <span class="v v-yes">yes</span>

### 24. `cudaMallocAsync` as the default allocator { #row-24 }

**What ComfyUI does.** `cudaMallocAsync` as the default allocator (`cuda_malloc.py`), unless `--disable-cuda-malloc`.

**Today.** <span class="v v-yes">yes</span> to read, and it is why a GPU tensor crossing the process boundary is copied rather than shared: the async allocator's pool is not IPC exportable, so comfy-env's zero copy CUDA IPC tier is unavailable under it and the transport falls to a shared memory copy. It also changes what `empty_cache` gives back

**With the upstream hook.** n/a

### 27. Dirty mmap bounce { #row-27 }

**What ComfyUI does.** Dirty mmap bounce. `mark_mmap_dirty` records mmapped storages written through during a cast, and `reset_cast_buffers` bounces them at the node boundary, which is what stops a dirtied private page from being charged to this process forever. `--mmap-torch-files` / `--disable-mmap`.

**Today.** <span class="v v-yes">yes</span>: mirrored, and this is the mechanism that makes shared page cache real rather than aspirational. Two workers loading the same checkpoint share its pages only while nothing dirties them

**With the upstream hook.** <span class="v v-yes">yes</span>

### 35. `--disable-smart-memory` { #row-35 }

**What ComfyUI does.** `--disable-smart-memory`: forget every model after each run; every eviction ask becomes 1e32.

**Today.** <span class="v v-yes">yes</span>: the flag is read, and the prompt-end unload is not invisible either. `execution.py` calls `unload_all_models` when it is set, which walks the list and reaches the stand-in exactly as in row 9, so the worker forgets its model after every run, which is what the flag means

**With the upstream hook.** <span class="v v-yes">yes</span>

### 36. Node output cache and RAM-pressure release { #row-36 }

**What ComfyUI does.** Node output cache and RAM-pressure release: the host keeps every step's results and drops the oldest and biggest when RAM runs low. Worker results are in that pile.

**Today.** <span class="v v-yes">yes</span> for outputs, with upstream's own bug attached: the release pops the *largest* tuple, so on a score tie it evicts the most recently touched entry rather than the oldest, contradicting the comment above it

**With the upstream hook.** <span class="v v-partial">partial</span>

### 37. Allocator cache release (`soft_empty_cache`) { #row-37 }

**What ComfyUI does.** Allocator cache release (`soft_empty_cache`): hand the driver back the memory torch kept in its pocket, after an eviction, after a run, before a retry.

**Today.** <span class="v v-partial">partial</span>: not every call reaches it. `free_memory` runs `soft_empty_cache` only if something was actually unloaded, or if torch's idle cache exceeds a quarter of free and `vram_state` is not `HIGH_VRAM`. A pass that evicts nothing under `--highvram` never gets there. And none of it crosses the boundary in any case: `torch.cuda.empty_cache()` is per process, so the host calling it releases nothing a worker's allocator holds. comfy-env empties the worker's on its own schedule, which is not ComfyUI's. No entry reads

**With the upstream hook.** <span class="v v-yes">yes</span>

### 42. `MAX_PINNED_MEMORY` and hostbuf ceilings { #row-42 }

**What ComfyUI does.** `MAX_PINNED_MEMORY` and hostbuf ceilings: every process assumes it may lock most of the machine's RAM, so N processes promise N times the RAM.

**Today.** <span class="v v-partial">partial</span>: mirrored, and it binds by default on one path out of six. `ensure_pin_registerable` is CALLED on every pin, but its verdict is only honoured in `pinned_memory.py`, where a False falls through to `_steal_pin`. The other four call sites discard the return and pin regardless (`mm.py`, `mm.py`, `pinned_memory.py`, `model_prefetch.py`), so there it is advisory. `mm.py` is its sibling `ensure_pin_budget`, a different function, and its verdict is discarded at that site too. `pinned_hostbuf_size` binds by default, capping each dynamic model's host buffer at twice `MAX_PINNED_MEMORY`; under `--high-ram` it returns twice the model size with no cap at all. What `--fast-disk` changes is the OTHER test, row 34's: it swaps that test's machine wide available RAM floor for this same per process ceiling. The share is not "most" everywhere either: 40 percent on Windows against up to 90 elsewhere, so two workers on Windows already overcommit the pool

**With the upstream hook.** <span class="v v-partial">partial</span>: needs a coordinator

## See also

- [Worker lifecycle](worker-lifecycle.md), the idle sweep and the reaper
- [Admission and the reserve](admission.md), the host side of the budget round trip
- [How aimdo manages weights](comfyui-aimdo.md), the pager a worker runs
