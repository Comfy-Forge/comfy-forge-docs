# ComfyUI's memory API

*What ComfyUI offers a caller, what it demands of a model in return, and
comfy-env's relationship to every symbol on that surface.*

*Last verified against ComfyUI `bab6ee5f` (2026-08-24) and comfy-env `f1f8260` (2026-09-04). Every upstream symbol below was re-checked against the tree and all 81 resolve. The comfy-env column was spot-corrected where [ADR-0038](adr/0038-the-memory-floor.md) changed the relationship; rows marked `inherits` were not individually re-verified.*

## There are two contracts, and they point in opposite directions

Almost every discussion of this conflates them, and they fail differently.

**You call ComfyUI.** A node asks where a tensor should live, or asks for a model
to be made resident. This is an ordinary module API and it either works or raises.

**ComfyUI calls you.** Once a model is in the ledger, the memory manager reads
members off it during eviction, without asking permission. This is an implicit
interface with no declaration anywhere, and it fails by `AttributeError` in the
middle of someone else's loop.

comfy-env has to satisfy both, and the second is the hard one: it is the whole subject of [the stand-in model](stand-in-model.md).

## How to read the comfy-env column

| Marking | Meaning |
|---|---|
| **calls** | comfy-env invokes it, in the parent or the worker |
| **patches** | comfy-env replaces it inside the WORKER process. Never in the host: an AST test fails the build if any module that runs in the host process assigns to a comfy module, apart from the one value comfy-env publishes ([ADR-0038](adr/0038-the-memory-floor.md)) |
| **implements** | the model proxy must provide it, because upstream reads it |
| **inherits** | the worker gets upstream's behaviour untouched, and that is correct |
| **watch** | not used today, but a change here would break something |

`comfy/model_management.py` exposes **113 public functions**. Most are dtype and
capability queries rather than memory management. comfy-env calls fifteen things
and patches three.

## Accounting: how much is there

| Function | Returns | comfy-env |
|---|---|---|
| [`get_free_memory(device)`](#get_free_memory) | driver free plus torch's own cache | **calls**, eleven sites, and corrects the answer |
| [`get_total_memory(device)`](#get_total_memory) | device total | **calls** |
| `module_size(module)` | bytes of a state dict, nothing else the module holds | inherits |
| [`minimum_inference_memory()`](#reserve) | the floor that must stay free | **calls**, in the admission sum |
| [`extra_reserved_memory()`](#reserve) | the reserve on top of that floor | **calls**, and comfy-env PUBLISHES into the global behind it so upstream's own arithmetic accounts for worker VRAM |
| `maximum_vram_for_weights(device)` | what is left for weights after reserves | inherits |
| `offloaded_memory(loaded_models, device)` | how much of the ledger is already off the card | inherits |
| `get_disk_swap_total()` | swap size, used to raise the pin ceiling | inherits, Linux only by construction |
| `debug_memory_summary()` | a dump for humans | ignores |

!!! danger "`get_free_memory` is the single most important entry here"
    It is the input to every decision in the system, it counts allocator cache
    that may not be returnable, and in a worker process on Windows it reports
    that process's own budget rather than the device. Correcting it is most of
    what comfy-env does: [admission and the reserve](admission.md).

!!! danger "There are two functions called `get_free_memory`"
    The module one, above, and `ModelPatcher.get_free_memory`, which adds what
    the dynamic manager could reclaim on demand. They return different numbers
    for the same device, and upstream uses both in the same batching decision.
    Which one you want depends on whether you are asking "what is free" or
    "what could I get".

## Loading and eviction

| Function | Does | comfy-env |
|---|---|---|
| [`load_models_gpu(models, memory_required=, ...)`](#load_models_gpu) | budget, evict, load | **patches** in the worker; **calls** the real one after |
| `load_model_gpu(model)` | one model, thin wrapper | inherits |
| [`free_memory(required, device, keep_loaded=, for_dynamic=, pins_required=, ram_required=)`](#free_memory) | "get me this many free bytes" | **calls**, with upstream's own target expression (`reserve.ask_target`), exactly two positionals, never `for_dynamic` |
| [`unload_all_models()`](#unload_all_models) | evict everything, everywhere | **reads**; comfy-env registers no wrap here. The stand-in is reached through it, because `unload_all_models` walks the list and `LoadedModel.model_unload` calls `detach` on every entry |
| [`unload_model_and_clones(model, ...)`](#unload_model_and_clones) | drop one model and its clones for a clean reload | inherits |
| [`loaded_models(only_currently_used=)`](#loaded_models) | the ledger contents | **watch**: it hands the proxy to arbitrary node code |
| `cleanup_models()` | drop dead ledger entries | **calls** |
| `cleanup_models_gc()` | the same, plus a collect when a leak is detected | inherits |
| `use_more_memory(extra, loaded_models, device)` | grow a partially loaded model | inherits |
| `current_loaded_models` (the list itself) | the ledger | **calls**, registers a proxy into it |

!!! warning "`free_memory` takes a parameter nobody passes"
    `ram_required` appears in one log string. No caller in the tree supplies it,
    comfy-env included, so host RAM is unbudgeted on both sides of the boundary.

## Pinned memory

Twelve functions. comfy-env inherits most of it and touches four.

| Function | Does |
|---|---|
| `pin_memory(tensor)` / `unpin_memory(tensor)` | lock or release host pages, in place |
| `ensure_pin_budget(size, ...)` | admission against the budget, or against free RAM |
| `ensure_pin_registerable(size)` | admission against the registration cap |
| `free_pins(target)` / `free_model_pins(...)` / `free_registrations(...)` | the eviction ladder |
| `should_free_pins_for_ram_pressure(shortfall)` | the sensor, and the one place Windows differs |
| `models_for_pin_eviction()` / `pin_eviction_tiers()` / `registration_eviction_tiers()` | victim ordering |
| `pinned_hostbuf_size(size)` | how large a pinned host buffer to take |

**comfy-env: mostly inherits, and the stand-in stays out of it.** The proxy
holds no tensors, so it pins nothing, and it declares no pinned bytes: claiming
otherwise would put a number into the host's pinned budget for memory that does
not exist. Each worker pins its own weights through the ordinary machinery.

What comfy-env does touch, in the worker only:

| Function | What comfy-env does |
|---|---|
| `free_model_pins` | **wraps** it, to count bytes evicted per victim and the resulting churn. The wrapper calls the original and changes no decision |
| `free_pins` | **calls** it, from the `release_pins` handler |
| `models_for_pin_eviction` | **calls** it, to attribute an eviction to a model |
| `TOTAL_PINNED_MEMORY`, `MAX_PINNED_MEMORY` | **reads** both, per worker, into the census the host ingests |

`contract.py` declares `TOTAL_PINNED_MEMORY` and `free_pins` as SHARED tier
couplings, so these are tracked rather than incidental.

!!! warning "The census is live; the lever is not"
    comfy-env can see exactly how much each worker has pinned. It cannot make
    a worker let go: `broadcast_pin_release` and everything under it has no
    caller. A reader should not infer a working reclaim path from a working
    census.

## Placement: where should this live

| Function | Answers | comfy-env |
|---|---|---|
| `get_torch_device()` | the device in use | **calls**, nine sites |
| `intermediate_device()` | where node outputs go. CPU normally, **the GPU under `--gpu-only`** | inherits, **watch** |
| `intermediate_dtype()` | dtype for those outputs | inherits |
| `unet_offload_device()` | where a UNet goes when evicted | **calls** |
| `unet_inital_load_device(...)` | where it first lands | inherits |
| `text_encoder_device()` / `text_encoder_offload_device()` / `text_encoder_initial_device()` | the same for text encoders | inherits |
| `vae_device()` / `vae_offload_device()` | the same for VAEs | inherits |

`intermediate_device()` is the one worth knowing, and comfy-env inherits it without correction. It decides whether [**Results**](comfyui-memory.md#4-results) live in host memory or VRAM, and
under `--gpu-only` a cached node output holds VRAM that nothing can evict.

!!! warning "`intermediate_device` decides whether Results are RAM or VRAM"
    Under `--gpu-only` it returns the GPU, so every cached node output holds
    VRAM, and the cache that bounds it counts a CUDA tensor as
    [0.05 bytes](comfyui-memory.md#4-results).

## Cast buffers, streams and the node boundary

| Function | Does | comfy-env |
|---|---|---|
| `get_cast_buffer(...)` / `get_aimdo_cast_buffer(...)` | the per stream staging buffers | inherits |
| `reset_cast_buffers()` | releases all of them, plus cross step tensors, dirty mmaps and pinned patch memory | **calls**, in the worker, from three sites in `memory_manager.py`: `release_node_boundary` (per node, aimdo workers), `cast_epoch_boundary` (per prompt epoch, every worker) and `full_release` |
| `get_offload_stream(device)` / `sync_stream(...)` / `current_stream(...)` | the async offload streams | inherits |
| `cast_to(...)` / `cast_to_device(...)` / `cast_to_gathered(...)` | weight casting | inherits |
| `mark_mmap_dirty(storage)` | flags a checkpoint page for writeback | inherits |

!!! danger "`reset_cast_buffers` is the whole of Carry"
    One caller in ComfyUI, in a `finally` around a single node. It is the only
    release path for a sixteen gibibyte reservation and the static tensors
    a sampler reuses between steps. A worker never runs that executor, so
    comfy-env calls it itself: `release_node_boundary` mirrors the per node
    `finally` in aimdo workers, `cast_epoch_boundary` resets the cast buffers
    at every prompt epoch change in every worker (the non-aimdo ratchet
    upstream's gate leaves unreleased), and `full_release` runs it as one step
    of a full worker release. A worker node that never returns still holds all
    of it until it does.

## Flushing and synchronisation

| Function | Does | comfy-env |
|---|---|---|
| [`soft_empty_cache(force=False)`](#soft_empty_cache) | return cached blocks to the driver. **`force` is ignored** | inherits; the worker calls `torch.cuda.empty_cache()` directly |
| `synchronize()` | wait for the device. **No MPS branch, silent no-op there** | inherits |

## Failure and interruption

| Function | Does | comfy-env |
|---|---|---|
| `is_oom(e)` / `raise_non_oom(e)` | classify, and re-raise anything that is not an OOM | inherits |
| `OOM_EXCEPTION` | the type, falling back to bare `Exception` where absent | inherits |
| `discard_cuda_async_error()` | clear a queued async error | inherits |
| `interrupt_current_processing()` / `processing_interrupted()` | the interrupt flag | **calls** `processing_interrupted()` in the host, from `pool._handle_progress`, as the non-consuming read that forwards a cancel to the worker without spending the click; `contract.py` lists it as a FATAL floor entry for that reason |
| `throw_exception_if_processing_interrupted()` | the check nodes are expected to call | inherits; never called by comfy-env, because it clears the flag before raising |
| `InterruptProcessingException` | the exception type | **calls** |

## Module state comfy-env writes to

Seven assignments, six of them inside the worker; the table is on
[inside a worker](worker-memory.md#what-comfy-env-writes-in-a-worker).

## What the proxy must implement

Eighteen members upstream reads off a ledger entry during eviction, with no
declaration anywhere that it will. They are listed and explained on
[the stand-in](stand-in-model.md#what-it-must-answer).


## Function reference

The functions the rest of these pages keep naming, in full: signature,
every parameter, what the body does in order, and who calls it. Read from
`comfy/model_management.py` at the verification commit above.

### `load_models_gpu` { #load_models_gpu }

```python
def load_models_gpu(models, memory_required=0, force_patch_weights=False,
                    minimum_memory_required=None, force_full_load=False)
```

What every loader node and every sampler calls to make models resident.

| Parameter | Meaning |
|---|---|
| `models` | the `ModelPatcher`s to load; each model's `model_patches_models()` (controlnets, hooks) is added, order preserved, duplicates dropped |
| `memory_required` | bytes of working memory the caller expects to need on top of the weights, usually the model's own activation estimate |
| `force_patch_weights` | apply LoRA patches to the weights in place rather than on the fly |
| `minimum_memory_required` | a smaller floor to retry against if the full request cannot be met; defaults to the full request |
| `force_full_load` | skip the partial load budget and load everything |

In order:

1. `cleanup_models_gc()`: drop ledger entries whose model died.
2. `extra_mem = max(minimum_inference_memory(), memory_required + extra_reserved_memory())`: the working memory plus the reserve, floored at 0.8 GiB plus the reserve.
3. For each model, look for it in `current_loaded_models` (by identity of the wrapped model). Found: mark it `currently_used` and reuse the entry. Not found: build a new `LoadedModel`. If any model is not paged (`is_dynamic()` False), the whole call is non dynamic: `free_for_dynamic = False`.
4. Clone dedup: any listed entry that `is_clone` of an incoming model is popped and detached, so a model and its clone never occupy the card twice.
5. Sum per device: `total_memory_required` is each entry's `model_memory_required(device)` (the whole model if it is elsewhere, only the offloaded remainder if it is already partly on this device); `total_pins_required` adds each non paged model's full size, the pinned host copy it will want.
6. `free_memory(total * 1.1 + extra_mem, device, for_dynamic=free_for_dynamic, pins_required=...)` per device: the admission ask, 10 percent over the sum.
7. If free memory is still below `minimum_memory_required`, one more `free_memory` for that floor.
8. Per model, compute `lowvram_model_memory`, the partial load budget: on `NORMAL_VRAM` and `LOW_VRAM`, what is free after the floor, capped by a minimum weight ratio; `0` means load everything (rewritten to `1e32` downstream), `0.1` means load essentially nothing, and `NO_VRAM` forces `0.1`. Then `model_load(lowvram_model_memory)`, and the entry is inserted at index 0 of the ledger (newest first).

Returns nothing. The paged path ignores the budget from step 8 and decides residency page by page at fault time.

### `free_memory` { #free_memory }

```python
def free_memory(memory_required, device, keep_loaded=[], for_dynamic=False,
                pins_required=0, ram_required=0)
```

Make `memory_required` bytes of VRAM free on `device` by unloading listed models.

| Parameter | Meaning |
|---|---|
| `memory_required` | bytes of VRAM that must be free when the call returns; the per victim shortfall is this minus `get_free_memory(device)` |
| `device` | the card; only entries on it are candidates. `None` means every device (`unload_all_models`) |
| `keep_loaded` | entries never to touch; only `unload_model_and_clones` passes one |
| `for_dynamic` | the request is on behalf of paged models only. Paged victims are then skipped (their resident size is subtracted from the request as if already free) and the pin step is skipped; the pager reclaims their pages on demand |
| `pins_required` | pinned host RAM the incoming load will want; when positive and not `for_dynamic`, other models' pinned copies are unpinned until it fits the budget |
| `ram_required` | unused: appears in one debug line, no branch reads it, no caller passes it |

In order:

1. `cleanup_models_gc()`.
2. Build the victim list from every entry on `device` that is not in `keep_loaded` and not dead, clearing each one's `currently_used`; sort by `(-model_offloaded_memory, sys.getrefcount(model), model_memory, index)`: most already off the card first, then fewest references, then smallest, then newest.
3. For each victim, recompute `memory_to_free = memory_required - get_free_memory(device)`. Under `--disable-smart-memory` it is `1e32` instead. A paged victim on a `for_dynamic` call is skipped as above. If the shortfall is positive, `model_unload(memory_to_free)`: partial if the shortfall is smaller than the model's resident size, full otherwise; a paged model's partial unload is the pager unmapping that many bytes of pages. The loop visits every victim; once free memory covers the request the shortfall is no longer positive and nobody else is asked.
4. Pop the unloaded entries from the ledger.
5. If not `for_dynamic` and `pins_required > 0`: `ensure_pin_budget` and `ensure_pin_registerable`, host RAM only.
6. `soft_empty_cache()` if anything was unloaded; otherwise only if torch's idle cache exceeds a quarter of what counts as free and `vram_state` is not `HIGH_VRAM`.

Returns the list of unloaded `LoadedModel`s. It never frees anything but listed models and torch's own cache: cached node outputs holding VRAM, cast buffers, CUDA graph pools and allocations outside torch are untouched.

Callers: `load_models_gpu` (steps 6 and 7 above), `unload_all_models` (`1e30`, every device), `unload_model_and_clones` (`1e30` with a `keep_loaded` list), and comfy-env's budget round trip on a worker's behalf, with two positionals and `for_dynamic` left `False`.

### `get_free_memory` { #get_free_memory }

```python
def get_free_memory(dev=None, torch_free_too=False)
```

"How much room is left" on `dev` (the torch device by default). Returns bytes, or `(total, torch_cache)` when `torch_free_too` is set.

On CUDA it is `torch.cuda.mem_get_info(dev)` free plus `reserved - active` from torch's allocator stats: the driver's free figure plus the blocks torch has freed but kept. The second term is counted as free and is not reliably returnable, since it may be fragmented. The driver's figure is device wide on Linux and the calling process's own budget on Windows WDDM ([why Windows needs its own branch](windows-blind-spot.md)).

Other backends answer differently: CPU and MPS return machine available RAM; DirectML returns a constant 1 GiB (marked `TODO`); XPU computes `total - reserved` from torch's stats, process local by construction; NPU and MLU mirror the CUDA form.

Called on every load (admission and every shortfall recomputation), by the samplers and the VAE to size batches, and by `/system_stats`. There is a second function of the same name, `ModelPatcher.get_free_memory`, which adds what the pager could reclaim on demand.

### `get_total_memory` { #get_total_memory }

```python
def get_total_memory(dev=None, torch_total_too=False)
```

The card's total in bytes (`mem_get_info` total on CUDA, machine RAM on CPU and MPS). Note that `cuMemGetInfo`'s total is not the card: on a 3090 it reports 24,122 MiB where `nvidia-smi` reports 24,576, the driver's own reserve. Read by `maximum_vram_for_weights` for the dtype gates and by `/system_stats`.

### `extra_reserved_memory` and `minimum_inference_memory` { #reserve }

```python
def extra_reserved_memory():     return EXTRA_RESERVED_VRAM
def minimum_inference_memory():  return 0.8 GiB + extra_reserved_memory()
```

`EXTRA_RESERVED_VRAM` is 400 MiB, 600 MiB on Windows, 700 MiB on Windows with more than 15 GB of VRAM, or exactly what `--reserve-vram` says. It is a module global read live on every load, which is why comfy-env can publish into it. `minimum_inference_memory` is the floor that must stay free below any load: 0.8 GiB plus the reserve.

### `soft_empty_cache` { #soft_empty_cache }

```python
def soft_empty_cache(force=False)
```

Hand torch's cached but unused blocks back to the driver: on CUDA `synchronize()`, `empty_cache()`, `ipc_collect()`; the matching call on MPS, XPU, NPU and MLU; nothing on CPU. `force` is accepted and ignored. Returns nothing. Per process: the host calling it releases nothing a worker's allocator holds. Cannot touch pager held weights, which were never in the caching allocator.

### `unload_all_models` { #unload_all_models }

```python
def unload_all_models()
```

`free_memory(1e30, device)` for every device. What the Free button, the top level OOM branch and `--disable-smart-memory` at prompt end call. The `1e30` never reaches a listed model: `model_unload` compares it with `loaded_size()`, it loses, and the entry is fully detached.

### `unload_model_and_clones` { #unload_model_and_clones }

```python
def unload_model_and_clones(model, unload_additional_models=True, all_devices=False)
```

Free everything except the given model, its clones (same `clone_base_uuid`) and, by default, its nested additional models, by calling `free_memory(1e30, device, keep_loaded=...)`. Written for multigpu cloning. The only caller that passes `keep_loaded`.

### `loaded_models` { #loaded_models }

```python
def loaded_models(only_currently_used=False)
```

The wrapped models of every ledger entry, optionally only those `load_models_gpu` marked `currently_used` on its last pass. Node code outside the memory manager borrows this list and hands entries back to `load_models_gpu`; comfy-env's stand-in registers with `currently_used` False so six of the seven callers never see it.

### `LoadedModel.model_unload` { #model_unload }

```python
def model_unload(self, memory_to_free=None, unpatch_weights=True)
```

The entry side of eviction, what `free_memory` calls per victim. If `memory_to_free` is smaller than `loaded_size()`, ask the model for `partially_unload(offload_device, memory_to_free)` and return `False` if it freed enough (a short answer escalates). Otherwise `detach(unpatch_weights)`, detach the finalizer, and return `True`. For a paged model `partially_unload` is the pager unmapping pages; for the stand-in model it is a forward to the worker.

## The HTTP surface

Not Python, and easy to miss when auditing.

| Endpoint | Releases | comfy-env |
|---|---|---|
| `POST /free {"unload_models": true}` | every model on every device | inherits, and it will evict a worker's proxy too |
| `POST /free {"free_memory": true}` | the node output cache and the node instance cache | inherits |
| `POST /history {"clear": true}` | stored prompts and their workflow JSON | inherits |
| `POST /queue {"clear": true}` | queued work | inherits |

## The 98 functions this page skips

They live in `model_management.py` and they are not memory management. They are
worth naming so nobody wonders why they are absent.

* **Capability probes**: `is_nvidia`, `is_amd`, `is_intel_xpu`, `is_ascend_npu`,
  `is_mlu`, `is_ixuca`, `is_wsl`, `cpu_mode`, `mps_mode`, `is_device_cpu`,
  `is_device_cuda`, `is_device_mps`, `is_device_xpu`, `is_directml_enabled`.
* **Dtype selection**: `unet_dtype`, `text_encoder_dtype`, `vae_dtype`,
  `dtype_size`, `supports_dtype`, `supports_cast`, `pick_weight_dtype`,
  `should_use_fp16`, `should_use_bf16`, `supports_fp8_compute`,
  `supports_nvfp4_compute`, `supports_mxfp8_compute`, `supports_fp64`,
  `lora_compute_dtype`, `get_supported_float8_types`.
* **Attention backend selection**: `xformers_enabled`, `pytorch_attention_enabled`,
  `sage_attention_enabled`, `flash_attention_enabled`,
  `comfy_kitchen_attention_enabled`, `force_upcast_attention_dtype`.
* **Device enumeration and naming**: `get_all_torch_devices`,
  `get_gpu_device_options`, `resolve_gpu_device_option`, `cuda_device_context`,
  `set_torch_device`, `get_torch_device_name`, `mac_version`, `amd_min_version`.

They change how much memory a model occupies, which is not the same as managing
it. A dtype decision is made once at load; the functions on this page run for the
life of the process.

## The short answer

Out of a 113 function surface, comfy-env **calls fifteen**, **patches three**,
and **implements eighteen members** on a proxy object. Everything else it
inherits, and the inheriting is deliberate: a worker is a real ComfyUI process,
so the correct behaviour is usually upstream's own.

The three things it patches are all the same fix wearing different clothes. A
worker cannot see what the rest of the machine holds, so comfy-env tells it.
