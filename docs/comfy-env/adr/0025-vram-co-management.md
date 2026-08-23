# ADR-0025: VRAM co-management across processes

**Status:** accepted (2026-08-14); **substantially corrected 2026-08-15**
after a four-reviewer audit measured the WDDM behaviour this record had
called merely "advisory". Two claims below were wrong as written and are
struck in place rather than quietly edited: the protocol was **inert**,
not best-effort, on the majority platform, and the "one decision-maker"
line overstated what the code does. The repairs are recorded in
[ADR-0034](0034-admission-by-arithmetic.md) and
[ADR-0035](0035-duck-typed-model-proxy.md).

!!! danger "Correction: the budget protocol did not work on Windows"

    `torch.cuda.mem_get_info` -- the source of `get_free_memory`'s
    device term -- reports the **calling process's** budget on WDDM, not
    device-wide free. Measured (RTX 4060 Ti 16 GB, driver 581.57): a
    sibling process allocated 13.0 GiB; `nvidia-smi` free fell 13,443 MB
    while the parent's `mem_get_info` free fell **75 MB**; at 4 GiB the
    parent's delta was **0 MB**.

    ComfyUI evicts only when `memory_required - get_free_memory(device)`
    is positive (`model_management.py:883,889`). With that free value
    stuck near full-card, the difference was negative and
    `free_memory()` **evicted nothing** in response to a worker's
    request. The worker, reading the same blind number, then *over*-loaded.
    Both sides overcommitted, and the driver's sysmem fallback -- named
    below as the "mitigation" -- was in fact the only thing keeping the
    system alive, at ~10x slower. Fixed in
    [ADR-0034](0034-admission-by-arithmetic.md).

## Decision

> **ComfyUI's VRAM manager stays the single authority; workers hold
> VRAM only on lease.** Worker-resident models exist in ComfyUI's
> `current_loaded_models` ledger as `SubprocessModelPatcher` entries,
> get evicted by ComfyUI's normal pressure logic, and the worker asks
> the parent for room before loading. Two allocator populations, one
> decision-maker.

!!! warning "Correction: 'one decision-maker' is an aspiration, not a description"

    The worker runs its own ComfyUI with its own `current_loaded_models`
    and its own `free_memory`, and only `load_models_gpu` is shimmed.
    The parent additionally reaches into the worker's ledger to pop
    entries by hand. There are **two ledgers**; the parent's authority
    is real for admission and eviction, but the claim as written
    overstated it. The honest version: *the parent decides admission and
    when to evict; the worker still manages its own residency in
    between.*

The protocol, as shipped:

1. **Detection**: worker-side `Module.to()`/`.cuda()` hooks
   auto-register models that land on GPU; metadata (id, size, device)
   rides back on the call response (`_new_models`), and the parent
   builds a `SubprocessModelPatcher` per model and inserts it into
   `current_loaded_models`. What "counts": anything that moved to CUDA
   through the hooked paths -- false negatives (manual cudaMalloc,
   non-Module allocations) are unbudgeted worker overhead; false
   positives are possible for transient modules. No pack-side opt-out
   exists yet; add one if a real pack needs it.
2. **Admission**: the worker's `load_models_gpu` shim calls back
   (`request_vram_budget`); the parent evicts through ComfyUI's own
   machinery until the request x **1.1 headroom** fits, then grants.
   The 10% headroom absorbs allocator slack and context growth; it is
   a guess that has held -- change it only with a measurement.
3. **Eviction**: parent-initiated `model_to_device` commands move
   worker models to CPU via the worker's patcher path; ComfyUI decides
   *when* (its normal free-memory loop), comfy-env decides *how*.
4. **Restart/kill interaction**: on worker death the patchers follow
   the [ADR-0019](0019-worker-lifecycle.md) `_STALE_PATCHERS` protocol;
   an [ADR-0018](0018-worker-call-timeout.md) timeout kill therefore
   also invalidates every lease that worker held -- the caches are
   rebuilt on the next generation's first load.
5. ~~**`COMFY_ENV_WORKER_VRAM_BUDGET`** overrides the worker's
   `vram_state` (a `NO_VRAM` worker is promoted to `NORMAL_VRAM` under
   an explicit budget) -- the escape hatch for setups where detection
   misjudges the card.~~ *Removed 0.4.25: never set by anyone, untested,
   and the blindness-corrected negotiation (ADR-0034/0036) computes the
   honest number automatically. The callback itself -- points 1-4 --
   is unchanged.*

### Single-device today, and the two recorded landmines

The entire ledger is single-device by assumption: budget negotiation
uses the singular `mm.get_torch_device()`, the worker binds
`current_device()`, and the parent-side pool patch hardcodes device 0.
Decision: acceptable until a real multi-GPU pack exists, **but** the
budget/`model_to_device` messages gain a `device` field the first time
anyone touches this protocol again (reserve now, semantics later), and
the wire's existing `device_idx` (CudaIPC/PoolIPC frames) silently
assumes **identical device enumeration in parent and worker** -- a
pack env setting its own `CUDA_VISIBLE_DEVICES` (some do, to hide GPUs
from bpy) turns `device_idx` into a wrong-device import with no error.
Any multi-GPU work starts by exchanging device UUIDs in the handshake
and mapping indices, never trusting them.

### The WDDM position

The protocol reasons in Linux terms: free VRAM is a hard number,
exhaustion is an OOM. On Windows/WDDM -- the majority platform -- the
OS pages VRAM and `mem_get_info` is advisory. With the driver's default
sysmem-fallback policy (on since R510), overcommit does not fail: it
silently demotes allocations to system memory and runs ~10x slower;
a user who has set "prefer no sysmem fallback" gets a real OOM instead,
so the softer failure mode is the common case, not a guarantee.
~~Recorded position: the budget protocol is **best-effort on
WDDM** -- the 1.1 headroom plus ComfyUI's own conservative accounting
is the mitigation, the common failure mode is a slowdown rather than a
crash (better than the Linux failure mode), and no additional
WDDM-specific machinery (HAGS detection, residency queries) is built
until a profiled case shows the slowdown biting in practice.~~

**Struck 2026-08-15.** This was wrong in kind, not degree. "Advisory"
implied an approximate number; the number was *unrelated* to the
device. The 1.1 headroom was not a mitigation, it was ceremony -- it
multiplied a target that a negative comparison then discarded entirely.
And the slowdown was not an acceptable failure mode we had chosen; it
was the symptom of the protocol not running. The profiled case the
paragraph waited for is the measurement in the correction box above.

Current position: admission is decided by **arithmetic comfy-env owns**,
never by `mem_get_info` -- see
[ADR-0034](0034-admission-by-arithmetic.md). The WDDM sysmem-fallback
description remains accurate and still explains why the failure was
survivable rather than loud.

## Context

This bridge -- two independent allocator populations sharing one GPU
without OOMing each other -- is arguably the hairiest correctness
surface in the project and had no record; the 2026-08 reviews ranked
it the largest unrecorded subsystem. It is also the deepest part of the
[ADR-0024](0024-upstream-interface-contract.md) loan book (entries
1-4): the entire mechanism is a stand-in for a VRAM lease API that
ComfyUI does not offer. This ADR documents the stand-in; 0024 records
the ask that retires it.

## Consequences

- One decision-maker means no split-brain: workers never evict each
  other directly; every eviction flows through ComfyUI's loop.
- The cross-worker call chain (worker A's budget request evicting into
  worker B) is the lock-ordering hazard named in
  [ADR-0020](0020-concurrency-and-env-granularity.md); the pending-map
  work must keep it single-flight or order the locks.
- Detection-by-hook means the ledger is only as good as the hooks;
  packs allocating GPU memory outside `nn.Module` movement are
  invisible to the budget. Known, accepted, revisit per-pack.
