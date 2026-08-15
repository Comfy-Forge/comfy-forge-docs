# ADR-0029: Parent as switchboard -- the worker-to-worker data plane

**Status:** accepted (2026-08-14) -- records the shape chosen in
0.4.15 and its revisit trigger.

## Decision

> **All inter-worker data flows through the parent, and the parent
> owns what it holds.** There are no worker-to-worker channels. A
> value passing from pack A to pack B is serialized by A's worker,
> held by the parent -- as a reconstructed object where the parent has
> the type, as a **materialized receipt** (receiver-owned bytes,
> [ADR-0015](0015-declared-wire-types.md)) where it doesn't -- and
> re-serialized to B's worker.

What this costs, stated with the numbers: a lib-less parent forwarding
a mesh between two isolated packs copies the bulk into parent RAM on
receipt and emits fresh frames on send -- a multi-pack pipeline
(Cadderizer -> CADabra -> GeometryPack is real, per the
[ADR-0016](0016-node-pack-dependencies.md) census) moves every
intermediate through parent memory. GPU frames are the exception
(the IPC-handle forwarding cache re-emits handles without copies).
Context: transport including these copies is an estimated 1-2% of
workflow wall-clock at current mesh scales, from ad-hoc echo-path
timing (2.4 ms call floor, ~1 ms per typical mesh payload); it is not
from a standing benchmark harness, which does not yet exist
([ADR-0027](0027-testing-and-verification.md) item 5). Treat the figure
as an order-of-magnitude estimate, not a measured invariant -- the
copies are noise at that scale today, which is why this simple shape
won.

Rejected alternatives, recorded so the re-derivation stops here:

- **Hold raw frames instead of materializing** -- was the 0.4.14
  behavior; receipts referenced sender shm that died on
  restart/TTL (the WinError 2 crash class). Materialization bought
  crash-safety for a measured ~1 ms per typical payload.
- **Per-consumer ack refcounting** (sender keeps blocks until every
  eventual consumer acks): a distributed refcount across crashing
  processes -- real engineering, wrong price at 1-2%.
- **Parent-brokered direct worker<->worker sockets**: N^2 channel
  management, and the parent still needs the receipt path for
  ComfyUI's own caching of outputs; complexity without deleting the
  simple path.
- **By-reference handles**: killed twice on census evidence
  (ADR-0015); the switchboard is why killing it was safe -- receipts
  survive producer death, references would not.

**The revisit trigger, named**: a profiled workflow where forwarding
copies exceed ~10% of wall-clock (plausible route: video/latent-scale
payloads chained across 3+ isolated packs). First response at that
point is the cheap one -- pinned-memory and copy-elision inside the
switchboard ([ADR-0030](0030-gpu-platform-floors.md)) -- before any
topology change. The census measured today's graphs, which are shaped
by today's costs; this trigger is the honest hedge against that
endogeneity.

## Context

The 2026-08 review named the second-order effect of the 0.4.15
materialization fix -- "the parent becomes a memcpy hub" -- and asked
that the tradeoff be a record instead of an emergent property. It also
noted the asymmetry (GPU forwards handles, CPU forwards copies) so a
future optimizer knows the precedent exists in-tree.

## Consequences

- Crash tolerance composes: any worker can die at any time and
  everything already handed to the parent survives. This property is
  load-bearing for [ADR-0019](0019-worker-lifecycle.md) and must be
  preserved by any future data-plane change.
- Parent RAM is the working set for cross-pack pipelines; the
  switchboard's memory use scales with workflow width, not pack
  count. Fine at mesh scale; the trigger above guards the rest.
- ComfyUI's output caching gets receipts it can hold indefinitely for
  free -- no TTLs, no invalidation protocol. That simplicity is most
  of this decision's value.
