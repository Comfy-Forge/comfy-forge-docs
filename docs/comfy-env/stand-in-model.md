# The stand-in model

*One fake model per worker model, placed in ComfyUI's own list so its
eviction can reach another process. Why it exists, what it must answer,
how it is checked, and what it costs.*
{: .subtitle }

This page assumes [what survives isolation](memory-approach.md), which
assumes [ComfyUI's memory management](comfyui-memory.md).

## Why a fake model at all

When ComfyUI wants to load a model and the card is full, making room means
walking `current_loaded_models` and asking each entry to unload. An isolated
pack's models live in another process, so unless something represents them
in that list the host cannot evict them, cannot make space on a full card,
and a pack's memory cannot be reclaimed at all.

So comfy-env puts something in the list: one stand-in per worker model,
which forwards the unload over IPC to the worker that holds the weights.
That is why the Free button works on a pack's model, why the out of memory
handler reaches packs, and why a host load can evict a pack's model instead
of failing. It is also the single most fragile thing comfy-env does, because
ComfyUI reads whatever it likes off anything in that list.

`load_models_gpu` and `free_memory` call out to exactly three things:
entries in that list, the pinned memory helpers, and `logging`. There is no
callback, no event and no registry on either path. The operating system
cannot substitute: there is no push notification for device memory anywhere,
NVML's event API has no memory bit, CUDA has no callback, and VRAM is charged
to no cgroup. Host side precursors are too late; on ComfyUI's real loading
path host RSS leads the device allocation by 0.1 s. That search is closed,
and the stand-in stays until [upstream offers a hook](upstream-ask.md).

## What it must answer

Put an object in `current_loaded_models` and the memory manager reads
members off it, at times of its choosing, in the middle of eviction.
Eighteen of them, none declared anywhere upstream:

```
load_device          offload_device       parent               model
model_size           loaded_size          current_loaded_device
model_dtype          model_patches_to     model_patches_models
partially_load       partially_unload     detach
lowvram_patch_counter  is_dynamic         is_clone
clone_base_uuid      get_nested_additional_models
```

Fourteen appear as a literal `.model.<name>` in the memory manager, which a
grep can find. The other four (`load_device`, `parent`,
`model_patches_models`, `get_nested_additional_models`) are reached through
an alias or read off the incoming model, and `load_device` is the one
upstream reads most while a literal grep sees it zero times. By purpose:

| Group | Members |
|---|---|
| Identity and placement | `load_device`, `offload_device`, `parent`, `model`, `clone_base_uuid`, `is_clone` |
| Accounting | `model_size`, `loaded_size`, `current_loaded_device`, `model_dtype`, `lowvram_patch_counter` |
| Action | `partially_load`, `partially_unload`, `detach`, `model_patches_to`, `model_patches_models` |
| Mode | `is_dynamic`, `get_nested_additional_models` |

`is_dynamic` is the highest leverage member. Answering `False` keeps the
stand-in out of every pin path, out of the cast buffer reset, and out of the
dynamic model bypass in the eviction loop. That last exclusion is what makes
it evictable at all, and it is also the first of the
[three answers that are not true](model-stand-in-inaccuracies.md).

The stand-in declares that surface explicitly (`COMFY_SURFACE` in
`isolation/model_patcher.py`) and **does not inherit `ModelPatcher`**; a test
enforces it. Inheriting would silently import well over a hundred members
that are wrong for an object holding no weights, every one of which would
appear to work while returning nonsense. A duck type fails loudly on the
member it lacks: its `__getattr__` raises with a message naming the member
and pointing at `COMFY_SURFACE`. The reasoning is
[ADR-0035](adr/0035-duck-typed-model-proxy.md).

## How we know it still fits

A test greps upstream's `model_management.py` for every `.model.<name>`
access, follows the `model = entry.model` alias within a function, subtracts
the names that are not patcher members and the ones gated behind
`is_dynamic()`, and asserts the remainder is a subset of `COMFY_SURFACE`.
Upstream has no declared interface, so the test derives one from the source.

What it covers and what it does not: it runs weekly against ComfyUI master
in a job marked allowed to fail, so a red run opens an issue and publishing
continues. It reads one file; node code outside `model_management.py` also
borrows the list and the canary does not look there. And it catches a
**missing** member, while every defect found in this seam has been a
**wrong value** on a member the stand-in implements. comfy-env has been
public since 2026-04-25 and the stand-in has never raised on a user; every
defect so far was a wrong number, found by review.

## Why it is a compromise

It fails all three tests comfy-env sets for its own code.

**It is not stable.** It answers eighteen attributes of internals upstream
never promised to keep. The eviction loop grew a whole new branch when
comfy-aimdo landed; `loaded_size` was reimplemented for the paged patcher;
the pinned memory tuple layout it must not touch moved twice in one year.
None of those were breaking changes to anyone else, because none of it is
an interface.

**It is not correct.** Three of its answers are not true, each deliberately:
it says a paged model is not paged, its residency is the last receipt rather
than a reading, and on Linux its size is already counted in the host's free
figure. Each is priced in
[where the stand-in is inaccurate](model-stand-in-inaccuracies.md).

**It is not easily maintainable.** `contract.py` lists what comfy-env reads
off ComfyUI, checked once before the first worker spawns. Not one entry
describes what ComfyUI reads off comfy-env, which is the direction that
breaks; the weekly canary above is all that covers it, and it catches a
change only once the new ComfyUI is in front of us.

## What this seam costs, honestly

On a default install every host model is managed by the pager, and the
eviction loop has a bypass for those (*"don't actually unload dynamic
models for the sake of other dynamic models as that works on-demand"*),
while the stand-in reports itself non dynamic and does not get it. The
worker's model can therefore be the only entry upstream evicts, and it also
sorts first, because a fresh stand-in reports nothing already offloaded and
the lowest possible reference count.

Two things stop that being a complaint about upstream. The bypass is a
considered refusal, since evicting a paged model to make room for another
paged model is churn. And `for_dynamic` is a parameter: comfy-env's own
`free_memory` call leaves it `False`, so on the path comfy-env drives the
bypass does not fire and host models are fully evictable. Where the
asymmetry appears it is downstream of comfy-env's own choice to answer
`is_dynamic()` `False`, the cost of the safe answer rather than a tilt in
the field.

## The rows it carries

The eleven mechanisms in [the table](memory-approach.md) that pass through
the stand-in, with what each reads off it. Exposure is stable, fragile or
broke: stable means nothing reads the stand-in on that path, fragile means
the host reads internals off it that upstream has already changed once,
broke means a read on that path has taken comfy-env down before.

### 1. `free_memory` eviction ladder { #row-1 }

**What ComfyUI does.** `free_memory` eviction ladder: when the card is short, the host ranks its loaded models and asks them to leave until there is room. The rank is a four key sort (`mm.py`): most already offloaded first, then lowest `sys.getrefcount`, then smallest, and only as a final tiebreak the list index, which is newest first because `load_models_gpu` inserts at 0. A model it never listed is never asked.

**Today.** <span class="v v-yes">yes</span>: ComfyUI's own eviction loop reaches the stand-in we register for each worker model, and the worker unloads. It is the only path by which *upstream's own code* takes memory from another process. comfy-env has three more of its own that free memory the list cannot reach: an admission time ask to idle workers (`pool._ask_idle_workers`), a node boundary release (`pool._release_idle_workers`), and a pressure hook on the stand-in's own `partially_unload`, which is handed the exact shortfall and posts an ask to idle siblings without blocking this loop

**How exposed that leaves the stand-in.** <span class="v v-partial">fragile</span>: a pass reads `.device`, `.is_dead()`, `.model_offloaded_memory()`, `.model_memory()`, `sys.getrefcount(.model)` and `.model.is_dynamic()` on every candidate, then `.model_unload()` and `.model.model.__class__.__name__` on the ones it picks (the second inside an eagerly built log string, so it runs regardless of log level). `__eq__` is NOT read here: the `not in keep_loaded` test in `mm.py` runs against an empty list on every caller but `unload_model_and_clones`, and `x not in []` compares nothing. The every-load `__eq__` is row 2's. `.currently_used` is *written* here, never read; its only reader is `loaded_models()`. The loop body grew a dynamic branch when aimdo landed, we don't know if that might happen again in the future

**With the upstream hook.** <span class="v v-yes">yes</span>

### 9. `unload_all_models` and the Free button { #row-9 }

**What ComfyUI does.** `unload_all_models` and the Free button: an eviction ask for an absurd number (1e30) sent to every listed model between prompts.

**Today.** <span class="v v-yes">yes</span>: the button reaches the stand-in and the worker releases. It does not arrive as 1e30, though: `model_unload` compares the ask against `loaded_size()`, 1e30 loses, and the stand-in is called with `detach(True)` (`mm.py`). Only a bare list entry implementing `model_unload` itself ever sees the sentinel

**How exposed that leaves the stand-in.** <span class="v v-yes">stable</span>: a `loaded_size()` read and one method call. The `unpatch_all` argument is honoured as of 2026-09-06, which only matters on row 38's path

**With the upstream hook.** <span class="v v-yes">yes</span>

### 12. `LoadedModel` size questions { #row-12 }

**What ComfyUI does.** `LoadedModel` size questions: how the host asks each listed model how big it is and how much is on the card. The legacy count reads 0 for a paged model; only the pager's own count is right.

**Today.** <span class="v v-partial">partial</span>: the stand-in answers all three, and the residency it answers with is measured correctly, by the worker calling its own real `loaded_size()` (`_resident_of` in `_persistent_worker`), which is right on both paths because the worker uses whichever implementation it has. What is partial is freshness. `loaded_size()` returns the last echo, not a live reading, and under aimdo the pager faults pages in and out between echoes with no message. So the honest gloss is "how much was on the card when we last talked". The lopsidedness is on a different number than you would guess: `apply_echo` writes the ledger `loaded_size()` answers with unconditionally, up or down, in every flag state. What may only rise mid-call is comfy-env's own admission peak, which ComfyUI never reads, because an idle worker cannot re-fault (faults are synchronous worker Python) and a busy one can

**How exposed that leaves the stand-in.** <span class="v v-partial">fragile</span>: `model_size`, `loaded_size`, `current_loaded_device`, and `loaded_size` means two different things upstream. Legacy (`mp.py`) returns `model_loaded_weight_memory`, which is 0 for a paged model; dynamic (`mp.py`) returns `vbar.loaded_size()` plus that. The stand-in must pick one and answer one number, and the number is not decorative: `model_offloaded_memory()` is `model_size() - loaded_size()`, the PRIMARY sort key of the eviction loop. If upstream shifts what `loaded_size` means, nothing raises. The stand-in sorts into the wrong position, evicted when it should not be or never picked when the card is full. A number cannot throw, which makes this the one surface where drift is silent rather than loud

**With the upstream hook.** <span class="v v-yes">yes</span>

### 17. `model_unload`, partial versus full { #row-17 }

**What ComfyUI does.** `model_unload` partial versus full: ask a model to shrink by the shortfall, else throw it out entirely. Returns True even if nothing was freed.

**Today.** <span class="v v-yes">yes</span>: the stand-in implements `loaded_size`, `partially_unload` returning bytes actually moved, and `detach`. A short return escalates to detach, which is upstream's own contract

**How exposed that leaves the stand-in.** <span class="v v-partial">fragile</span>: `partially_unload` has a return contract (a short answer escalates to `detach`) and a second dynamic implementation via `vbar_free_memory`, the return contract since 2024-08, the dynamic implementation 2026-01

**With the upstream hook.** <span class="v v-yes">yes</span>

### 22. `cleanup_models` prune { #row-22 }

**What ComfyUI does.** `cleanup_models` prune: whenever a model object dies, the host erases every list entry whose `real_model()` is gone.

**Today.** <span class="v v-yes">yes</span>, nothing to do; via the registered stand-in, the fake's wrapper carries a `real_model` weakref

**How exposed that leaves the stand-in.** <span class="v v-partial">fragile</span>: the prune assumes every entry went through `model_load`; comfy-env sets `real_model` and `model_finalizer` by hand (`pool._insert_loaded_model`), matching internals upstream never promised. Getting it wrong is not a quiet wrong number: `cleanup_models` and `is_dead` CALL `real_model()`, and `cleanup_models_gc` is the first statement of both `free_memory` and `load_models_gpu`, so a single unplanted entry raises `TypeError` on every load and every free for the life of the process

**With the upstream hook.** <span class="v v-yes">yes</span>

### 26. `model_load` and the finalizer tripwire { #row-26 }

**What ComfyUI does.** `model_load` and the finalizer tripwire: loading a listed model also plants a weakref that erases its entry when the model dies.

**Today.** <span class="v v-yes">yes</span>, and not because the host did it: comfy-env inserts the entry directly rather than through `load_models_gpu`, so it plants `real_model` and `model_finalizer` by hand (`pool._insert_loaded_model`) to do upstream's job for it. The host only reaches `model_load` on a stand-in through row 38's leak

**How exposed that leaves the stand-in.** <span class="v v-partial">fragile</span>: `.model.model` must be a stable weakref-able object forever; `model_patches_to`, `model_dtype` and `partially_load` are all read on the way, which is row 11's surface arriving by this path

**With the upstream hook.** <span class="v v-yes">yes</span>

### 28. Entry identity and the dead-entry sweep { #row-28 }

**What ComfyUI does.** Entry identity and the dead-entry sweep: the list holds a weak grip on each model; if the owner vanishes while weights remain, the host runs a full garbage sweep.

**Today.** <span class="v v-partial">partial</span>: effect yes, visibility no; comfy-env must hold the fake's patcher strongly, and the consequence of not doing so is quieter than a sweep. `real_model` is a weakref to the fake's inner `SubprocessModel`, whose only strong reference is the patcher itself, so both die together, `is_dead()` stays False, and `cleanup_models` simply prunes the entry with no signal at all

**How exposed that leaves the stand-in.** <span class="v v-partial">fragile</span>: `__eq__` is `.model` identity (2023-08), `is_dead` reads the weakref and `_switch_parent` rebinds to `.parent` when a clone dies (both 2024-12). `_switch_parent` cannot fire on a stand-in at all: it is armed only when `model.parent` is not None, and the fake sets it None

**With the upstream hook.** <span class="v v-yes">yes</span>

### 29. Clone dedup and `is_clone` { #row-29 }

**What ComfyUI does.** Clone dedup and `is_clone` probing: before every load the host checks whether this model or a twin is already listed and throws out the twin.

**Today.** <span class="v v-yes">yes</span> for the case that matters: upstream runs `is_clone` on the INCOMING host model with the fake as its argument, and answers False, so a worker model is never mistaken for a twin of a host one. It is not unconditional. The fake's own `is_clone` answers True for itself, so a stand-in arriving as an incoming model matches its own listed entry and is popped and detached. That was expensive until `detach` learned to honour `unpatch_all=False` on 2026-09-06, and unreachable since the same day, when the leak that delivered it closed

**How exposed that leaves the stand-in.** <span class="v v-no">broke</span>: only one of the three reads runs on the fake. `__eq__` does, via `index()`, on every load. `is_clone` runs on the *incoming* model with the fake as its argument, so what reads the fake is upstream's `hasattr(other, 'model')`. `model_patches_models()` never touches a list entry. Any new read in this function still lands here

**With the upstream hook.** <span class="v v-yes">yes</span>

### 30. `is_dynamic` gate and the per-node ledger walk { #row-30 }

**What ComfyUI does.** `is_dynamic` gate and the per-node ledger walk: a yes or no tag deciding whether the host digs into a model's pinned-RAM internals after every step. The walk itself is gated on `aimdo_enabled`, so in the legacy cells it does not run at all.

**Today.** <span class="v v-yes">yes</span>: the worker resets its own; via the registered stand-in, the fake answers `is_dynamic()` False and nothing deeper is read

**How exposed that leaves the stand-in.** <span class="v v-no">broke</span>: answering True means faking `dynamic_pins`, a dict of four six element positional tuples plus four scalar flags, of which `reset_cast_buffers` rebuilds two. It landed 2026-05-21 and the layout has moved twice since: four element tuples became six on 05-31, and 07-29 added the two `-loaded` subsets and two more flags (05-25 relocated the construction without changing a field)

**With the upstream hook.** <span class="v v-yes">yes</span>

### 38. `loaded_models()` leak into node code { #row-38 }

**What ComfyUI does.** `loaded_models()` leak into node code: controlnet and a few extras nodes borrow the list and hand it straight back to `load_models_gpu`, so anything in it is treated as a real model.

**Today.** <span class="v v-yes">yes</span> as of 2026-09-06: every stand-in is now registered with `currently_used` False, and that flag is the only thing `loaded_models(only_currently_used=True)` filters on, so six of the seven callers no longer see a worker model at all. Only `multigpu` reads the list unfiltered, and it never re-loads. What follows is what the leak cost while it was open, and what still applies to that seventh reader. Controlnet and three extras nodes do hand the stand-in back into `load_models_gpu`, but `model_memory_required` asks for the offloaded remainder of a model already on the target device, and a resident worker model has none, so it adds zero. The cost was never a mere re-fault, though. Before admission, the clone dedup loop runs, the stand-in's own `is_clone` matches ITSELF, and the entry is popped and detached: a full worker offload, then a full reload. That is fixed too, by honouring `unpatch_all=False` the way upstream does. `multigpu` reads `load_device` and `clone_base_uuid` and would call `clone()`, which the stand-in raises on, but it never gets there: the `clone_base_uuid` mismatch filters the fake out first, with only the `is_multigpu_base_clone` test between that check and the `clone()` call

**How exposed that leaves the stand-in.** <span class="v v-no">broke</span>: seven call sites in five files outside `model_management.py` read the list, and node code can read anything. Only `multigpu.py` reads it unfiltered

**With the upstream hook.** <span class="v v-yes">yes</span>

### 40. `unload_model_and_clones` { #row-40 }

**What ComfyUI does.** `unload_model_and_clones`: throw out one model and its copies but keep everything else, using the same 1e30 as the button.

**Today.** <span class="v v-yes">yes</span>: the stand-in's `clone_base_uuid` is a private sentinel object, so it can never equal a uuid and never lands in the freed set. It was `None` until 2026-09-06, which worked for exactly one reason: upstream assigns `uuid.uuid4()` in `ModelPatcher.__init__`, so no real target carries `None`. Latent, not live, because the comparison is target against entry and never entry against entry. Shipping on somebody else's constructor was the wrong bet, and one `object()` retires it

**How exposed that leaves the stand-in.** <span class="v v-partial">fragile</span>: `clone_base_uuid` is an internal identity two callers compare directly

**With the upstream hook.** <span class="v v-yes">yes</span>

Three rows carry a `broke` exposure: 29 (clone dedup), 30 (the `is_dynamic`
gate and the per node pin walk) and 38 (the `loaded_models()` leak into node
code). Those are the reads that run against the stand-in and have moved
under it before. Everything else comfy-env does is reading values and
publishing one number.

## See also

- [Where the stand-in is inaccurate](model-stand-in-inaccuracies.md), the three untrue answers priced one at a time
- [What only upstream can fix](upstream-ask.md), the hook that retires the stand-in
- [ADR-0035](adr/0035-duck-typed-model-proxy.md), duck type over subclass; [ADR-0038](adr/0038-the-memory-floor.md), the decision it belongs to
