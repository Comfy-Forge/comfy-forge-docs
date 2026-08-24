# Accelerator declarations

A node pack convention, enforced by comfy-env: **a node declares the
backend(s) it requires at execution, or none**, and accelerator packages
must be imported lazily, inside the nodes that declare them.

```python
class RemeshGPUNode(io.ComfyNode):
    ACCELERATOR = "cuda"            # REQUIRES CUDA at execution

class SegmentGPUNode(io.ComfyNode):
    ACCELERATOR = ["cuda", "mps"]   # runs on either; not on ROCm or CPU

    def execute(cls, mesh, ...):
        import cumesh      # lazy: only runs when the node actually executes
        ...
```

## The rule

1. **Declaration.** `ACCELERATOR` is a class attribute holding one value,
   or a list of values, from comfy-env's backend vocabulary -- `"cuda"`,
   `"rocm"`, `"xpu"`, `"mps"`. A list means "any of these will do", which is
   how a node that works on CUDA and Metal but not ROCm says so. Absent
   means CPU-capable. The meaning is strictly **"requires one of these
   backends at execution"**, not "can use them": a node with a real CPU
   fallback declares nothing.

    There is **no "any GPU" sentinel.** Spell out the backends the node
    actually supports -- `["cuda", "rocm", "xpu", "mps"]` if it really is
    all four. A catch-all value claims support for hardware nobody tested.

    Values are normalized at scan time (lowercased, de-duplicated, sorted),
    and an unrecognized one **fails the scan loudly**, naming the node and
    the vocabulary. It has to: an unknown value used to be compared for
    equality against the machine backend, so nothing matched it and the node
    was hidden on *every* machine -- including one with the right hardware --
    with no message anywhere.
2. **Lazy imports only.** Packages from the env's `[cuda] packages` list may
   only be imported inside function bodies (typically `execute()`) of nodes
   that declare that accelerator. A module-top-level accelerator import is
   an error *anywhere*: on machines without the package (every CPU machine
   -- comfy-env skips CUDA wheels when no GPU is present), that import kills
   the metadata scan and **every node in the env silently vanishes**, CPU
   nodes included.
3. **Registered but menu-hidden**
   ([ADR-0012](adr/0012-unavailable-nodes-hidden-not-unregistered.md)).
   On a machine lacking the declared backend, the node still **registers**
   with its real inputs and outputs -- so shared workflows load and
   dispatcher node-ids resolve -- but it is hidden from the node
   picker/search (via `DEPRECATED`), a startup warning summarizes the
   hidden nodes, and executing it (via a loaded workflow) raises a
   named-reason error:

   ```
   Node 'GeomPackRemesh_GPU' requires CUDA; this machine has backend 'cpu'
   (no NVIDIA GPU detected). Use a CPU-capable alternative node or run on a
   machine with CUDA.
   ```

   Full *unregistration* was rejected deliberately: a missing node type
   breaks shared-workflow loading with an inscrutable "node type not
   found". Menu-hiding gives the clean picker without that cost.

## Multi-backend nodes: the dispatch pattern

A list covers "this node runs on several GPU backends". It does **not**
cover "this node has a CPU path too" -- that is a different shape.

A node that offers both CPU and GPU backends should not declare anything --
it should be an **accelerator-neutral dispatcher** that routes to hidden
per-backend leaf nodes (by node id, without importing them), each declaring
its own requirement. This is the pattern ComfyUI-GeometryPack's flagship
nodes (Remesh, UV Unwrap, Fix Normals) already use, and it stays the blessed
shape: a declaration can express *which GPUs*, never *GPU or CPU*, because
absent-means-CPU-capable is what makes the node available everywhere.

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
  unavailable-stub for declared nodes the machine can't serve -- the gate is
  membership, `machine_backend in declared`. Available nodes carry
  `_comfy_env_accelerator` (a list) on the proxy class for downstream
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
