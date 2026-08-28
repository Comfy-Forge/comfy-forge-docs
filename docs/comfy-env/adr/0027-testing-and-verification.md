# ADR-0027: Testing and verification strategy

**Status:** accepted (2026-08-14) -- makes the existing practice a
contract and names what it deliberately does not cover.

## Decision

> **CI proves the transport and the compilers; the canary proves the
> GPU; the suite spawns real workers or it proves nothing.** What
> hosted CI structurally cannot exercise is verified per-machine at
> runtime instead -- by design, not by accident.

The layers, and what each one guarantees:

1. **The contract suite** (`tests/`, ~25 files; 3-OS x 2-Python
   matrix, gating publish): real `SubprocessWorker`s spawned with
   `sys.executable` -- round-trip equality, error propagation, crash
   loudness, timeout kills, registry/opaque semantics, lifetime
   (producer-death survival, consumed-ack release), config parsing,
   manifest compilation. House rule, now written: transport tests use
   real workers and real sockets; in-process fakes are allowed only
   where a race cannot be produced deterministically otherwise (the
   one existing `FakeTransport` documents itself as that exception).
2. **The runtime canary IS the GPU test.** Hosted CI has no GPUs and
   never will here; the [ADR-0005](0005-tiered-tensor-serialization.md)
   canary round-trips the production path per worker, per machine, per
   spawn -- refusing on CPU-tier failure, demoting on GPU-tier
   failure. Answering "how do you know CUDA IPC works" with "on YOUR
   machine, we checked at startup" is the honest answer and the
   decided one. Gap to close per the 2026-08 GPU review: the canary
   battery is one contiguous fp32 tensor -- extend with a view, a
   bf16, and a >2MB tensor (milliseconds), and log
   "zero-copy verified" vs "zero-copy not attempted" distinctly.
3. **Compat canaries**: the pinned-ComfyUI checkout lane
   (`comfyui-canary.yml`) guards the [ADR-0024](0024-upstream-interface-contract.md)
   loan book; add the **HEAD lane** -- scheduled, against ComfyUI
   nightly, allowed to fail loudly -- as the early-warning system for
   the unofficial ABI. Cheapest insurance in the whole strategy.
4. **The worker Python floor gate**: `tests/test_ipc_shared_constraints.py`
   `ast.parse`s `_persistent_worker.py` + `_ipc_shared.py` at
   `feature_version=(3, 10)` -- the floor comfy-env supports, matching
   ComfyUI's own `requires-python >= 3.10`
   ([ADR-0006](0006-worker-crosses-the-boundary-as-source-text.md)); a config
   pinning lower is rejected at load. The worker source is stdlib-only at
   module scope and ships as text, so without this gate a contributor's
   3.11+ syntax would surface only at worker startup on an older env,
   never in CI.
5. **The benchmark harness** ([ADR-0010](0010-wire-protocol-and-transport.md)
   item 10, still missing -- this ADR owns it now): a repeatable
   CPU-only floor benchmark (echo tiny / 1MB / mesh-shaped payload) run
   in CI, tracked over time. Exists so the next "~30 ms" folklore
   number cannot fossilize; perf claims in ADRs cite it or say "not
   yet measured."
6. **Wire conformance** (0010 v1's unkept promise): before the
   pending-map rewrite touches the protocol, golden-transcript tests
   pinning frame shapes for each message type, both directions --
   the two hand-maintained interleave loops get a spec the moment
   they're replaced, not after.
7. **Discipline rules that live in review, not CI**: `_CACHE_VERSION`
   bump on any metadata-scan change ([ADR-0023](0023-metadata-scan-and-proxy-synthesis.md));
   docs-claims check (config keys/env vars grepped against the tree)
   per the truth-sweep practice; no type checker for now -- recorded
   as a deliberate choice to revisit at 1.0, not an oversight.
8. **Division of labor with comfy-test**: comfy-env's suite proves
   comfy-env (transport, compilers, contracts); comfy-test proves
   *packs* (example workflows end-to-end, wire-report gating,
   bare-host runs). Neither duplicates the other.

## Context

ADR-0009 shipped claiming "there is no test suite" long after one
existed; 0010 promised conformance tests that never appeared; the GPU
story's verification model was real but unstated, leaving "how do you
know pool IPC works" unanswerable (current honest answer: we don't --
it is default-off and [ADR-0030](0030-gpu-platform-floors.md) gates it
on a lifetime contract). A strategy document is how the suite's scope
stops drifting from its reputation, in both directions.

## Consequences

- "Covered" now has a defined meaning per layer; a reviewer can ask
  "which layer catches this" and get an answer or a gap.
- Items 2's battery, 3's HEAD lane, 4, 5, and 6 are the implementation
  backlog this ADR creates; they are small and independent.
- The no-GPU-in-CI constraint is permanent at this project's scale;
  anything that can only be verified on-GPU must be canary-shaped
  (probed at runtime, refused/demoted loudly) to be shippable at all.
