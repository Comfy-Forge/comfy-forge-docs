# ADR-0036: Mirroring ComfyUI's memory manager across the process boundary

**Status:** accepted (2026-08-16); **superseded 2026-09-04** by
[ADR-0038](0038-the-memory-floor.md). Amends
[ADR-0025](0025-vram-co-management.md), and superseded the mechanism —
though not the goal — of [ADR-0034](0034-admission-by-arithmetic.md).

!!! warning "Mirroring was the wrong shape"

    Part 1 remains the reference description of upstream's memory manager
    and is worth reading on its own. The decision it reached, mirroring that
    manager across the boundary, is not what shipped: comfy-env now
    publishes one number into a knob ComfyUI already reads and lets
    upstream's manager run unmodified. Mirroring meant re-deriving upstream
    arithmetic, which drifts silently every time upstream edits a formula.

Part 1 describes upstream's memory manager in full, because ComfyUI ships no
documentation of it and no decision here is intelligible without it. Part 2
describes what a process boundary breaks. The decision follows.

**Baseline:** ComfyUI `0.32.0`, commit `b323a34` (2026-08-12). Line numbers
refer to `comfy/model_management.py` unless stated otherwise. Measurements
were taken on an RTX 4060 Ti 16 GB, Windows 11, driver 581.57, WDDM,
torch 2.8.0+cu128. Everything here is read from source; nothing is inferred
from behaviour unless labelled as a measurement.

---

# Part 1 — How ComfyUI manages memory

## 1.1 What it tracks

One module-level list:

```python
current_loaded_models = []
```

Entries are `LoadedModel` objects, each wrapping a `ModelPatcher`. Per model,
the manager can ask:

| Question | Method | Notes |
|---|---|---|
| How big is it? | `model_size()` | full weight bytes |
| How much is on the GPU *now*? | `loaded_size()` | 0 … `model_size()` — **not** a boolean |
| How much is not? | `model_offloaded_memory()` (`:773`) | `model_size() - loaded_size()` |
| Where does it live / go? | `load_device`, `offload_device` | offload target is normally CPU RAM |
| Is it in use this moment? | `currently_used` | set `True` on load, cleared for every eviction candidate (`:876`) |

Not stored, but read off a model during eviction: **`sys.getrefcount(model)`**
— a plain builtin evaluated at `:875` while building the candidate list, used
as the second sort key (§1.5). Fewer live references sorts earlier, i.e. a
model nothing else is holding is evicted sooner. Note this counts references
to the *patcher*, so for an object type that is only ever held by the ledger
it is a constant and contributes nothing to the ordering.

**Residency is fractional.** A model can be 30% resident. That is what
"lowvram" means: keep some weights on the card, stream the rest per layer.

There is no allocator here, no arena, no ownership graph. A list, some sizes,
and two globals re-read on demand.

### The ledger holds weak references

Python frees an object when the last reference to it disappears. A **weak
reference** points at an object *without counting* — it lets you observe
something without keeping it alive. Call it like a function to get the object
back, and once the object has been freed you get `None` instead.

`current_loaded_models` is a module-level global that lives for the process.
If it held ordinary (strong) references, **every model ever loaded would stay
in memory forever**, because the list alone would be enough to keep it alive.
So the ledger holds weak ones:

```python
self._model = weakref.ref(model)      # :751 — the ModelPatcher
...
@property
def model(self):
    return self._model()              # :762-764 — deref; None once collected
```

The ledger is therefore an *observer*, not an owner. What actually keeps a
model alive is whatever is using it — the workflow, a node's cached output.
When the last of those goes away, the model is freed even though it is still
listed.

**Finalizers do the cleanup.** `weakref.finalize(obj, fn)` registers `fn` to
run at the moment `obj` is freed. There are two:

- `weakref.finalize(model, self._switch_parent)` (`:754`) — if a patcher dies
  but has a parent, the entry re-points at the parent rather than going dead.
- `weakref.finalize(real_model, cleanup_models)` (`:796`) — when the actual
  `nn.Module` is freed, the ledger prunes itself.

**`is_dead()` detects the leak case** (`:827-828`):

```python
return self.real_model() is not None and self.model is None
```

The inner model is still alive but its patcher was collected — meaning
something outside ComfyUI is holding weights it can no longer manage.
`cleanup_models_gc()` scans for this, forces a `gc.collect()` and a cache
flush, and logs *"WARNING, memory leak with model …"* if the entry survives.
Dead entries are also excluded from eviction candidates (`:874`).

**What this entails.** Three consequences that shape everything downstream:

1. **The ledger cannot cause a leak, but it can go stale.** Entries vanish on
   their own schedule — including *during* an eviction walk, since freeing a
   model runs `cleanup_models` synchronously, which mutates the very list
   being iterated.
2. **Two dead entries compare equal.** `__eq__` is
   `self.model is other.model` (`:820-821`) — identity on the *patcher*, and
   both sides deref. Once collected, both are `None`, and `None is None` is
   `True`. Any code using `in` or `not in` against a list of `LoadedModel`s —
   `keep_loaded` at `:874`, for instance — will match a collected entry
   against an unrelated one.
3. **Anything standing in for a model must be weakref-able**, which rules out
   `__slots__`-free tricks and objects that override `__eq__` carelessly.

## 1.2 The VRAM state machine

```python
class VRAMState(Enum):
    DISABLED = 0    # no VRAM: never move models to GPU
    NO_VRAM = 1     # very low VRAM: every saving enabled
    LOW_VRAM = 2
    NORMAL_VRAM = 3
    HIGH_VRAM = 4
    SHARED = 5      # no dedicated VRAM (unified memory)
```

Selected at import (`:558-579`) from `--lowvram` / `--novram` / `--highvram`
/ `--gpu-only` / `--cpu`, defaulting to `NORMAL_VRAM`. The state matters
mainly at load time: `NO_VRAM` forces the smallest possible resident
fraction, `HIGH_VRAM` skips offloading, and `LOW_VRAM`/`NORMAL_VRAM` both go
through the streaming calculation in §1.6.

## 1.3 Measuring free memory — where the OS distinction lives

`get_free_memory(dev, torch_free_too=False)` (`:1739`) is the single input to
every decision below.

**CPU or MPS** (`:1744-1746`):

```python
mem_free_total = psutil.virtual_memory().available
```

System-wide, honest, shared between processes.

**CUDA** (`:1774-1778`):

```python
mem_free_cuda, _  = torch.cuda.mem_get_info(dev)
mem_free_torch    = mem_reserved - mem_active     # torch's own free cache
mem_free_total    = mem_free_cuda + mem_free_torch
```

Both terms are per-process. `mem_free_torch` is this process's allocator
cache — blocks torch holds but is not using; counting them as free is correct
since torch reuses them without asking the driver. `mem_free_cuda` comes from
`mem_get_info`, and that is where the platforms diverge:

| Platform | What `mem_get_info` reports |
|---|---|
| Linux | free memory on the device, globally |
| Windows / WDDM | **this process's VidMm commitment budget** |

On Windows it is not device free memory. It debits 1:1 for the calling
process's own allocations, but is blind to other processes until the video
memory manager re-partitions budgets — and **re-partitioning is triggered by
process and context lifecycle events, not by memory pressure**.

Measured: one sibling process grew from 256 MB to 14,336 MB, taking the card
to 1,331 MB physically free, while the observer's reported free never moved
from 15,221 MB. A *third* process starting and allocating 50 MB did move it.

Two biases follow, both reproduced: while the budget is pinned, reported free
sits **583 MB below** true device free (four reproductions, independent of the
caller's own usage); just after re-partitioning it sits roughly **533 MB
above** it.

### The failure mode this creates

On Linux, exhausting VRAM raises an out-of-memory error — loud, catchable,
attributable. On Windows it does not fail. WDDM demand-pages some process's
GPU allocations out to system RAM.

Measured: a process allocated 8 GiB with 109 MB physically free, sustained
143 GB/s, and never saw an error — while a *sibling* went from 237 GB/s to
**4.5 GB/s**, then recovered to 248 GB/s on a second pass as its pages
faulted back in.

The cost lands on a bystander, and there is no signal anywhere: no exception,
no counter, invisible to the allocator, to `mem_get_info`, and to NVML —
which returns `NOT_AVAILABLE` for per-process memory on every PID under WDDM,
including the caller's own.

Upstream knows the symptom without naming the mechanism:

```python
EXTRA_RESERVED_VRAM = 400 * 1024 * 1024
if WINDOWS:
    EXTRA_RESERVED_VRAM = 600 * 1024 * 1024  # Windows is higher because of the shared vram issue
    if total_vram > (15 * 1024):             # more on 16GB+ cards
        EXTRA_RESERVED_VRAM += 100 * 1024 * 1024
```

`:847-851`. Overridable with `--reserve-vram`. It treats the problem with a
constant rather than a measurement.

## 1.4 Reserves

```python
def minimum_inference_memory():
    return (1024 * 1024 * 1024) * 0.8 + extra_reserved_memory()   # :860-861
```

0.8 GB of working room plus the reserve above. On a 16 GB Windows card that
is 0.8 + 0.7 = **1.5 GB** held back from weights for activations and
workspaces.

`MIN_WEIGHT_MEMORY_RATIO` is `0.4`, but **`0.0` on NVIDIA** (`:454-456`),
which removes the "keep at least 40% of weights resident" floor from §1.6.

## 1.5 Eviction

```python
def free_memory(memory_required, device, keep_loaded=[],
                for_dynamic=False, pins_required=0, ram_required=0):   # :863
```

**Build the candidate list** (`:871-877`) — everything on this device not in
`keep_loaded` and not `is_dead()`, clearing `currently_used` as a side
effect. Sort key:

```python
(-model_offloaded_memory(), sys.getrefcount(model), model_memory(), index)
```

Ascending, so **the most already-offloaded model is evicted first**, being
cheapest to finish evicting.

**Walk it** (`:879-894`):

```python
for x in can_unload_sorted:
    memory_to_free = memory_required - get_free_memory(device)   # :883
    if memory_to_free > 0 and current_loaded_models[i].model_unload(memory_to_free):
        unloaded_model.append(i)
```

The target is **recomputed on every iteration**, and the loop acts only while
it is positive. That re-measurement is the whole design: each eviction raises
free memory, shrinking the shortfall, until the guard stops the walk. It
evicts the minimum rather than everything.

`model_unload` (`:806-815`) escalates:

```python
if memory_to_free < self.model.loaded_size():
    freed = self.model.partially_unload(self.model.offload_device, memory_to_free)
    if freed >= memory_to_free:
        return False          # enough — stays registered, just smaller
self.model.detach(unpatch_weights)
return True                   # full unload — caller pops it from the list
```

Note the guard: when `memory_to_free >= loaded_size()` the partial path is
skipped entirely. A model with `loaded_size() == 0` therefore always takes
the detach path and is **popped while freeing nothing**.

Returning True removes the entry from `current_loaded_models` (`:893-894`),
and **nothing re-adds it** — re-registration happens only through
`load_models_gpu`.

`unload_all_models()` is `free_memory(1e30, device)` (`:2054-2056`), reached
from OOM recovery (`execution.py:645`), from `--disable-smart-memory` after
every prompt (`execution.py:837`), and from the "free memory" button.

## 1.6 Loading

`load_models_gpu(models, memory_required=0, force_patch_weights=False, minimum_memory_required=None, force_full_load=False)` (`:934`).

**Budget:**

```python
inference_memory = minimum_inference_memory()
extra_mem = max(inference_memory, memory_required + extra_reserved_memory())
```

**Dedup and expand** — order-preserving, includes `model_patches_models()`.

**Re-register already-loaded models.** For each model being loaded, every
entry whose patcher `is_clone` of it is popped, `detach(unpatch_all=False)`'d,
and re-inserted (`:951-959`). This is bookkeeping, not an unload:
`ModelPatcher.detach(unpatch_all=False)` skips `unpatch_model` entirely
(`model_patcher.py:1295-1299`), so **weights stay on the GPU**.

**Free room** (`:969-974`):

```python
free_memory(total_memory_required[device] * 1.1 + extra_mem, device,
            for_dynamic=free_for_dynamic,
            pins_required=total_pins_required.get(device, 0))
```

`model_memory_required(device)` (`:776-780`) asks only for the *offloaded*
portion when the model is already on the target device — a partially resident
model does not re-request its whole size.

**Decide the resident fraction** (`:988-1002`):

```python
lowvram_model_memory = max(0,
    (current_free_mem - minimum_memory_required),
    min(current_free_mem * MIN_WEIGHT_MEMORY_RATIO,
        current_free_mem - minimum_inference_memory()))
lowvram_model_memory -= loaded_memory
if lowvram_model_memory == 0:
    lowvram_model_memory = 0.1
if vram_set_state == VRAMState.NO_VRAM:
    lowvram_model_memory = 0.1
```

**`0.1` is a sentinel meaning "load essentially nothing"** — set when the
computed budget is zero, and unconditionally under `NO_VRAM`.

**Load** (`:782-790`):

```python
use_more_vram = lowvram_model_memory
if use_more_vram == 0:
    use_more_vram = 1e32          # 0 means "no limit"
self.model_use_more_vram(use_more_vram, ...)   # -> partially_load(device, extra_memory)
```

So `partially_load` receives either `1e32` (everything), a real byte budget,
or `0.1` (nothing). **It never receives `0`.**

## 1.7 Host RAM

Eviction does not delete a model — it moves it to `offload_device`, i.e. RAM.
VRAM pressure becomes RAM pressure, and there is a second budget for it;
`free_memory` accepts `ram_required` and `pins_required` alongside
`memory_required`.

**Pinned memory** is page-locked host memory the OS may not swap, required
for DMA transfers that skip a bounce buffer and can overlap with compute.
On by default for NVIDIA and AMD, applied with `cudaHostRegister` to tensors
that already exist (`:1626`, `pinned_memory.py:61,106`) — so
`MAX_PINNED_MEMORY` is a **ceiling with a running total**, not an up-front
reservation.

```python
if WINDOWS:
    MAX_PINNED_MEMORY = ram * 0.40   # "Windows limit is apparently 50%"
else:
    MAX_PINNED_MEMORY = max(ram * 0.40,
                            min(ram * 0.90, ram - 4GB,
                                ram + get_disk_swap_total() - 16GB))
```

`:1575-1578`. The Linux branch folds swap into the budget;
`get_disk_swap_total()` reads `/proc/swaps` and returns 0 anywhere else.
Release policy is the mirror image (`:701-711`): non-Windows frees pins on
*any* pressure, Windows only below 512 MB available or at ≥5% swap usage.
Disable with `--disable-pinned-memory`.

## 1.8 The dynamic path

Models answering `is_dynamic() → True` use a separate just-in-time loader:
weights fault in on use rather than loading up front, backed by uncommitted
file-backed mappings the OS can reclaim. `is_dynamic()` gates it throughout —
`:650`, `:884-888`, `:934`, `:966`, `:1006`, `:1430` — including a branch
inside the eviction loop that declines to unload dynamic models on behalf of
other dynamic models.

Its OS distinction is about host RAM, not VRAM: on Windows it shows up as
high apparent RAM usage that Windows reclaims on demand, on Linux as low
usage with the remainder counted as disk cache. See
[Dynamic VRAM in ComfyUI](https://blog.comfy.org/p/dynamic-vram-in-comfyui-saving-local).

## 1.9 Cache flushing

```python
def soft_empty_cache(force=False):
    ...
    elif torch.cuda.is_available():
        torch.cuda.synchronize()
        torch.cuda.empty_cache()
        torch.cuda.ipc_collect()
```

**The `force` argument is ignored on the CUDA branch** — there is no cheap
mode. `free_memory` calls it once *after* its loop (`:900-906`), never
before.

Measured: `empty_cache()` does return memory to the driver under
`backend:cudaMallocAsync` (NVML used 2,364 → 316 MB); marginal cost over an
already-pending sync is ~0.02 ms, and releasing 2 GiB costs ~12-15 ms.
Flushing does **not** change `get_free_memory` — it moves bytes from
`mem_free_torch` into `mem_free_cuda`, and the sum is invariant.

## 1.10 Flags

| Flag | Effect |
|---|---|
| `--lowvram` / `--novram` / `--highvram` / `--gpu-only` / `--cpu` | pick `VRAMState`; mutually exclusive |
| `--reserve-vram GB` | overrides `EXTRA_RESERVED_VRAM` |
| `--disable-smart-memory` | skips the eviction-target recompute (`:882`) and unloads everything after each prompt |
| `--disable-pinned-memory` | sets `MAX_PINNED_MEMORY = -1` |
| `--cache-ram` | RAM-pressure cache thresholds |
| `--fast-disk` | prefer disk-backed offload over unpinned RAM |

## 1.11 Where this came from

ComfyUI has no `docs/` directory and no document describing any of the above;
`docs.comfy.org` covers flags rather than mechanism. Supporting material:

- [Dynamic VRAM in ComfyUI](https://blog.comfy.org/p/dynamic-vram-in-comfyui-saving-local)
  — best first-party writing; covers §1.8 and the RAM-side OS distinction.
- [NVFP4, async offload and pinned memory](https://blog.comfy.org/p/new-comfyui-optimizations-for-nvidia)
  — why §1.7 is on by default.
- [Startup flags](https://docs.comfy.org/development/comfyui-server/startup-flags)
  — reference for §1.10.
- [DeepWiki: Memory and Device Management](https://deepwiki.com/Comfy-Org/ComfyUI/2.6-memory-and-device-management)
  — machine-generated from source; orientation only.
- [PR #11845](https://github.com/Comfy-Org/ComfyUI/pull/11845) — proposes
  reading WDDM's target VRAM figure *"rather than using the pytorch/Cuda
  stack reported numbers"*, i.e. the §1.3 diagnosis. **Status unconfirmed:**
  no DXGI or WDDM call exists anywhere in the `b323a34` tree, so it is not
  what we build against today.

---

# Part 2 — What the process boundary breaks

comfy-env inserts `SubprocessModelPatcher` proxies into
`current_loaded_models` so worker-resident models participate in §1.5's loop.
Four things break, independently of each other:

1. **The feedback signal is severed — on Windows.** When the loop evicts a
   worker model, real VRAM is freed in another process. The parent's
   `mem_free_cuda` term does not move (§1.3). The loop cannot observe its own
   progress and keeps evicting. On Linux `mem_get_info` is device-wide, the
   signal survives, and this failure does not occur.
2. **The admission target is computed from a number that is not device-free.**
   Same root cause, at load time rather than during the loop.
3. **The proxy does not honour its arguments.** `detach(unpatch_all=False)` is
   bookkeeping upstream (§1.6) and is called on *every* already-loaded model;
   the proxy treated it as a full unload, producing a GPU→CPU→GPU round trip
   where upstream intended a no-op. Symmetrically, `partially_load` received
   the `0.1` sentinel meaning "load almost nothing" and loaded everything —
   precisely when the card is full.
4. **Registration is one-shot.** Upstream pops an entry whenever
   `model_unload` returns True and never re-adds it (§1.5). comfy-env skipped
   ids already known (`pool.py:575`) and the worker deduped on `id(module)`
   for the process lifetime, so a popped proxy could never come back — the
   VRAM stayed resident, invisible, and unevictable. §1.5's sort order makes
   this fire early and often, because a zero-resident proxy sorts *first* and
   is popped having freed nothing.

Two facts from Part 1 constrain any fix: over-admission on WDDM harms an
unobservable third party rather than failing, and NVML per-process accounting
is unavailable there, so exact attribution is off the table.

---

# Part 3 — Decision

> **Worker models stay registered in ComfyUI's ledger, and comfy-env takes
> over exactly two things a process boundary makes impossible for upstream:
> the eviction *target*, and the choice of which *worker* model to evict.**
> Everything else — when to evict, which host models to evict, the escalation
> ladder, the load path — remains upstream's.
>
> Three rules make that work: the proxy obeys its arguments exactly; the
> target is a change of variables, never an estimate; and registration is
> reconciliation, not a one-shot event.

## Decision detail

**Proxies stay in `current_loaded_models`.** Registration is not only a
subscription to eviction; it also subscribes worker models to
`unload_all_models` — OOM recovery, `--disable-smart-memory`'s
after-every-prompt unload, and the free-memory button (§1.5). Leaving the
ledger would silently break all three, and restoring them would mean wrapping
three upstream functions where
[ADR-0034](0034-admission-by-arithmetic.md) already judged one too many.

**The proxy obeys its arguments exactly.** `detach(unpatch_all=False)` does
nothing and sends no IPC. `partially_load` never inflates a small budget into
a full load. The general rule, which the
[ADR-0035](0035-duck-typed-model-proxy.md) surface audit missed by checking
*which* members upstream touches rather than *what it passes them*: **an
argument is part of the contract.**

**Eviction runs in two phases.**

*Phase one* — comfy-env evicts worker models itself, from its own ledger,
re-measuring true device free between steps so it stops at the minimum. This
is the only place a sensible policy can be expressed: comfy-env knows which
worker is idle and which ran last, while §1.5's sort key is offload fraction,
refcount and size — and refcount is a constant for a proxy object.

*Phase two* — the residual goes to `mm.free_memory(..., keep_loaded=<worker
LoadedModels>)`. `keep_loaded` is applied before the candidate list is built
(`:874`), so every remaining victim is parent-local, every eviction moves the
parent's own numbers, and the feedback loop works again.

`keep_loaded` is therefore **load-bearing on Windows and policy-only on
Linux.** Membership uses `LoadedModel.__eq__`, which dereferences a weakref
(§1.1) — so the list must hold strong references to the patchers, or a
collected patcher makes `None is None` keep an unrelated model.

**The target is a change of variables, not an estimate.** Given that upstream
computes `memory_required - get_free_memory(device)`, pass:

```
memory_required = need + (blind_free − true_free)
```

`blind_free` cancels, and upstream targets `need − true_free` regardless of
what its own measurement says. This is exact in both WDDM regimes — when the
budget is pinned the term is large; when VidMm re-partitions the term
collapses *because the blind number became honest*; the product is the same
either way. It is not a correction factor and must not be tuned.

Two things break the identity and are forbidden: **clamping the term at zero**
(it is legitimately negative while `blind` sits below `true`, which per §1.3
is the idle case), and **letting the parent's own allocator cache into it**
(subtract it via `torch_free_too=True` rather than by flushing, which per
§1.9 would not move the number anyway).

**NVML is the only device-wide truth, read in-process** via `ctypes` into
`nvml.dll` / `libnvidia-ml.so.1` — no pip dependency, and 0.002 ms against
the 35 ms of shelling out to `nvidia-smi`, twice per request. Resolve the
device by UUID, not index, since NVML's enumeration and CUDA's ordinals
disagree. With no NVIDIA driver there is no contention to arbitrate, and we
degrade to upstream's own semantics.

**Registration is reconciliation.** On each drain: create proxies that are
missing, refresh residency from worker telemetry, and **re-insert a
`LoadedModel` for any live resident patcher upstream has popped.** The worker
announces a module whenever its residency transitions, not once per
`id(module)`.

**Worker telemetry replaces guessed constants.** The worker reports
instantaneous `torch.cuda.memory_reserved()` — never the high-water mark,
measured transiently at 15.2 GB — plus a per-process floor measured once at
startup after warm-up. This is a *floor*, not a truth source: it is blind to
allocations made outside torch (measured, 1,536 MB via `cuMemAlloc` moved the
accounting gap by exactly that and left `memory_reserved()` unchanged), which
is why NVML remains primary.

**Errors are deliberately one-sided.** Because over-admission harms an
unobservable third party while under-admission costs a bounded, attributable
reload, headroom is biased high. Concretely this restores upstream's `× 1.1`
rather than the `1.02` point estimate, and deletes the flat per-worker
constant that charged a CPU-only worker for a CUDA context it never created.

**What is deliberately not mirrored: host RAM.** The proxy answers
`is_dynamic() → False`, excluding it from every pin and RAM-eviction path,
because those paths assume a real patcher holding real weights. This is safer
than the VRAM equivalent for a structural reason worth recording: per §1.7,
`ensure_pin_budget` measures against `psutil.virtual_memory().available`,
which is system-wide and already counts worker RAM, so ComfyUI backs off on
its own. The residue is that it can never *ask* a worker to release host
memory. Failing toward under-pinning is the safe direction.

## Order of adoption

Each step ships alone and leaves the tree better than it found it.

1. Registration and reconciliation — nothing else holds without it.
2. `detach(unpatch_all=False)` as a no-op, bundled with a real worker
   generation check (the existing `_worker_generation` field is written and
   never read, and `is_alive()` is true for a *respawned* worker whose model
   ids restart from zero).
3. Drop the `partially_load` clamp.
4. Fix the measurement: unclamp the term, subtract the torch cache
   arithmetically.
5. In-process NVML by UUID.
6. Make the drift canary actually run — it has never executed outside one
   developer's machine.
7. Two-phase eviction with `keep_loaded`.
8. Worker telemetry; delete the two constants.

## Alternatives rejected

- **Patch `mm.get_free_memory`.** A global mutation of another project's
  function on behalf of every caller, including ComfyUI's own loads. Rejected
  in ADR-0034 and still rejected.
- **Leave the ledger and wrap `mm.load_models_gpu`.** Trades one seam for
  three, and silently breaks OOM recovery, `--disable-smart-memory`, and the
  free-memory button. The worker-side `load_models_gpu` shim is not a
  precedent: the worker is comfy-env's own process, where comfy-env *is* every
  caller.
- **Reimplement victim selection entirely.** What upstream actually provides
  is a sort and a `soft_empty_cache`; the argument for owning it is real but
  applies only to *worker* models, which is what phase one does.
- **Fixed per-worker VRAM budgets.** Converts WDDM's soft failure into a hard
  OOM. Kept as an opt-in escape hatch, not a default.
- **Require `pynvml`.** A dependency for something `ctypes` does in four
  lines. It was also never installed, so the NVML rung had never once executed
  in production.
- **Ledger-only accounting, no NVML.** Blind to every non-torch allocator —
  Blender, TensorRT, cuPy, NVENC — which is precisely the population comfy-env
  exists to host.

## Consequences

- Eviction converges instead of draining every worker, and the admission
  target is exact rather than approximate.
- comfy-env now owns a policy decision it previously delegated. Idle/LRU
  ordering across workers is ours to get right, and ours to get wrong.
- Phase one performs cross-worker IPC from inside a budget callback, which
  increases traffic on the [ADR-0020](0020-concurrency-and-env-granularity.md)
  lock-ordering hazard. Snapshotting the patcher map and staying clear of the
  pool lock are necessary but not sufficient; a single-flight or ordered-lock
  discipline is still owed.
- **The design has a tripwire.** If upstream makes `get_free_memory`
  WDDM-aware — which PR #11845 (§1.11) proposes — the change of variables
  becomes a double correction and must be removed. This is the single upstream
  change most likely to break us, and exactly what the drift canary exists to
  catch, which is why step 6 is not optional.
- We still cannot detect the failure we are preventing. Every decision here is
  argued from a model validated by measurements taken outside the running
  system; no test can prove the absence of bystander thrash.
- Host RAM remains unmirrored, and multi-GPU remains a single-device
  assumption throughout.

---

The plain-language version of all of the above is
[comfy-env's approach to memory management](../memory-approach.md).
