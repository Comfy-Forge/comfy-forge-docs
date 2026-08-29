# Accelerator declarations

## Why this convention exists

Packs on the registry install for everyone, whatever the machine, and then
fail at install or execution when the hardware does not match (example: TRELLIS2 node pack on Mac, no CUDA GPU).

This bites quite hard for node packs like ComfyUI-GeometryPack:
- Some nodes need a CUDA GPU, most run fine on CPU, and the
CUDA remeshing backends should simply disappear for a user without an NVIDIA
card instead of greeting them with a stack trace.
- Testing has a similar disease in another form: python files importing CUDA packages
forced CPU test lanes to mock those imports.

The cure for both of these problems is one tag on the node.

## What you write

`ACCELERATOR` is a class attribute holding one value, or a list of values,
from a closed vocabulary. An unrecognized value fails the metadata scan
loudly, naming the node and the vocabulary.

| Value | Hardware | Status |
|---|---|---|
| `"cuda"` | NVIDIA | wired end to end (wheel index, `[cuda]`, probes) |
| `"rocm"` | AMD | valid and availability gated; no wheel story yet |
| `"xpu"` | Intel | valid and availability gated; no wheel story yet |
| `"mps"` | Apple | valid and availability gated; no wheel story yet |

```python
class RemeshGPUNode(io.ComfyNode):
    ACCELERATOR = "cuda"            # REQUIRES CUDA at execution

class SegmentGPUNode(io.ComfyNode):
    ACCELERATOR = ["cuda", "mps"]   # runs on either; not on ROCm or CPU

    def execute(cls, mesh, ...):
        import cumesh      # lazy: only runs when the node actually executes
        ...
```

The second half of the convention is visible in that example: accelerator
packages are imported lazily, inside the nodes that declare them, never at
module top level. [Enforcement](#enforcement) covers who checks this and
what each checker can see.

## What it buys you

| # | Behavior | Mechanism |
|---|---|---|
| 1 | **Precise degradation on the wrong machine.** The node still registers with its real inputs and outputs (shared workflows load, dispatcher node ids resolve) but is hidden from the node picker, its description badged "(requires CUDA, unavailable on this machine)", a startup line names it, and executing it raises a named reason error instead of a raw torch stack trace | the unavailable stub; the gate is machine backend &isin; declared list ([ADR-0012](adr/0012-unavailable-nodes-hidden-not-unregistered.md)) |
| 2 | **Import hygiene, enforced twice.** The declaration tells both checkers which nodes may lazily import the `[cuda]` packages | [Enforcement](#enforcement) |
| 3 | **Honest CPU test lanes.** comfy-test skips declared GPU nodes as "requires cuda" instead of faking their imports with empty mock modules | [What this replaces](#what-this-replaces) |
| 4 | **A machine readable tag**, `_comfy_env_accelerator` on the proxy class, for harnesses and future UI badging | registration |

One honest limit: the declaration is **consumed, never audited**. Nothing
verifies the node actually needs what it declares.

Over-declare and a node that runs fine on CPU is needlessly hidden on Macs.

Under-declare and CPU users get the raw error instead of the named one.

Auditing implies executing the node on real hardware, which is the job of comfy-test's
[execution level](../comfy-test/levels/execution.md), not a metadata scan.

## The fine print

1. A list means any of these will do. `["cuda", "mps"]` says CUDA or Metal,
   not ROCm.
2. Absent means CPU capable, and the meaning is strictly "requires one of
   these backends at execution", never "can use them". A node with a real
   CPU fallback declares nothing.
3. Values are normalized at scan time: lowercased, deduplicated, sorted.
4. **Registered but hidden from the menu**
   ([ADR-0012](adr/0012-unavailable-nodes-hidden-not-unregistered.md)) is
   row 1 above, mechanically: the hiding rides ComfyUI's own `DEPRECATED`
   handling (hidden from picker and search, still registered), and the
   named reason error reads:

    ```
    Node 'GeomPackRemesh_GPU' requires CUDA; this machine has backend 'cpu'
    (no NVIDIA GPU detected). Use a CPU-capable alternative node or run on a
    machine with CUDA.
    ```

    Full unregistration was rejected deliberately: a missing node type
    breaks shared workflow loading with an inscrutable "node type not
    found". Hiding gives the clean picker without that cost.
5. **Lazy imports only.** Packages from the env's `[cuda]` list may only be
   imported inside function bodies (typically `execute()`) of nodes that
   declare that accelerator. A module top level accelerator import is an
   error anywhere: comfy-env skips CUDA wheels on machines with no GPU, so
   on every CPU machine that import kills the metadata scan and every node
   in the env silently vanishes, CPU nodes included.

## CPU or GPU: the dispatch pattern

A declaration can express which GPUs, never GPU or CPU, because absent
means CPU capable. So a node offering both should declare nothing and act
as an accelerator neutral dispatcher, routing to hidden per backend leaf
nodes (by node id, without importing them), each declaring its own
requirement.

GeometryPack's flagship nodes (Remesh, UV Unwrap, Fix Normals)
already work this way, and it stays the blessed shape.

Opportunistic GPU use, `device = "cuda" if torch.cuda.is_available() else
"cpu"` with a genuine CPU path, is legal anywhere and declares nothing.
Prefer `comfy.model_management.get_torch_device()`, which is ComfyUI's own
answer to the same question and respects flags like `--cpu`.

## Enforcement

Three mechanisms, in order of authority:

1. **Scan time observation (authoritative).** The metadata scan checks
   `sys.modules` after importing the pack: nothing has executed during a
   scan, so any `[cuda]` package present was imported at module top level.
   Import names are mapped from distribution names via package metadata, so
   `faithc-aot` importing as `faithcontour` is caught too. Violations are
   reported loudly at every `register_nodes()`.
2. **Static check (CI).** `comfy-test lint --check accel` AST walks each
   env on a bare checkout, no env built, no server, so a violation is
   caught before the pack ships rather than after it is installed.
   Unguarded top level accelerator imports are errors; guarded
   (`try/except`) ones and `torch.cuda` use in undeclared modules are
   warnings. It resolves import names from
   [`env.stamp.json`](seals.md)'s `accel_imports`, so `faithc-aot` is
   matched as `faithcontour` exactly rather than guessed; a package with no
   recorded mapping is reported as unverifiable, not passed. Static
   analysis can still be defeated by dynamic imports, which is why the
   scan time check remains the authority.

    This lived in comfy-env as a `comfy-env doctor` section until 0.4.27.
    It moved because the check is only useful before shipping, which is
    CI's job, and because guessing import names by
    `name.replace("-", "_")`, the best a checker inside comfy-env could
    do, passes a top level `import faithcontour` silently.

3. **Registration gate.** `build_proxy_class` builds the unavailable stub
   for declared nodes the machine cannot serve. Available nodes carry
   `_comfy_env_accelerator` (a list) on the proxy class for downstream
   consumers.