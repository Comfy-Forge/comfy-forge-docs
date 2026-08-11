# ADR-0001: Process isolation via persistent subprocess workers

**Status:** accepted -- reaffirmed 2026-08 after an independent adversarial
review (two reviewers, both verdicts: the decision stands; criticism
concentrates on the transport layer, now split into
[ADR-0010](0010-wire-protocol-and-transport.md))

## Context

ComfyUI custom nodes share a single Python environment and a single process.
This breaks when node A needs torch 2.4 and node B needs torch 2.8, when two
packages bundle conflicting native libraries (libomp, CUDA runtimes, cv2), or
when a node requires a different Python version (Blender needs 3.11, pymesh2
needs 3.9).

Alternatives considered by the ecosystem:

- **Pin everything in one env** -- collapses as soon as two popular packs
  disagree; native-library conflicts are unsolvable this way.
- **Threads / in-process sandboxing** -- Python cannot load two versions of a
  compiled extension into one process; no isolation of native state.
- **Fresh subprocess per node execution** -- correct but unusable: model loads
  and imports would repeat on every graph execution.

Alternatives explored and rejected in the 2026-08 review (recorded so they
are not re-litigated without new facts):

- **CPython subinterpreters (PEP 734) / per-interpreter GIL** -- one address
  space means one copy of each native extension per dynamic-linker namespace;
  two torch builds or two libomps cannot coexist regardless of interpreter
  count, and torch does not support subinterpreters. Solves parallelism, not
  native conflicts.
- **Free-threaded CPython** -- orthogonal for the same reason: helps the
  parent's concurrency, not isolation.
- **Import-hook / sys.path swapping in one process** -- sound only for
  pure-Python conflicts; cannot touch the motivating native cases. (A cheap
  "tier 0" for pure-Python-conflict packs remains a possible future
  optimization.)
- **Env layering / site-packages overlays** -- layering cannot reconcile
  *conflicting* packages; as a disk optimization it is already delivered by
  pixi/uv hardlinking (see the dedup tests).
- **Containers** -- right isolation primitive, wrong audience: the user base
  is majority Windows desktop, where GPU passthrough and image-management UX
  are disqualifying. Fine for server farms; not this product.
- **WASM** -- no CUDA. Dead on arrival.
- **pyisolate-style host-coupled venvs** -- the closest sibling project
  reconstructs the host's `sys.path` in the child and assumes the host's
  torch, which reintroduces the shadowing/native-conflict chaos isolation
  exists to kill; its fully-sealed mode transports tensors as JSON lists,
  disqualifying at multi-GB scale. Its transport discipline is worth
  stealing (ADR-0010); its isolation model is not.

Related directions explicitly NOT rejected, tracked as future work in
ADR-0010: a single GPU-owner process ("tensor daemon") to collapse N CUDA
contexts into one; CUDA MPS as a measurable partial mitigation (Linux only);
scheduler-level async integration with ComfyUI implemented at the
synthesized-proxy seam (preserving the zero-code-change contract); making
the ComfyUI-facing hooks official upstream instead of monkey patches.

## Decision

> **One permanent worker process per environment.** Not one shared
> environment (conflicts are structural, see Context); not a fresh process
> per execution (model reloads make it unusable); a **persistent** process
> per isolated env -- spawned lazily on first use, resident until shutdown,
> restarted on crash.

Concretely: nodes that declare a `comfy-env.toml` run in **persistent
subprocess workers**, one per environment, using the isolated env's own
interpreter (`isolation/wrap.py`). Workers are spawned lazily on the first
call to that env, then stay alive across executions (models stay resident),
auto-restart on crash, and are keyed by env directory.

Why persistent rather than spawn-per-execution: a fresh spawn pays
interpreter boot + `import torch` + CUDA context creation (seconds and
hundreds of MB) *plus reloading every model the node holds* -- multi-GB
weights, disk -> RAM -> VRAM -- on every graph execution, of which a
workflow has many. Persistence amortizes all of that to once per session;
`SubprocessModelPatcher` exists precisely so resident models still obey
ComfyUI's VRAM manager (evict to CPU under pressure) instead of the two
processes OOMing each other. Measured cost of persistence: ~180 MB private
RAM per idle CPU worker (torch code pages are shared between workers on the
same build), plus a CUDA context where one is created.

The parent **never imports node code**. At registration time,
`isolation/metadata.py` spawns a short-lived subprocess inside the isolation
env to serialize node metadata (`INPUT_TYPES`, `RETURN_TYPES`, ...), and the
parent synthesizes **proxy classes** from that metadata. To ComfyUI a proxied
node is indistinguishable from a normal one.

Worker-resident GPU models participate in ComfyUI's VRAM management through
`SubprocessModelPatcher` (`isolation/model_patcher.py`): ComfyUI eviction
calls `unpatch_model()`, which IPCs the worker to move the model to CPU.
Model detection is automatic (the worker hooks `Module.to()` / `.cuda()`), so
isolated repos need zero changes.

## Consequences

- Conflicting torch/CUDA/Python stacks coexist on one ComfyUI install.
- Crashes in native node code kill a worker, not ComfyUI; the pool restarts it.
- All data crossing the boundary must be serialized -- hence the tiered
  strategy ladder ([ADR-0005](0005-tiered-tensor-serialization.md)) and its
  zero-copy paths for tensors. The torch-family alignment that ladder
  depends on is *delivered* by the wheel farm's combo-pinned builds -- see
  the [cuda-wheels ADR series](../../cuda-wheels/adr/index.md), a separate
  decision record set for that repo.
- Startup cost: metadata scans spawn subprocesses; mitigated by hash-keyed
  metadata caching.
- Bidirectional IPC is required (progress reporting, VRAM budget negotiation
  flow worker -> parent).
