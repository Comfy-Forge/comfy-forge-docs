# Accelerator declarations

A node pack convention, enforced by comfy-env: **a node declares at most one
accelerator, or none** -- and accelerator packages must be imported lazily,
inside the nodes that declare them.

```python
class RemeshGPUNode(io.ComfyNode):
    ACCELERATOR = "cuda"   # this node REQUIRES CUDA at execution

    def execute(cls, mesh, ...):
        import cumesh      # lazy: only runs when the node actually executes
        ...
```

## The rule

1. **Declaration.** `ACCELERATOR` is a class attribute with exactly one
   value from comfy-env's backend vocabulary -- `"cuda"`, `"rocm"`, `"xpu"`,
   `"mps"` -- or the reserved `"gpu"` (any non-CPU backend). Absent means
   CPU-capable. The meaning is strictly **"requires this backend at
   execution"**, not "can use it": a node with a real CPU fallback declares
   nothing.
2. **Lazy imports only.** Packages from the env's `[cuda] packages` list may
   only be imported inside function bodies (typically `execute()`) of nodes
   that declare that accelerator. A module-top-level accelerator import is
   an error *anywhere*: on machines without the package (every CPU machine
   -- comfy-env skips CUDA wheels when no GPU is present), that import kills
   the metadata scan and **every node in the env silently vanishes**, CPU
   nodes included.
3. **Degradation, not disappearance.** On a machine lacking the declared
   backend, the node still **registers** -- with its real inputs and
   outputs, a "(requires CUDA -- unavailable on this machine)" description
   badge -- and raises a named-reason error when executed:

   ```
   Node 'GeomPackRemesh_GPU' requires CUDA; this machine has backend 'cpu'
   (no NVIDIA GPU detected). Use a CPU-capable alternative node or run on a
   machine with CUDA.
   ```

   Hiding the node was rejected deliberately: a missing node type breaks
   shared-workflow loading with an inscrutable frontend error.

## Multi-backend nodes: the dispatch pattern

A node that offers both CPU and GPU backends should not declare anything --
it should be an **accelerator-neutral dispatcher** that routes to hidden
per-backend leaf nodes (by node id, without importing them), each of which
declares its own single accelerator. This is the pattern
ComfyUI-GeometryPack's flagship nodes (Remesh, UV Unwrap, Fix Normals)
already use, and it is the blessed shape: the "one accelerator per node"
scalar is true at the leaf level by construction.

Opportunistic GPU use -- `device = "cuda" if torch.cuda.is_available() else
"cpu"` with a genuine CPU path -- is legal anywhere and declares nothing.

## Enforcement

- **Scan-time observation (authoritative).** The metadata scan checks
  `sys.modules` after importing the pack: nothing has *executed* during a
  scan, so any `[cuda]` package present was imported at module top level.
  Import names are mapped from distribution names via package metadata, so
  `faithc-aot -> faithcontour` is caught too. Violations are reported
  loudly at every `register_nodes()`.
- **Static lint (advisory).** `comfy-env doctor` AST-walks each env:
  unguarded top-level accelerator imports are errors; guarded
  (`try/except`) ones and `torch.cuda` use in undeclared modules are
  advisories. Static analysis can be defeated by dynamic imports, which is
  why the scan-time check is the authority.
- **Registration-time gate.** `build_proxy_class` builds the
  unavailable-stub for declared nodes the machine can't serve; available
  nodes carry `_comfy_env_accelerator` on the proxy class for downstream
  consumers (test harnesses, UI badging).

## What this replaces

Previously, CPU test lanes stubbed missing CUDA packages with empty modules
(`COMFY_TEST_MOCK_PACKAGES`) so imports would not fail -- which made
registration "pass" for code that was never importable and broke on
`from`-imports and submodules. With declarations, test harnesses can skip
tagged nodes honestly on CPU lanes; the mock remains only as a fallback for
undeclared packs.

## Deliberately out of scope (for now)

- Per-backend-option tags inside a dispatcher's combo (needs frontend
  support to grey out unavailable options).
- Derived minimum compute capability (the cuda-wheels farm already knows
  each package's arch list; hand-declared values would rot -- this arrives
  when the wheel index publishes arch metadata).
- VRAM requirements as a gate: workload-dependent, permanently rejected.
