# The memory management API

*What ComfyUI offers a caller, what it demands of a model in return, and how
comfy-env satisfies both from another process.*

*Last verified against ComfyUI `bab6ee5f` (2026-08-24) and comfy-env `f1f8260` (2026-09-04).*

Read [ComfyUI memory management background](comfyui-memory.md) first. This page
is the interface rather than the design. For the exhaustive list, function by
function, with what comfy-env does about each, see
[Memory API inventory](memory-api-inventory.md).

## There are two contracts, and they point in opposite directions

Almost every discussion of this conflates them, and they fail differently.

**You call ComfyUI.** A node asks where a tensor should live, or asks for a model
to be made resident. This is an ordinary module API and it either works or raises.

**ComfyUI calls you.** Once a model is in the ledger, the memory manager reads
members off it during eviction, without asking permission. This is an implicit
interface with no declaration anywhere, and it fails by `AttributeError` in the
middle of someone else's loop.

comfy-env has to satisfy both, and the second is the hard one.

## Contract one: what you call

All of it is `comfy.model_management`. Counted by how often node code in the
tree actually calls it:

### Where should this tensor go

The most used part of the API by a wide margin, and the least discussed.

| Function | Answers |
|---|---|
| `get_torch_device()` | the device this ComfyUI is running on |
| `intermediate_device()` | where node outputs should live. **CPU normally, the GPU under `--gpu-only`** |
| `intermediate_dtype()` | the dtype for those outputs |
| `unet_offload_device()` `text_encoder_offload_device()` `vae_offload_device()` | where each model type goes when evicted |
| `text_encoder_device()` `vae_device()` | where each runs |
| `unet_manual_cast()` | whether weights need casting on the fly |

`intermediate_device()` is the one worth knowing. It decides whether [**Results**](comfyui-memory.md#4-results) live in host memory or VRAM, and
under `--gpu-only` a cached node output holds VRAM that nothing can evict.

### Make room, and load

| Function | Does |
|---|---|
| `load_models_gpu(models, memory_required=, force_patch_weights=, ...)` | the main entry point. Computes a budget, evicts if needed, loads |
| `load_model_gpu(model)` | one model, thin wrapper |
| `free_memory(memory_required, device, keep_loaded=, for_dynamic=, pins_required=, ram_required=)` | "get me this many free bytes on this device" |
| `unload_all_models()` | evict everything, everywhere |
| `loaded_models(only_currently_used=False)` | the current ledger contents |

!!! warning "`free_memory` has a parameter that does nothing"
    `ram_required` appears in one log string and no caller in the tree passes it.
    Host RAM is not budgeted. See
    [nothing budgets pageable RAM](comfyui-memory.md#making-room).

### How much is there

| Function | Returns |
|---|---|
| `get_free_memory(device)` | driver free **plus torch's own cache**, which is not reliably returnable |
| `get_total_memory(device)` | device total |
| `module_size(module)` | bytes of a module's state dict, and nothing else it holds |
| `minimum_inference_memory()` | the floor that must stay free |
| `soft_empty_cache(force=False)` | return cached blocks to the driver |

!!! danger "There are two functions called `get_free_memory`"
    The module one, above, and `ModelPatcher.get_free_memory`, which adds what
    the dynamic manager could reclaim on demand. They return different numbers
    for the same device, and upstream uses both in the same batching decision.
    Which one you want depends on whether you are asking "what is free" or
    "what could I get".

### When it goes wrong

| Function | Does |
|---|---|
| `raise_non_oom(e)` | re-raise unless this is an out of memory error. The correct guard for a retry loop |
| `OOM_EXCEPTION` | the exception type, which falls back to bare `Exception` on builds without it |

Eleven places in the tree catch an OOM and retry smaller. `raise_non_oom` is what
keeps those from swallowing real bugs, and six weight adapters do not call it.

### Release on request

Not a Python API at all, and the one most people miss:

| Endpoint | Releases |
|---|---|
| `POST /free {"unload_models": true}` | every model, on every device |
| `POST /free {"free_memory": true}` | the node output cache **and** the node instance cache, by rebuilding both |
| `POST /history {"clear": true}` | stored prompts and their workflow JSON |
| `POST /queue {"clear": true}` | queued work |

This is a documented public endpoint with a button in the stock interface. It is
the only release path that answers to a person rather than to a condition, and
it short circuits three of the six kinds at once.

## Contract two: what ComfyUI calls on you

Put an object in `current_loaded_models` and the memory manager will read
members off it, at times of its choosing, in the middle of eviction. Eighteen of
them, none declared anywhere in upstream:

```
load_device          offload_device       parent               model
model_size           loaded_size          current_loaded_device
model_dtype          model_patches_to     model_patches_models
partially_load       partially_unload     detach
lowvram_patch_counter  is_dynamic         is_clone
clone_base_uuid      get_nested_additional_models
```

!!! note "Why eighteen and not sixteen"
    Sixteen are read as a literal `.model.<name>` in the memory manager, which is
    what the compatibility test can detect by grepping. The other two arrive by a
    different route: `parent` is read by the ledger entry itself when it adopts a
    model, and `model` is the inner module the whole object stands in for. A grep
    based check finds the sixteen; the other two have to be known.

Three groups, by what they are for:

* **Identity and placement.** `load_device`, `offload_device`, `parent`,
  `clone_base_uuid`, `is_clone`. Eviction needs to know what a thing is and
  whether two entries are the same model.
* **Accounting.** `model_size`, `loaded_size`, `current_loaded_device`,
  `lowvram_patch_counter`. How big, how much of it is resident, and where.
* **Action.** `partially_load`, `partially_unload`, `detach`, `model_patches_to`.
  The verbs eviction actually calls.

`is_dynamic` deserves its own note. It decides whether an entry is managed by
the dynamic manager, and returning `False` excludes an object from every
pin path, every cast buffer reset, and the dynamic model bypass in the eviction
loop. It is the single highest leverage member on the list.

## How comfy-env satisfies both

### Contract one is not intercepted at all

The worker imports `comfy.model_management` from the host ComfyUI tree and calls
the real functions. There is no shim, no reimplementation and no divergence,
because the worker is a real ComfyUI process in every respect except that it did
not start the server.

The one thing it does do is **correct the numbers it reads**, because
`get_free_memory` in a worker reports that process's own view -- though only
where that is actually true. On Linux `cudaMemGetInfo` is device-wide, so the
correction is a double count there and is applied on WDDM only. See
[comfy-env's memory management](memory-approach.md).

### Contract two: a duck type, now deprecated

!!! warning "This section describes a mechanism on its way out"

    Registering a stand-in for a worker's model was how comfy-env let upstream
    evict across the process boundary. Both of comfy-env's loud breakages in
    twelve months came through that object, and once workers release VRAM on
    their own it buys latency rather than capability, so host-driven reclaim
    was dropped ([ADR-0038](adr/0038-the-memory-floor.md)). Nothing in the
    memory floor depends on it. What remains sanctioned is a read-only
    observer that reports holding nothing and is **off by default**; the
    object below is still registered but is scheduled for removal.

    The design reasoning that follows is still worth reading: it is why a
    duck type beat a subclass, and it applies to the observer too.

comfy-env registers a stand in object into `current_loaded_models` so upstream
can evict a worker's model the way it evicts its own. That object declares its
surface explicitly:

```python
COMFY_SURFACE = frozenset({
    "load_device", "offload_device", "parent", "model", "clone_base_uuid",
    "model_size", "loaded_size", "current_loaded_device", "model_dtype",
    "model_patches_to", "model_patches_models", "partially_load",
    "partially_unload", "detach", "lowvram_patch_counter", "is_dynamic",
    "is_clone", "get_nested_additional_models",
})
```

**It does not inherit `ModelPatcher`**, and a test enforces that. Inheriting
would silently import well over a hundred members that are wrong for an object
holding no weights, and every one of them would appear to work while returning
nonsense. A duck type fails loudly on the member it lacks; a wrong subclass
answers confidently.

The proxy's `__getattr__` is the loud failure: anything upstream reaches for that
is not in the surface raises with a message naming the member and telling the
reader to extend `COMFY_SURFACE` rather than reach for inheritance.

### How we know it still fits

A test greps upstream's `model_management.py` for every `.model.<name>` access,
subtracts the names that are not patcher members and the ones gated behind
`is_dynamic()`, and asserts the remainder is a subset of `COMFY_SURFACE`.

That is the right shape for the problem. Upstream has no declared interface, so
the test derives one from the source rather than trusting a written record.

!!! danger "The tripwire does not run"
    The canary's own docstring calls it a CI tripwire. It is not one.

    The main test job selects `not comfyui` markers and installs no ComfyUI, so
    the test skips for want of a tree to grep. The canary job installs ComfyUI
    but selects only `-m comfyui`, and this file carries no marker, so it is
    deselected. It runs in neither.

    Its dev machine fallback paths do not match this repository's layout either,
    so it skips locally as well.

!!! warning "And it cannot see the failure that actually happens"
    The canary catches a **missing** member. Both live defects in this seam are
    **wrong values** on members the proxy implements: an eviction sort key that
    places the proxy first, and a size that is fed into the wrong budget. A
    surface check is structurally blind to those.

    It also only reads one file, and only literal `.model.<name>` accesses, so
    an aliased read of the form `m = entry.model` followed by `m.load_device`
    is invisible to it.

## What this seam costs, honestly

The proxy works. It is registered, upstream evicts it, and the surface is
currently complete against `b133e483`.

What has shifted underneath it is that on a default install **every host model is
managed dynamically and therefore protected** by the bypass in the eviction loop,
while the proxy reports itself as non dynamic and is not. The worker's model is
the only entry upstream can actually evict, and it also sorts first, because a
fresh proxy reports nothing already offloaded and the lowest possible reference
count.

That is not a bug in the proxy. It is the field tilting under a design that was
correct when both sides were the same kind of thing.
