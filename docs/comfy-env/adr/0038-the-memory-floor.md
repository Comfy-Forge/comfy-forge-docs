# ADR-0038: The memory floor — read, publish, ask, never patch

**Status:** accepted (2026-09-04). Supersedes the admission *mechanism* of
[ADR-0034](0034-admission-by-arithmetic.md) and the co-management design of
[ADR-0036](0036-mirroring-comfyui-memory-management.md); places
[ADR-0035](0035-duck-typed-model-proxy.md)'s model proxy behind a
default-off switch pending removal. The goal of
[ADR-0025](0025-vram-co-management.md) is unchanged: a pack's VRAM should be
invisible to the user. What changed is the price we are willing to pay for
it.

## Decision

> **comfy-env does not patch, wrap or class-patch anything in the ComfyUI
> host process, and does not register a model it does not own.** It reads
> values, publishes one number into a knob ComfyUI already exposes, and
> calls ComfyUI's own public functions. Host-driven reclaim of worker VRAM
> is deliberately dropped; workers release on their own instead.
>
> Two wraps survive as a named, switched exception, each calling the
> original first: `unload_all_models` (the Free-memory broadcast) and
> `should_free_pins_for_ram_pressure` (RAM reclaim). Both are on the list to
> go. An AST test fails the build if anything else assigns to a comfy
> module.

The floor is always on and has four moving parts:

| | What it does |
|---|---|
| **Publish** | Adds what workers hold to `EXTRA_RESERVED_VRAM`, which ComfyUI reads live on every load, so its own arithmetic backs off |
| **Ask** | When a worker needs room, calls `mm.free_memory`, which evicts the host's own models unconditionally |
| **Measure** | Workers report one measured scalar of what they hold, not a ledger sum |
| **Release** | A worker idle for 60 s gives its VRAM back, and only then may the reserve shrink |

## Context

### The reserve works on one path and is inert on the other

Measured on an RTX 3090, ComfyUI 0.33.0, comfy-aimdo 0.4.13
(`research/memory-floor/p2_reserve_levers.py`, 6/6):

| Path | Preventive (publish a reserve) | Reactive (ask the host to free) |
|---|---|---|
| legacy | **works**: a 20.13 GiB reserve took a 6 GiB model from 6.00 GiB resident to 1.38 GiB | works |
| paged (aimdo) | **inert**: 6.03 GiB resident with and without | **works**: 6.03 GiB → 0.03 GiB |

`ModelPatcherDynamic` ignores `lowvram_model_memory` and decides residency
at page-fault time, so ComfyUI's reserve never enters the decision. aimdo's
own headroom is fixed when its devices initialise: the global
`set_simple_vram_headroom` is inert once running (tested with NVML pressure
on and off), a second `init_devices` returns `False`, and a second
`control.init` **segfaults the process**. On an aimdo host the reserve is
whatever `--reserve-vram` set at launch, and comfy-env cannot move it.

So the floor is preventive on the legacy path and reactive on the paged one.
This is the single largest correction to the design as originally drafted.

### The host already sees half of it

`get_free_memory` is `cudaMemGetInfo` plus torch's cached-but-unused bytes,
and `cudaMemGetInfo` is device-wide on Linux. The host therefore already
sees resident worker VRAM there. Publishing residency again double-books it,
which cost **8.9 GiB of idle card** in measurement. The charge is the
headroom a worker will take *beyond* what it already holds; on Windows WDDM,
where the reading is per process, the whole entitlement is charged.

### The ledger is not the truth

A paged model reads **zero** in ComfyUI's own
`model_loaded_weight_memory`, and torch reports about 20 MB against 6.4 GB
resident, because aimdo allocates outside torch's allocator. The worker
therefore reports one measured scalar: the **maximum** of aimdo's own
accounting and torch's reserved, never their sum. They overlap when torch
allocates (a 4 GiB model measured 4.02 GiB in torch and 4.03 GiB in aimdo at
the same instant, so summing reserved 8.05 GiB for a 4 GiB worker) and
partition only when aimdo pages.

### Reclaim was priced wrong

[ADR-0035](0035-duck-typed-model-proxy.md)'s proxy exists so ComfyUI can
evict a worker's model. Both of comfy-env's loud breakages in twelve months
came through it: a new unguarded `.model.<x>` read landing on a fake object.
Against *nothing* that is a fair trade. Against a worker that releases on
its own it buys only latency, in one narrow window:

| Situation | With proxy | Reserve plus idle release |
|---|---|---|
| Worker executing, host needs VRAM | cannot evict a running model | same |
| Worker idle a while | host evicts it | already released |
| Worker idle 5 s | host evicts it | host runs conservatively for seconds |
| Host would over-commit | evicts to survive | never over-commits |

Only the third row differs, and it costs latency rather than capability. The
proxy is not worth its breakage history for that.

### Upstream reached the same shape and it did not ship

ComfyUI's own `pyisolate-support` branch built exactly this proxy at ten
times the scale: a 1311-line registry, an 891-line patcher proxy, and
113 lines of in-tree changes to `model_management.py` to make a foreign
entry survive its lifecycle paths. It is four months stale, 747 commits
behind, and none of it is in master. The lesson taken here is that a full
proxy is viable **in** the tree and a treadmill outside it.

## Consequences

**Good.** The always-on floor is satisfied by ComfyUI back to roughly
September 2024, bounded only by `EXTRA_RESERVED_VRAM`, against the **22-day
window** the previous design required. That range is computed by sweeping
`comfy_env.contract` over upstream history
(`research/memory-floor/sweep_contract.py`), not asserted. comfy-env stops
re-deriving upstream arithmetic, which removes a whole class of silent
drift: the shipped admission slack was 1.02 where upstream books 1.1, under-
freeing by 680 MiB on a 12 GiB model and growing linearly.

**Bad, and accepted.** The host cannot take VRAM back from a busy worker. It
avoids over-committing and waits. During a host out-of-memory event, idle
release cannot help, because the workers are not idle then. That is the case
the optional observer exists for.

**Also lost by default.** ComfyUI's Free-memory button no longer reaches
workers unless the observer is enabled.

**Unresolved.** The proxy is still registered. Nothing in the floor depends
on it any more, so removing it is now a deletion rather than a redesign, but
it has not been done.

## What this replaces, precisely

* [ADR-0034](0034-admission-by-arithmetic.md)'s offset-compensated target
  computed from numbers comfy-env owns → `reserve.ask_target`, which
  reproduces upstream's own expression. The offset survives, but only where
  `mem_get_info` is genuinely process-local.
* [ADR-0036](0036-mirroring-comfyui-memory-management.md)'s mirroring of the
  manager → publishing one number and letting upstream's manager run
  unmodified.
* The `PromptModelTracker.start` class patch → reading
  `comfy_execution.progress.get_progress_state().prompt_id`, which carries
  the real prompt id, cannot be reverted by ComfyUI's own custom-node
  unhooking pass, and predates the tracker by more than a year.
* The pin-split allocation half → deleted entirely (481 lines). It never
  shipped, `ensure_pin_budget` already stops pinning from the global
  available-RAM figure, and the same ceiling sizes each model's host buffer
  through `pinned_hostbuf_size`, so a grant would have silently capped large
  models.

See [Memory management](../memory-management.md) for what an operator can
switch, and the measured range that each level requires.
