# ADR-0001: Process isolation via persistent subprocess workers

**Status:** accepted

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

## Decision

Nodes that declare a `comfy-env.toml` run in **persistent subprocess
workers**, one per environment, using the isolated env's own interpreter
(`isolation/wrap.py`). Workers stay alive across executions (models stay
resident), auto-restart on crash, and are keyed by env directory.

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
  zero-copy paths for tensors.
- Startup cost: metadata scans spawn subprocesses; mitigated by hash-keyed
  metadata caching.
- Bidirectional IPC is required (progress reporting, VRAM budget negotiation
  flow worker -> parent).
