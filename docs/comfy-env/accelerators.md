# Accelerator declarations

A node pack convention with two halves, enforced differently:

1. **A node declares the backend(s) it requires at execution, or none.** The
   vocabulary is closed and comfy-env enforces it -- an unrecognized value
   raises during the metadata scan.
2. **Accelerator packages are imported lazily, inside the nodes that declare
   them.** Nothing can enforce this at declaration time; it is *observed* at
   scan time and *checked* statically in CI. See
   [Enforcement](#enforcement) for what each mechanism can and cannot see.

```python
class RemeshGPUNode(io.ComfyNode):
    ACCELERATOR = "cuda"            # REQUIRES CUDA at execution

class SegmentGPUNode(io.ComfyNode):
    ACCELERATOR = ["cuda", "mps"]   # runs on either; not on ROCm or CPU

    def execute(cls, mesh, ...):
        import cumesh      # lazy: only runs when the node actually executes
        ...
```

## What declaring buys you

The declaration is not paperwork -- comfy-env consumes it in four places:

| # | Behavior | Mechanism |
|---|---|---|
| 1 | **Precise degradation on the wrong machine.** On a machine without the backend, the node still registers with its real inputs and outputs (shared workflows load; dispatcher node-ids resolve) but is hidden from the node picker, its description badged "(requires CUDA -- unavailable on this machine)", a startup line names it, and executing it raises a named-reason error instead of a raw torch stack trace | the unavailable stub; gate = machine backend &isin; declared list ([ADR-0012](adr/0012-unavailable-nodes-hidden-not-unregistered.md)) |
| 2 | **Import hygiene, enforced twice.** The declaration tells both checkers which nodes may lazily import the `[cuda]` packages -- the scan's `sys.modules` check at every `register_nodes()`, and `comfy-test lint --check accel` in CI | [Enforcement](#enforcement) |
| 3 | **Honest CPU test lanes.** comfy-test skips declared-GPU nodes as "requires cuda" instead of faking their imports with empty mock modules | [What this replaces](#what-this-replaces) |
| 4 | **A machine-readable tag** -- `_comfy_env_accelerator` on the proxy class, for harnesses and future UI badging | registration |

One honest limit: the declaration is **consumed, never audited**. Nothing
verifies the node actually needs what it declares -- over-declare and a
node that runs fine on CPU is needlessly hidden on Macs; under-declare and
CPU users get the ugly raw error instead of the named one. Auditing would
mean executing the node on real hardware, which is comfy-test's execution
level's job, not a metadata scan's.

## The rule

1. **Declaration.** `ACCELERATOR` is a class attribute holding one value,
   or a list of values, from comfy-env's backend vocabulary -- `"cuda"`,
   `"rocm"`, `"xpu"`, `"mps"`. A list means "any of these will do", which is
   how a node that works on CUDA and Metal but not ROCm says so. Absent
   means CPU-capable. The meaning is strictly **"requires one of these
   backends at execution"**, not "can use them": a node with a real CPU
   fallback declares nothing.

    In practice, **only `"cuda"` is wired end-to-end today** -- it is the
    only backend with a wheel index, a `[cuda]` config section, and hardware
    probes, and the only one any fleet pack declares. The other three names
    are valid and the availability gate honours them (a ROCm/MPS machine is
    detected correctly), but declaring them is forward-compatibility, not a
    supported path: there is no `[rocm]`/`[xpu]`/`[mps]` wheel story yet.

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
   ([ADR-0012](adr/0012-unavailable-nodes-hidden-not-unregistered.md)) --
   row 1 above, mechanically: the hiding rides ComfyUI's own `DEPRECATED`
   handling (hidden from picker/search, still registered), and the
   named-reason error reads:

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
- **Static check (CI).** `comfy-test lint --check accel` AST-walks each env
  on a bare checkout -- no env built, no server -- so a violation is caught
  before the pack ships rather than after it is installed. Unguarded
  top-level accelerator imports are errors; guarded (`try/except`) ones and
  `torch.cuda` use in undeclared modules are warnings. It resolves import
  names from [`env.stamp.json`](seals.md)'s `accel_imports`, so `faithc-aot`
  is matched as `faithcontour` exactly rather than guessed; a package with no
  recorded mapping is **reported as unverifiable, not passed**. Static
  analysis can still be defeated by dynamic imports, which is why the
  scan-time check remains the authority.

    This lived in comfy-env as `comfy-env doctor`'s third section until
    0.4.27. It moved because the check is only useful before shipping, which
    is CI's job, and because guessing import names by
    `name.replace("-", "_")` -- the best a checker inside comfy-env could
    do -- passes a top-level `import faithcontour` silently.
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
