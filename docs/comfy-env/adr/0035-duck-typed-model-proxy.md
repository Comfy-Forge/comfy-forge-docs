# ADR-0035: The model proxy is a duck-type, not a `ModelPatcher` subclass

**Status:** accepted (2026-08-15); **deprecated 2026-09-04** by
[ADR-0038](0038-the-memory-floor.md). Replaced the proxy design shipped with
[ADR-0025](0025-vram-co-management.md).

!!! warning "The proxy is on its way out"

    Everything below is still true of the object as built, and the two
    honesty rules were the right ones. What changed is the verdict on
    whether it should exist at all. Both of comfy-env's loud breakages in
    twelve months came through it, and once workers release VRAM on their
    own it buys latency rather than capability. Nothing in the memory floor
    depends on it any more. The sanctioned pattern is now the optional
    observer, which reports holding nothing and is off by default.

## Decision

> **`SubprocessModelPatcher` implements the ~18 members ComfyUI actually
> reads off a loaded model and inherits nothing.** Unknown attribute
> access raises **naming the attribute**. A test greps ComfyUI for every
> `.model.<name>` access and fails when upstream touches something the
> proxy does not declare.
>
> Two honesty rules bind it: **never lie about bytes**, and **never
> raise inside someone else's loop.**

## Context

The proxy stands in for a model whose weights live in another process.
It began as a `comfy.model_patcher.ModelPatcher` subclass, which looked
like reuse and was actually inheritance of ~120 members — `add_patches`,
`load`, `apply_hooks`, `pinned_memory_size` — every one of them wrong
for an object holding no weights, and none disabled. Twelve were
overridden; the rest were live.

The failure mode of that arrangement is *silence*. Upstream adds a field
or shifts a contract, an inherited method runs against a fake model, and
the result is a wrong number rather than an exception. Three concrete
instances were found in one audit:

- `partially_load` ignored `extra_memory` — ComfyUI's computed budget —
  and full-loaded. Under `--lowvram`, where ComfyUI sets
  `lowvram_model_memory ≈ 0` meaning *load nothing*, the worker loaded
  the whole model.
- `partially_unload` evicted everything and returned `self.size`.
  `LoadedModel.model_unload` compares `freed >= memory_to_free`, so a
  200 MB request got an 8 GB eviction *and* an answer that told ComfyUI
  the model was still resident.
- `partially_unload_ram` was a no-op whose signature did not match its
  only caller; it was unreachable solely because `is_dynamic()` returns
  `False`.

Separately, `_check_worker()` raised out of `unpatch_model`. That
propagates through ComfyUI's `free_memory` loop, and every subsequent
load fails for the life of the process — a stale patcher after a worker
restart bricked memory management.

## Decision detail

**The surface is enumerated, not inherited.** Verified against real call
sites in `model_management.py`: attributes `load_device`,
`offload_device`, `parent`, `model`, `clone_base_uuid`; methods
`model_size`, `loaded_size`, `current_loaded_device`, `model_dtype`,
`model_patches_to`, `model_patches_models`, `partially_load`,
`partially_unload`, `detach`, `lowvram_patch_counter`, `is_dynamic`,
`is_clone`, `get_nested_additional_models`.

**`is_dynamic() -> False` is load-bearing**, not a stub. It excludes
`dynamic_pins`, `loaded_ram_size`, `pinned_memory_size` and the whole
pin-eviction path — where most upstream churn lives.

**Never lie about bytes.** `partially_unload` returns what the worker
actually freed, obtained by delegating to the worker's *real*
`ModelPatcher.partially_unload` over IPC. A short return is the designed
path: ComfyUI escalates to `detach()` by itself. This also recovers
genuine partial residency, which the flattened version had thrown away.

**Never raise inside someone else's loop.** Eviction paths treat a dead
or restarted worker as already-offloaded — truthfully, since the VRAM
left with the process. Load paths still raise, because a failed load
should be loud.

**Unsupported operations raise.** `add_patches`/`clone` were
structurally broken (patches stored, never applied; clones dropping
`parent` and the patch dict) and unreachable only because these objects
never become node outputs. Silently dropping a LoRA is wrong output with
no error, so they now raise. Applying patches worker-side was
considered and rejected: it would mean serialising LoRA tensors, key
namespaces, strengths, hooks and `patches_uuid` semantics, plus
reimplementing `clone_has_same_weights` for cache correctness — a
subsystem, not a method. Packs that want LoRA apply it inside their own
environment, where a real patcher holds real weights.

**The canary replaces the contract we cannot have.**
`tests/test_model_patcher_surface.py` extracts every `.model.<name>`
access from the installed ComfyUI and asserts the proxy declares it.
With no upstream interface contract available
([ADR-0024](0024-upstream-interface-contract.md)), a test that fails on
the day upstream drifts is the closest substitute.

## Alternatives rejected

- **Keep the subclass, override more methods.** Cannot enumerate what
  you do not know upstream will add; the failure stays silent.
- **Subclass and blanket-raise the unused members.** Requires listing
  ~100 names and keeping them listed. The duck-type gets the same
  property from `__getattr__` in four lines.
- **Forward patches to the worker.** See above — a subsystem.
- **Vendor a snapshot of `ModelPatcher` to inherit from.** Freezes a
  copy of someone else's internals; drift becomes invisible instead of
  loud.

## Consequences

- Upstream drift now surfaces as `AttributeError:
  SubprocessModelPatcher has no 'x'` pointing at the seam, or as a red
  canary in CI, instead of a wrong number in a memory calculation.
- Anything ComfyUI adds that a loaded model must answer will *break*
  rather than silently misbehave — deliberate, and it means a ComfyUI
  upgrade can fail loudly at test time. That is the trade.
- Real partial offload is now possible across the process boundary,
  since the proxy reports true deltas.
- The pin accounting that charged the host for host-RAM the proxy never
  allocates is gone with `is_dynamic() -> False` and the removal of the
  RAM stubs.
