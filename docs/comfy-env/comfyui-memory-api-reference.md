# ComfyUI's memory API, function reference

*The memory management functions the other pages keep naming, in full:
signature, every parameter, what the body does in order, the return value,
and who calls it. Read from `comfy/model_management.py` at ComfyUI
`bab6ee5f` (2026-08-24), re-checked 2026-09-15.*
{: .subtitle }

The shape of the surface and comfy-env's relationship to every symbol on it
is [ComfyUI's memory API](comfyui-memory-api-inventory.md); this page is
the detail for the handful of functions that carry the memory decisions.

## Two words first: `ModelPatcher` and `LoadedModel` { #modelpatcher }

A **`ModelPatcher`** (`comfy/model_patcher.py`) is ComfyUI's wrapper around
a torch model. It holds the raw `nn.Module`, the device the model runs on
(`load_device`) and the one it is parked on when evicted
(`offload_device`), the list of weight patches to apply (LoRAs, applied to
the weights on the fly or baked in), and the methods the memory manager
calls: how big it is (`model_size`), how much of it is on the card
(`loaded_size`), load some of it (`partially_load`), move some of it back
(`partially_unload`), take it off the card (`detach`), make a second
handle to the same weights (`clone`). Every model a node hands around, the
`MODEL` output of a checkpoint loader, the model inside a `CLIP` or a
`VAE`, a controlnet, is a `ModelPatcher`. Under the pager it is the
subclass `ModelPatcherDynamic`, which answers `is_dynamic()` True and
loads page by page.

A **`LoadedModel`** (`comfy/model_management.py`) is the ledger entry:
what `current_loaded_models` actually holds. It wraps one `ModelPatcher`
through a weak reference, remembers which device it was loaded to, carries
the `currently_used` flag, and turns the memory manager's questions into
calls on the patcher (`model_memory`, `model_loaded_memory`,
`model_offloaded_memory`, `model_load`, `model_unload`). comfy-env's
stand-in model is a duck typed `ModelPatcher` wrapped in a real
`LoadedModel`.

## `load_models_gpu` { #load_models_gpu }

```python
def load_models_gpu(models, memory_required=0, force_patch_weights=False,
                    minimum_memory_required=None, force_full_load=False)
```

What every loader node and every sampler calls to make models resident.

| Parameter | Meaning |
|---|---|
| `models` | the [`ModelPatcher`s](#modelpatcher) to make resident. Each one's `model_patches_models()`, the extra models its patches need alongside it (a controlnet, hook models), is added to the list; order is preserved and duplicates dropped |
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

## `free_memory` { #free_memory }

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

## `get_free_memory` { #get_free_memory }

```python
def get_free_memory(dev=None, torch_free_too=False)
```

"How much room is left" on `dev` (the torch device by default). Returns bytes, or `(total, torch_cache)` when `torch_free_too` is set.

On CUDA it is `torch.cuda.mem_get_info(dev)` free plus `reserved - active` from torch's allocator stats: the driver's free figure plus the blocks torch has freed but kept. The second term is counted as free and is not reliably returnable, since it may be fragmented. The driver's figure is device wide on Linux and the calling process's own budget on Windows WDDM ([why Windows needs its own branch](windows-blind-spot.md)).

Other backends answer differently: CPU and MPS return machine available RAM; DirectML returns a constant 1 GiB (marked `TODO`); XPU computes `total - reserved` from torch's stats, process local by construction; NPU and MLU mirror the CUDA form.

Called on every load (admission and every shortfall recomputation), by the samplers and the VAE to size batches, and by `/system_stats`. There is a second function of the same name, `ModelPatcher.get_free_memory`, which adds what the pager could reclaim on demand.

## `get_total_memory` { #get_total_memory }

```python
def get_total_memory(dev=None, torch_total_too=False)
```

The card's total in bytes (`mem_get_info` total on CUDA, machine RAM on CPU and MPS). Note that `cuMemGetInfo`'s total is not the card: on a 3090 it reports 24,122 MiB where `nvidia-smi` reports 24,576, the driver's own reserve. Read by `maximum_vram_for_weights` for the dtype gates and by `/system_stats`.

## `extra_reserved_memory` and `minimum_inference_memory` { #reserve }

```python
def extra_reserved_memory():     return EXTRA_RESERVED_VRAM
def minimum_inference_memory():  return 0.8 GiB + extra_reserved_memory()
```

`EXTRA_RESERVED_VRAM` is 400 MiB, 600 MiB on Windows, 700 MiB on Windows with more than 15 GB of VRAM, or exactly what `--reserve-vram` says. It is a module global read live on every load, which is why comfy-env can publish into it. `minimum_inference_memory` is the floor that must stay free below any load: 0.8 GiB plus the reserve.

## `soft_empty_cache` { #soft_empty_cache }

```python
def soft_empty_cache(force=False)
```

Hand torch's cached but unused blocks back to the driver: on CUDA `synchronize()`, `empty_cache()`, `ipc_collect()`; the matching call on MPS, XPU, NPU and MLU; nothing on CPU. `force` is accepted and ignored. Returns nothing. Per process: the host calling it releases nothing a worker's allocator holds. Cannot touch pager held weights, which were never in the caching allocator.

## `unload_all_models` { #unload_all_models }

```python
def unload_all_models()
```

`free_memory(1e30, device)` for every device. What the Free button, the top level OOM branch and `--disable-smart-memory` at prompt end call. The `1e30` never reaches a listed model: `model_unload` compares it with `loaded_size()`, it loses, and the entry is fully detached.

## `unload_model_and_clones` { #unload_model_and_clones }

```python
def unload_model_and_clones(model, unload_additional_models=True, all_devices=False)
```

Free everything except the given model, its clones (same `clone_base_uuid`) and, by default, its nested additional models, by calling `free_memory(1e30, device, keep_loaded=...)`. Written for multigpu cloning. The only caller that passes `keep_loaded`.

## `loaded_models` { #loaded_models }

```python
def loaded_models(only_currently_used=False)
```

The wrapped models of every ledger entry, optionally only those `load_models_gpu` marked `currently_used` on its last pass. Node code outside the memory manager borrows this list and hands entries back to `load_models_gpu`; comfy-env's stand-in registers with `currently_used` False so six of the seven callers never see it.

## `LoadedModel.model_unload` { #model_unload }

```python
def model_unload(self, memory_to_free=None, unpatch_weights=True)
```

The entry side of eviction, what `free_memory` calls per victim. If `memory_to_free` is smaller than `loaded_size()`, ask the model for `partially_unload(offload_device, memory_to_free)` and return `False` if it freed enough (a short answer escalates). Otherwise `detach(unpatch_weights)`, detach the finalizer, and return `True`. For a paged model `partially_unload` is the pager unmapping pages; for the stand-in model it is a forward to the worker.

