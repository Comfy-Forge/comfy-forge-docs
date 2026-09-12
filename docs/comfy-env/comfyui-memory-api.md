# ComfyUI's memory management API

*What ComfyUI offers a caller, what it demands of a model in return, and how
comfy-env satisfies both from another process.*

*Last verified against ComfyUI `bab6ee5f` (2026-08-24) and comfy-env `f1f8260` (2026-09-04).*

Read [ComfyUI memory management background](comfyui-memory.md) first. This page
is the interface rather than the design. For the exhaustive list, function by
function, with what comfy-env does about each, see
[ComfyUI memory API inventory](comfyui-memory-api-inventory.md).

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

!!! note "Why eighteen and not fourteen"
    Fourteen of them appear as a literal `.model.<name>` in the memory manager
    (`model` itself among them, as `.model.model`), which is what a grep can
    detect. The other four never do: `load_device`, `parent`,
    `model_patches_models` and `get_nested_additional_models` are reached
    through an alias (`model = loaded_model.model`, then `model.load_device`)
    or read off the INCOMING model rather than a list entry. `load_device` is
    the one upstream reads most, and a literal grep sees it zero times. That
    is why the compatibility test walks the AST and follows the alias rather
    than grepping; its own docstring in `tests/test_model_patcher_surface.py`
    records the same count.

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

The worker imports `comfy.model_management` from the host ComfyUI tree and
calls the real functions, with one exception.
`load_models_gpu` IS replaced: the worker assigns its own
`_shimmed_load_models_gpu` over it, which measures the incoming models, asks
the host to free room for them, writes back what the host says, and only then
calls the original it saved. Nothing is reimplemented, and the real function
still does the loading, but a reader who takes "no shim" literally will not
understand where a worker's reserve comes from.

The one thing it does do is **correct the numbers it reads**, because
`get_free_memory` in a worker reports that process's own view on WDDM. The
correction itself is not platform gated: `pool._handle_vram_budget` computes
its offset as ComfyUI's blind reading minus the NVML free figure wherever
NVML answers, on Linux included. What differs is the arithmetic: on WDDM the
difference is what siblings hold, while on Linux `cudaMemGetInfo` is already
device-wide, the sibling term cancels, and the offset collapses to the host's
own idle torch cache. The platform verdict only chooses the fallback when NVML
is absent (reconstruct from comfy-env's ledger on WDDM, trust the blind
reading elsewhere), so the double count it exists to avoid is the ledger's,
not NVML's. See
[comfy-env's memory management](memory-approach.md).

### Contract two: a duck type

Registering a stand-in is the only mechanism by which upstream's own eviction
reaches another process ([ADR-0038](adr/0038-the-memory-floor.md)). Every
defect ever found in this object has come through it, and every one was a
wrong number found by audit rather than a missing attribute found by a user.
The design reasoning below is why a duck type beat a subclass; it is the
reason the object is safe to keep, not an argument for replacing it.

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

!!! warning "What the tripwire covers, and what it does not"
    It runs weekly against ComfyUI master, in a job the workflow itself marks
    *allowed to fail, never gates anything*. So it is a notification, not a
    gate: a red run opens an issue and publishing continues.

    It reads one file, `model_management.py`. Node code outside it also reads
    the loaded-model list, and the canary does not look there. It follows both
    `.model.<name>` and the `model = entry.model` alias within a function, but
    three surface members are read off the INCOMING model rather than off a
    list entry and no ledger-shaped sweep finds those.

!!! warning "And it cannot see the failure that actually happens"
    The canary catches a **missing** member. Every defect found in this seam has
    been a **wrong value** on a member the proxy implements. The one still live
    is the eviction sort key that places the proxy first; the other, a size fed
    into the wrong pin budget through a leaked `loaded_models()` list, closed on
    2026-09-06 when every stand-in started registering with `currently_used`
    False. A surface check is structurally blind to both kinds.

    It also reads only one file. Within that file it does follow the
    `m = entry.model` alias, as the paragraph above says; what it cannot follow
    is a read that happens somewhere else, in node code that borrowed the list.

## What this seam costs, honestly

The proxy works. It is registered, upstream evicts it, and the surface is
currently complete against `b133e483`.

On a default install every host model is managed dynamically, and the eviction
loop has a bypass for those, while the proxy reports itself as non dynamic and
does not get it. The worker's model can therefore be the only entry upstream
evicts, and it also sorts first, because a fresh proxy reports nothing already
offloaded and the lowest possible reference count.

Two things stop that being a complaint about upstream.

The bypass is `if entry.model.is_dynamic() and for_dynamic:`, and the comment
under it says why: *"don't actually unload dynamic models for the sake of other
dynamic models as that works on-demand."* Evicting a paged model to make room
for another paged model is churn, because the pager reclaims on demand. That is
a considered refusal, not drift.

And `for_dynamic` is a **parameter**. comfy-env's own `free_memory` call leaves
it False, so on the path comfy-env drives the bypass does not fire at all and
host dynamic models are fully evictable. Where the asymmetry does appear, it is
downstream of comfy-env's own choice to answer `is_dynamic()` False, which
[ADR-0035](adr/0035-duck-typed-model-proxy.md) calls load-bearing and
[where the stand-in is inaccurate](model-stand-in-inaccuracies.md) prices honestly. It is the cost
of the safe answer, not a tilt in the field.
