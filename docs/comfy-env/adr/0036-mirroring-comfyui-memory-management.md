# ADR-0036: Mirroring ComfyUI's memory manager across the process boundary

**Status:** accepted (2026-08-16). Amends
[ADR-0025](0025-vram-co-management.md), and supersedes the mechanism —
though not the goal — of [ADR-0034](0034-admission-by-arithmetic.md).
Upstream behaviour is described in
[ComfyUI's memory manager](../comfyui-memory-management.md); read §3 and §5
of that page first, because this record is unintelligible without them.

## Decision

> **Worker models stay registered in ComfyUI's ledger, and comfy-env takes
> over exactly two things a process boundary makes impossible for upstream:
> the eviction *target*, and the choice of which *worker* model to evict.**
> Everything else — when to evict, which host models to evict, the
> escalation ladder, the load path — remains upstream's.
>
> Three rules make that work: the proxy obeys its arguments exactly; the
> target is a change of variables, never an estimate; and registration is
> reconciliation, not a one-shot event.

## Context

ComfyUI's eviction loop is a feedback loop. It recomputes
`memory_to_free = memory_required - get_free_memory(device)` per victim
(`model_management.py:883`) and stops when that goes non-positive. That
re-measurement is what makes it evict the minimum instead of everything.

comfy-env inserts `SubprocessModelPatcher` proxies into
`current_loaded_models` so worker-resident models participate in that loop.
Four things break, and they are independent of each other:

1. **The feedback signal is severed — on Windows.** When the loop evicts a
   worker model, real VRAM is freed in another process. The parent's
   `mem_free_cuda` term does not move, because `mem_get_info` on WDDM reports
   the calling process's commitment budget. The loop cannot observe its own
   progress and keeps evicting. On Linux `mem_get_info` is device-wide, the
   signal survives, and this failure does not occur.
2. **The target is computed from a number that is not device-free.** Same
   root cause, at admission time rather than during the loop.
3. **The proxy does not honour its arguments.** `detach(unpatch_all=False)`
   is bookkeeping upstream — it skips `unpatch_model` and leaves weights
   resident (`comfy/model_patcher.py:1295-1299`) — and `load_models_gpu`
   calls it on *every* already-loaded model (`:958`). The proxy treated it as
   a full unload. Symmetrically, `partially_load` received the `0.1` sentinel
   meaning "load almost nothing" and loaded everything.
4. **Registration is one-shot.** Upstream pops an entry from
   `current_loaded_models` whenever `model_unload` returns True (`:893-894`)
   and never re-adds it. comfy-env skipped ids already known
   (`pool.py:575`) and the worker deduped on `id(module)` for the process
   lifetime, so a popped proxy could never come back — the VRAM stayed
   resident, invisible, and unevictable.

Two further facts constrain any fix. Over-admission on WDDM does not fail:
it silently demand-pages a *bystander* process's working set to system RAM —
measured, a 53× bandwidth collapse in an unrelated process, with no exception
and no counter anywhere. And NVML per-process accounting returns
`NOT_AVAILABLE` for every PID under WDDM, including our own, so exact
attribution is unavailable.

## Decision detail

**Proxies stay in `current_loaded_models`.** Registration is not only a
subscription to eviction; it also subscribes worker models to
`unload_all_models` — OOM recovery, `--disable-smart-memory`'s
after-every-prompt unload, and the "free memory" button. Leaving the ledger
would silently break all three, and restoring them would mean wrapping three
upstream functions where
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
worker is idle and which ran last; upstream's sort key is offload fraction,
refcount and size, and refcount is a constant for a proxy object.

*Phase two* — the residual goes to `mm.free_memory(..., keep_loaded=<worker
LoadedModels>)`. `keep_loaded` is applied before the candidate list is built
(`:874`), so every remaining victim is parent-local, every eviction moves the
parent's own numbers, and the feedback loop works again.

`keep_loaded` is therefore **load-bearing on Windows, and policy-only on
Linux.** Membership uses `LoadedModel.__eq__`, which dereferences a weakref
(`:820-821`, `:762-764`) — so the list must hold strong references to the
patchers, or a collected patcher makes `None is None` keep an unrelated
model.

**The target is a change of variables, not an estimate.** Given that upstream
computes `memory_required - get_free_memory(device)`, pass:

```
memory_required = need + (blind_free − true_free)
```

`blind_free` cancels, and upstream targets `need − true_free` regardless of
what its own measurement says. This is exact in both WDDM regimes — when the
budget is pinned the term is large, when VidMm re-partitions the term
collapses *because the blind number became honest*, and the product is the
same either way. It is not a correction factor and must not be tuned.

Two things break the identity and are therefore forbidden: **clamping the
term at zero** (it is legitimately negative while `blind` sits below `true`,
which is the idle case), and **letting the parent's own allocator cache into
it** (`get_free_memory` adds `reserved − active`; subtract it via
`torch_free_too=True` rather than by flushing).

**NVML is the only device-wide truth, read in-process.** Via `ctypes` into
`nvml.dll` / `libnvidia-ml.so.1` — no pip dependency, and 0.002 ms against
the 35 ms that shelling out to `nvidia-smi` costs, twice per request. Resolve
the device by UUID, not by index, since NVML's enumeration and CUDA's
ordinals disagree. When no NVIDIA driver is present there is no contention to
arbitrate, and we degrade to upstream's own semantics.

**Registration is reconciliation.** On each drain: create proxies that are
missing, refresh residency from worker telemetry, and **re-insert a
`LoadedModel` for any live resident patcher that upstream has popped.** The
worker announces a module whenever its residency transitions, not once per
`id(module)`.

**Worker telemetry replaces guessed constants.** The worker reports
instantaneous `torch.cuda.memory_reserved()` — never the high-water mark,
which was measured transiently at 15.2 GB — plus a per-process floor measured
once at startup after warm-up. This is a *floor*, not a truth source: it is
blind to allocations made outside torch (measured, 1,536 MB via `cuMemAlloc`
moved the accounting gap by exactly that much and left `memory_reserved()`
unchanged), which is why NVML remains primary.

**Errors are deliberately one-sided.** Because over-admission harms an
unobservable third party while under-admission costs a bounded, attributable
reload, headroom is biased high. Concretely this restores upstream's `× 1.1`
rather than the `1.02` point estimate, and deletes the flat per-worker
constant that charged a CPU-only worker for a CUDA context it never created.

**What is deliberately not mirrored: host RAM.** The proxy answers
`is_dynamic() → False`, excluding it from every pin and RAM-eviction path,
because those paths assume a real patcher holding real weights. This is safer
than the VRAM equivalent for a structural reason worth recording:
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
  precedent for this: the worker is comfy-env's own process, where comfy-env
  *is* every caller.
- **Reimplement victim selection entirely.** What upstream actually provides
  is a sort and a `soft_empty_cache`; the argument for owning it is real but
  applies only to *worker* models, which is what phase one does.
- **Fixed per-worker VRAM budgets.** Converts WDDM's soft failure into a hard
  OOM. Kept as an opt-in escape hatch, not a default.
- **Require `pynvml`.** A dependency for something `ctypes` does in four
  lines. It was also never installed, so the NVML rung had never once
  executed in production.
- **Ledger-only accounting, no NVML.** Blind to every non-torch allocator —
  Blender, TensorRT, cuPy, NVENC — which is precisely the population comfy-env
  exists to host.

## Consequences

- Eviction converges instead of draining every worker, and the eviction
  target is exact rather than approximate.
- comfy-env now owns a policy decision it previously delegated. Idle/LRU
  ordering across workers is ours to get right, and ours to get wrong.
- Phase one performs cross-worker IPC from inside a budget callback, which
  increases traffic on the [ADR-0020](0020-concurrency-and-env-granularity.md)
  lock-ordering hazard. Snapshotting the patcher map and staying clear of the
  pool lock are necessary but not sufficient; a single-flight or ordered-lock
  discipline is still owed.
- **The design has a tripwire.** If upstream ever makes `get_free_memory`
  WDDM-aware — which [PR #11845](https://github.com/Comfy-Org/ComfyUI/pull/11845)
  proposes — the change of variables becomes a double correction and must be
  removed. This is the single upstream change most likely to break us, and it
  is exactly what the drift canary exists to catch, which is why step 6 is not
  optional.
- We still cannot detect the failure we are preventing. Every decision here is
  argued from a model validated by measurements taken outside the running
  system; no test can prove the absence of bystander thrash.
- Host RAM remains unmirrored, and multi-GPU remains a single-device
  assumption throughout.
