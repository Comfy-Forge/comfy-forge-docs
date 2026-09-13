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
| `get_free_memory(device)` | driver free plus torch's own cache | **calls**, eleven sites, and corrects the answer |
| `get_total_memory(device)` | device total | **calls** |
| `module_size(module)` | bytes of a state dict, nothing else the module holds | inherits |
| `minimum_inference_memory()` | the floor that must stay free | **calls**, in the admission sum |
| `extra_reserved_memory()` | the reserve on top of that floor | **calls**, and comfy-env PUBLISHES into the global behind it so upstream's own arithmetic accounts for worker VRAM |
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
| `load_models_gpu(models, memory_required=, ...)` | budget, evict, load | **patches** in the worker; **calls** the real one after |
| `load_model_gpu(model)` | one model, thin wrapper | inherits |
| `free_memory(required, device, keep_loaded=, for_dynamic=, pins_required=, ram_required=)` | "get me this many free bytes" | **calls**, with upstream's own target expression (`reserve.ask_target`), exactly two positionals, never `for_dynamic` |
| `unload_all_models()` | evict everything, everywhere | **reads**; comfy-env registers no wrap here. The stand-in is reached through it, because `unload_all_models` walks the list and `LoadedModel.model_unload` calls `detach` on every entry |
| `unload_model_and_clones(model, ...)` | drop one model and its clones for a clean reload | inherits |
| `loaded_models(only_currently_used=)` | the ledger contents | **watch**: it hands the proxy to arbitrary node code |
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
| `soft_empty_cache(force=False)` | return cached blocks to the driver. **`force` is ignored** | inherits; the worker calls `torch.cuda.empty_cache()` directly |
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
