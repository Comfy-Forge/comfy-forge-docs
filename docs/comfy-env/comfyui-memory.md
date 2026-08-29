# How ComfyUI manages memory

*A plain-language tour of the memory a running ComfyUI occupies and the
machinery that manages it -- chiefly `comfy/model_management.py`, plus the
execution cache that owns most of your RAM between runs. Line references are
against **ComfyUI v0.33.0** (`comfy/model_management.py` unless noted); they
drift by a few lines between releases.*

You need this page before [Sharing one GPU](sharing-one-gpu.md), because
everything comfy-env does about VRAM is a reaction to what is described here.

## The problem in one paragraph

A checkpoint is several gigabytes. A graphics card has a handful of gigabytes.
A workflow may touch a UNet, a VAE, a text encoder, two ControlNets and an
upscaler. They do not all fit at once, and moving one between CPU and GPU costs
seconds. So ComfyUI keeps as much as it can resident, and evicts the cheapest
thing only when it must. Model weights are the star of this show -- but they
are not the only memory in the theater, and the biggest RAM consumer between
runs is usually something else entirely.

## The map

Six kinds of memory, with very different amounts of actual management:

| # | Memory | Where it lives | How much ComfyUI manages it |
|---|---|---|---|
| 1 | Model weights on the GPU | VRAM | **Fully managed.** The `current_loaded_models` ledger and the evict-the-minimum loop -- [the ledger](#the-ledger) and everything through [eviction](#freeing-the-eviction-loop). |
| 2 | Model weights evicted to the CPU | RAM | **Managed as the same ledger's other half** -- [eviction is a move, not a delete](#the-ram-half-of-the-ledger). |
| 3 | Pinned (page-locked) RAM | RAM | **Fully managed**, by [a separate budget](#the-pin-budget). |
| 4 | Activation working memory | VRAM | **Managed by guessing** -- [reserved headroom](#reserved-headroom), never tracked. |
| 5 | The allocator cache | VRAM | **Counted and flushable**, not allocated by ComfyUI -- see [the number everything depends on](#the-number-everything-depends-on). |
| 6 | Cached node outputs and node instances | RAM (+VRAM if outputs hold GPU tensors) | **Managed by a different subsystem** -- [the execution caches](#the-execution-caches), not the memory manager. |

Only #1 and #3 involve ComfyUI actively deciding what to keep and what to
give back. #2 is a consequence of #1, #4 and #5 are arithmetic around memory
it cannot control, and #6 is a cache-retention policy that happens to hold
most of your RAM. So: how is #1 managed?

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
  -- not a value comparison.

## Loading: "how much of this fits?"

`load_models_gpu(models, ...)` (`:909`) is the entry point. For each model it
computes a budget called `lowvram_model_memory` (`:995-996`):

> free memory minus the headroom we must not touch -- but at least 40% of free
> memory on non-NVIDIA cards, even when that eats into the inference reserve
> (`MIN_WEIGHT_MEMORY_RATIO` is a **floor** inside a `max(...)`, and it is
> **0.0 on NVIDIA**, `:454-456`) -- minus what is already loaded.

Then it hands that number to the model. And here is the part that trips
everyone up:

!!! warning "Two magic numbers"
    | Value | Means |
    |---|---|
    | `0` | **"Load everything."** Rewritten to `1e32` at `:787-789`. |
    | `0.1` | **"Load essentially nothing."** Set at `:998-999` when the computed budget came out as zero, and at `:1002` under `NO_VRAM`. |

    So the smallest possible request and the largest possible request are
    `0.1` and `0` -- adjacent numbers with opposite meanings. Any code that
    does `int(extra_memory)` turns `0.1` into `0` and inverts the instruction.

## Partial residency

A model does not have to be all-in or all-out. `partially_load(device, extra_memory)`
loads layers until it has used `extra_memory` more bytes; the rest stay on the
CPU and are streamed in during the forward pass. `partially_unload` is the
reverse. This is what "lowvram mode" actually is -- not a separate mode, just a
small budget.

## Reserved headroom

The budget above never fills the card, because a forward pass creates
temporary activation tensors that are never tracked -- ComfyUI just reserves
room for them up front and estimates per-operation needs before big ops like
VAE decode. It holds back:

| Reserve | Amount | Where |
|---|---|---|
| `EXTRA_RESERVED_VRAM` | **400 MB**, or **600 MB on Windows** (*"the shared vram issue"*), **+100 MB** on Windows cards over 15 GB | `:847-851` |
| inference working room | **0.8 GB** + the above | `minimum_inference_memory()`, `:860-861` |
| user override | `--reserve-vram <GB>` replaces the first entirely | `:853-854` |

So on a 16 GB Windows card, roughly 1.5 GB is spoken for before a single
weight is loaded. When the guess is short, the symptom is an OOM mid-sampling
and the knob is `--reserve-vram`.

## Freeing: the eviction loop

`free_memory(memory_required, device)` walks the ledger and unloads until
there is room. The interesting part is the order it picks victims (`:875`):

```python
can_unload.append((-shift_model.model_offloaded_memory(),
                   sys.getrefcount(shift_model.model),
                   shift_model.model_memory(), i))
```

Sorted ascending, so the first victim is the one with the **largest
already-offloaded portion** -- i.e. the model that is already mostly on the
CPU, because finishing that eviction is the cheapest way to free the next
byte. Ties break on refcount (least-referenced first), then size.

Then, per victim (`:879-889`, verbatim):

```python
for x in can_unload_sorted:
    i = x[-1]
    memory_to_free = 1e32
    if not DISABLE_SMART_MEMORY or device is None:
        memory_to_free = 0 if device is None else memory_required - get_free_memory(device)
        if current_loaded_models[i].model.is_dynamic() and for_dynamic:
            #don't actually unload dynamic models for the sake of other dynamic models
            #as that works on-demand.
            memory_required -= current_loaded_models[i].model.loaded_size()
            memory_to_free = 0
    if memory_to_free > 0 and current_loaded_models[i].model_unload(memory_to_free):
```

Note it **recomputes free memory every iteration** -- it is a feedback loop,
not a precomputed plan. It stops as soon as there is enough room. (A `None`
device means "free everything everywhere", and dynamic models are spared when
the caller is itself loading one -- they stream on demand anyway.)

!!! note "`--disable-smart-memory` changes the contract"
    With that flag (and a concrete device), `memory_to_free` stays hard-set to
    `1e32` (`:881`) and the recompute at `:883` never runs. Every candidate is
    fully detached rather than partially unloaded. The feedback loop above
    simply does not run.

Eviction frees VRAM. But the weights have to go *somewhere*.

## The RAM half of the ledger

Evicting a model does not destroy it -- `model_unload` moves weights to the
model's `offload_device`, which is CPU RAM. VRAM pressure therefore *converts
into* RAM pressure, and the eviction entry point accounts for both at once:

```python
def free_memory(memory_required, device, keep_loaded=[],
                for_dynamic=False, pins_required=0, ram_required=0):   # :863
```

`get_free_memory` on a CPU device answers from
`psutil.virtual_memory().available` (`:1754`) -- a **system-wide** number, not
a ComfyUI-private one. That choice matters: anything else on the machine
eating RAM automatically shrinks what ComfyUI thinks it can offload, so the
honest shared measurement coordinates with the rest of the system for free.

And those `pins_required` bytes? They belong to the second real budget.

## The pin budget

To copy weights CPU→GPU at full PCIe speed, the source pages must be
**pinned** -- page-locked so the OS cannot swap them out. Pinned pages are
subtracted from the whole machine's flexibility, not just ComfyUI's, so an
unbounded pin pool starves everything else running.

ComfyUI runs an explicit budget for this: `MAX_PINNED_MEMORY` is derived from
total RAM (40% on Windows -- *"Windows limit is apparently 50%"* -- and up to
90% minus safety margins elsewhere, `:1585-1587`), `ensure_pin_budget(size)`
(`:714`) gates every new pin against it, and `free_pins` (`:695`) walks
eviction tiers when the budget is exceeded, with an emergency path keyed on
system-available RAM under Windows swap pressure (`:706`). `--high-ram`
disables the gate entirely (`:715-716`) -- and, less obviously, the same flag
also switches [the execution cache](#the-execution-caches) to classic mode
(`comfy/cli_args.py:285-286`): one flag, two effects.

This is real memory management -- budget, admission, eviction -- entirely
about RAM, and entirely separate from the VRAM ledger. Both ledgers, though,
steer by the same instrument.

## The number everything depends on

`get_free_memory(device)` (`:1748`) is the input to every decision above. On
CUDA (`:1785-1787`):

```python
mem_free_cuda, _ = torch.cuda.mem_get_info(dev)
mem_free_torch  = mem_reserved - mem_active     # torch's own reusable cache
mem_free_total  = mem_free_cuda + mem_free_torch
```

That second term is the **allocator cache**: PyTorch keeps freed VRAM in a
private reusable pool rather than returning it to the driver. ComfyUI counts
it as free -- correctly, for its own process -- and can flush it back to the
driver with `soft_empty_cache` (`:2045`). The reason this complicates
cross-process accounting is [Sharing one GPU](sharing-one-gpu.md).

**The first term is the honest number only on Linux.** `torch.cuda.mem_get_info`
asks the driver, and on Windows the driver answers *per process*: it reports
what **this process** may still commit, not what the card has free. Another
process can be using 13 GB and this number barely moves.

ComfyUI has no platform branch here -- it makes one unconditional call and
trusts it. That is not an oversight so much as an assumption that ComfyUI is
the only thing using the GPU. Which is exactly the assumption comfy-env breaks
by putting models in worker subprocesses.

## The execution caches

One large consumer remains -- the RAM you forgot about. After a workflow
finishes, ComfyUI keeps two caches so the next run can skip unchanged work
(`execution.py:132-149`, `comfy_execution/caching.py`):

- **`outputs`** -- every node's return values, keyed by a signature of the
  node's inputs. Images, latents, meshes: real tensors, held between runs.
- **`objects`** -- the node *instances* themselves, keyed by node id. This is
  where a V1 node's `self.`-stashed state lives (a loaded model a node cached
  on itself survives here between runs).

This is why RAM grows after a run completes and stays grown with zero models
loaded -- and it is retention policy, not the memory manager: nothing in
`model_management.py` can evict a cached output. The user-facing controls are
the cache flags (`comfy/cli_args.py:140-143`; the default is chosen in
`main.py:351-357`). Only the `outputs` cache varies by mode -- `objects` is a
`HierarchicalCache` in every mode except `--cache-none`
(`execution.py:135-149`):

| Flag | `outputs` cache |
|---|---|
| `--cache-ram [GB ...]` *(default)* | `RAMPressureCache` -- keep outputs until RAM headroom runs low |
| `--cache-classic` | `HierarchicalCache` -- keep outputs for everything still in the graph; **implied by `--high-ram`** (`cli_args.py:285-286`) |
| `--cache-lru N` | `LRUCache` -- keep the N most recently used node results |
| `--cache-none` | `NullCache` -- keep nothing; every node re-executes every run |

!!! note "Why comfy-env cares about #6"
    For an isolated node, the `objects` cache holds the parent-side **proxy**,
    while any `self.` state lives in the worker's real instance -- kept alive
    by the worker's own object cache for exactly this reason. And a cached
    `outputs` entry can hold shared-memory tensors that crossed the process
    boundary, which is what the consumed-ack lifetime protocol
    ([ADR-0032](adr/0032-shm-lifetime-consumed-ack.md)) exists to keep valid.

## What nothing manages

Completing the map with the memory ComfyUI cannot see or steer:

- **Mid-execution temporaries** beyond the reserved headroom -- plain PyTorch
  refcounting, gone when the tensor goes out of scope.
- **Non-PyTorch allocations** -- `cuMemAlloc`, TensorRT engines, NVENC,
  OpenGL: invisible to `torch.cuda` accounting entirely. Measured
  consequences in [Sharing one GPU](sharing-one-gpu.md#what-this-does-not-fix).
- **Other processes' VRAM** -- including, without comfy-env's proxies, a
  worker's models; that gap is the entire subject of
  [Sharing one GPU](sharing-one-gpu.md).

## Why comfy-env has to care

comfy-env runs node code in separate processes, so a worker's models are real
VRAM that ComfyUI's ledger cannot see. Everything in
[Sharing one GPU](sharing-one-gpu.md) -- the model proxies, the admission
arithmetic, the eviction bridge -- exists to put worker-held memory back into
the picture above, without forking any of it.

The short version: comfy-env registers a stand-in object into
`current_loaded_models` so upstream's eviction loop can evict a worker's model
the same way it evicts its own, and pre-compensates `memory_required` so the
feedback loop converges on the truth rather than on the parent's blind view.
The precise version is
[ADR-0036](adr/0036-mirroring-comfyui-memory-management.md).

## Cheat sheet

| Thing | One line |
|---|---|
| `current_loaded_models` | the list of what is resident, weakly held |
| `load_models_gpu` | "make room and load these" |
| `free_memory(n, dev)` | "get me `n` free bytes on `dev`" -- also takes `pins_required` / `ram_required` |
| `partially_load/unload` | move part of a model across the PCIe bus |
| `lowvram_model_memory` | the per-model byte budget |
| `0` / `0.1` | "everything" / "nothing" |
| `minimum_inference_memory()` | 0.8 GB + reserve, never touched |
| `get_free_memory` | the input to every decision; per-process on Windows |
| `MAX_PINNED_MEMORY` / `ensure_pin_budget` | the RAM budget for page-locked transfer staging |
| `outputs` / `objects` caches | the execution-side RAM: node results and node instances, kept between runs |