# ADR-0001: Process isolation via persistent subprocess workers

**Status:** accepted -- reaffirmed 2026-08 after an independent adversarial
review (two reviewers, both verdicts: the decision stands; criticism
concentrates on the transport layer, now split into
[ADR-0010](0010-wire-protocol-and-transport.md))

## Decision

> **One permanent worker process per environment.** Not one shared
> environment (conflicts are structural, see Context); not a fresh process
> per execution (per-call interpreter + torch + CUDA startup makes it
> unusable); a **persistent** process per isolated env -- spawned lazily on
> first use, resident until shutdown, restarted on crash.

Concretely: nodes that declare a `comfy-env.toml` run in **persistent
subprocess workers**, one per environment, using the isolated env's own
interpreter (`isolation/wrap.py`). Workers are spawned lazily on the first
call to that env, then stay alive across executions (models stay resident),
auto-restart on crash, and are keyed by env directory.

Why persistent rather than spawn-per-execution, in order of generality:

1. **Per-call startup latency (applies to every node, model or not).** A
   fresh spawn pays interpreter boot + `import torch` (seconds) + CUDA
   context creation (more seconds, hundreds of MB) on *every* node
   execution -- and a workflow run executes many isolated nodes. Most
   isolated nodes (the CGAL/mesh ops) hold no models at all; this cost
   alone disqualifies spawning.
2. **Cross-call state.** The worker's by-reference object cache lets one
   call's result (a mesh handle, a scene object) be consumed by a later
   call without serializing it; compiled-kernel and JIT caches also
   persist. Spawn-per-call structurally breaks both.
3. **Model residency (the multiplier, where models exist).** ML packs
   would additionally reload multi-GB weights disk -> RAM -> VRAM per
   execution. `SubprocessModelPatcher` exists precisely so resident models
   still obey ComfyUI's VRAM manager (evict to CPU under pressure) instead
   of the two processes OOMing each other.

Measured cost of persistence: ~180 MB private RAM per idle CPU worker
**even between workers on the identical torch build** -- OS page sharing
covers only the mapped read-only code/constants (~40 MB, counted once);
the Python object graph that `import torch` builds and copy-on-write data
pages are per-process by nature. Workers on *different* builds lose the
shared portion too (~220 MB each, plus duplicated disk/page cache) --
one more reason to keep the env combo spread small. Add a CUDA context
where one is created. Corroboration: pyisolate -- Comfy-Org's
own isolation library -- independently made the same choice: the child is
spawned once at extension load (`_internal/host.py:490`) and serves RPC on
one long-lived connection until an explicit `stop()`; there is no
spawn-per-call mode (earlier pyisolate iterations used impermanent
workers; the shipped design converged on persistence). Notably, neither
project had ever benchmarked the two models head-to-head -- pyisolate's
benchmark suite measures only warm RPC overhead -- until the measurement
below.

### Measured: spawn-per-call vs persistent (2026-08)

Direct A/B on real `SubprocessWorker`s -- Windows 11, NVIDIA RTX-class
machine, CPU-only torch, same-interpreter workers (no pixi activation in
the loop), trivial echo module as the node stand-in. Persistent = one
spawn, then 50 warm calls; spawn-per-call = 5 full cycles of
spawn + one call + kill. (`tests/fixtures/echo_node.py` is the fixture;
the run predates removal of the per-call health ping, so warm figures
include it.)

| model | per-call cost |
|---|---|
| cold start (spawn + `import torch` + first call) | 2,403 ms |
| persistent, warm (tiny payload) | 30.1 ms |
| persistent, warm (1 MB tensor) | 30.4 ms |
| **spawn-per-call** (spawn + call + kill) | **1,559 ms -- 52x warm** |

Interpretation, with the caveats stated honestly:

- **52x is spawn-per-call's BEST case.** The measurement deliberately
  excludes the two costs that dominate real packs: CUDA context creation
  (adds ~1-3 s and hundreds of MB VRAM per spawn) and model reloads
  (multi-GB, disk -> RAM -> VRAM, tens of seconds). A real GPU node under
  spawn-per-call pays 10-100+ seconds per execution versus ~30 ms warm; a
  20-node workflow queued twice would spend minutes purely on re-imports.
- **Payload size is not the cost at small scale**: 1 MB tensor == tiny int
  (30.4 vs 30.1 ms). The warm floor is fixed transport overhead -- and the
  run itself exposed that every warm call was paying the per-call health
  ping (ADR-0010 defect list), so the true floor is lower than 30 ms.
- **The refinement worth having is an idle reaper, not spawning**: keep
  workers hot while in use, reap after an idle window to reclaim the
  ~180-220 MB (and CUDA context) of envs the user stopped touching. That
  caps standing cost without ever putting the 1.5-s spawn on the execution
  path.

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
