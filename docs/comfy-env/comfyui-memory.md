# How ComfyUI manages memory

*A plain-language tour of `comfy/model_management.py`, the file that decides
what lives on your GPU. Line references are against **ComfyUI v0.33.0**
(`comfy/model_management.py` unless noted); they drift by a few lines between
releases.*

You need this page before [Sharing one GPU](sharing-one-gpu.md), because
everything comfy-env does about VRAM is a reaction to what is described here.

## The problem in one paragraph

A checkpoint is several gigabytes. A graphics card has a handful of gigabytes.
A workflow may touch a UNet, a VAE, a text encoder, two ControlNets and an
upscaler. They do not all fit at once, and moving one between CPU and GPU costs
seconds. So ComfyUI keeps as much as it can resident, and evicts the cheapest
thing only when it must. Everything below is machinery for "as much as it can"
and "only when it must".

## The ledger

ComfyUI tracks resident models in one process-global list:

```python
current_loaded_models = []          # of LoadedModel
```

Each `LoadedModel` wraps one model and remembers which device it is on and how
much of it is currently loaded. Two details matter later:

- **It holds a weak reference.** `self._model = weakref.ref(model)` (`:751`).
  If nothing else in your Python process is holding that model, it can be
  garbage-collected out from under the ledger; `is_dead()` (`:827-828`) is how
  the eviction loop skips those corpses.
- **Equality is identity.** `__eq__` is `self.model is other.model` (`:820-821`)
  — not a value comparison.

## Loading: "how much of this fits?"

`load_models_gpu(models, ...)` (`:909`) is the entry point. For each model it
computes a budget called `lowvram_model_memory` (`:995-996`):

> free memory, minus the headroom we must not touch, minus what is already
> loaded — and never more than 40% of free memory on non-NVIDIA cards
> (`MIN_WEIGHT_MEMORY_RATIO`, which is **0.0 on NVIDIA**, `:454-456`).

Then it hands that number to the model. And here is the part that trips
everyone up:

!!! warning "Two magic numbers"
    | Value | Means |
    |---|---|
    | `0` | **"Load everything."** Rewritten to `1e32` at `:787-789`. |
    | `0.1` | **"Load essentially nothing."** Set at `:998-999` when the computed budget came out as zero, and at `:1002` under `NO_VRAM`. |

    So the smallest possible request and the largest possible request are
    `0.1` and `0` — adjacent numbers with opposite meanings. Any code that
    does `int(extra_memory)` turns `0.1` into `0` and inverts the instruction.

## Partial residency

A model does not have to be all-in or all-out. `partially_load(device, extra_memory)`
loads layers until it has used `extra_memory` more bytes; the rest stay on the
CPU and are streamed in during the forward pass. `partially_unload` is the
reverse. This is what "lowvram mode" actually is — not a separate mode, just a
small budget.

## Reserved headroom

ComfyUI never fills the card. It holds back:

| Reserve | Amount | Where |
|---|---|---|
| `EXTRA_RESERVED_VRAM` | **400 MB**, or **600 MB on Windows** (*"the shared vram issue"*), **+100 MB** on Windows cards over 15 GB | `:847-851` |
| inference working room | **0.8 GB** + the above | `minimum_inference_memory()`, `:860-861` |
| user override | `--reserve-vram <GB>` replaces the first entirely | `:853-854` |

So on a 16 GB Windows card, roughly 1.5 GB is spoken for before a single weight
is loaded.

## Freeing: the eviction loop

`free_memory(memory_required, device)` walks the ledger and unloads until there
is room. The interesting part is the order it picks victims (`:875`):

```python
can_unload.append((-shift_model.model_offloaded_memory(),
                   sys.getrefcount(shift_model.model),
                   shift_model.model_memory(), i))
```

Sorted ascending, so the first victim is the one with the **largest
already-offloaded portion** — i.e. the model that is already mostly on the CPU,
because finishing that eviction is the cheapest way to free the next byte. Ties
break on refcount (least-referenced first), then size.

Then, per victim (`:879-889`):

```python
memory_to_free = 1e32
if not DISABLE_SMART_MEMORY or device is None:
    memory_to_free = memory_required - get_free_memory(device)
if memory_to_free > 0 and current_loaded_models[i].model_unload(memory_to_free):
    ...
```

Note it **recomputes free memory every iteration** — it is a feedback loop, not
a precomputed plan. It stops as soon as there is enough room.

!!! note "`--disable-smart-memory` changes the contract"
    With that flag, `memory_to_free` is hard-set to `1e32` (`:881`) and the
    recompute at `:883` is skipped. Every candidate is fully detached rather
    than partially unloaded. The feedback loop above simply does not run.

## The number everything depends on

`get_free_memory(device)` (`:1739`) is the input to all of the above. On CUDA
(`:1772-1778`):

```python
mem_free_cuda, _ = torch.cuda.mem_get_info(dev)
mem_free_torch  = mem_reserved - mem_active     # torch's own reusable cache
mem_free_total  = mem_free_cuda + mem_free_torch
```

**This is the honest number only on Linux.** `torch.cuda.mem_get_info` asks the
driver, and on Windows the driver answers *per process*: it reports what **this
process** may still commit, not what the card has free. Another process can be
using 13 GB and this number barely moves.

ComfyUI has no platform branch here — it makes one unconditional call and
trusts it. That is not an oversight so much as an assumption that ComfyUI is the
only thing using the GPU. Which is exactly the assumption comfy-env breaks by
putting models in worker subprocesses.

## Why comfy-env has to care

comfy-env runs node code in separate processes, so a worker's models are real
VRAM that ComfyUI's ledger cannot see. Everything in
[Sharing one GPU](sharing-one-gpu.md) — the model proxies, the admission
arithmetic, the eviction bridge — exists to put worker-held memory back into the
picture above, without forking any of it.

The short version: comfy-env registers a stand-in object into
`current_loaded_models` so upstream's eviction loop can evict a worker's model
the same way it evicts its own, and pre-compensates `memory_required` so the
feedback loop converges on the truth rather than on the parent's blind view.

## Cheat sheet

| Thing | One line |
|---|---|
| `current_loaded_models` | the list of what is resident, weakly held |
| `load_models_gpu` | "make room and load these" |
| `free_memory(n, dev)` | "get me `n` free bytes on `dev`" |
| `partially_load/unload` | move part of a model across the PCIe bus |
| `lowvram_model_memory` | the per-model byte budget |
| `0` / `0.1` | "everything" / "nothing" |
| `minimum_inference_memory()` | 0.8 GB + reserve, never touched |
| `get_free_memory` | the input to every decision; per-process on Windows |
