# ADR-0032: Shared-memory lifetime -- the consumed-ack protocol

**Status:** accepted (shipped 0.4.15) -- records a correctness-critical
protocol that lived only in `CLAUDE.md` and was referenced but never
owned by [ADR-0019](0019-worker-lifecycle.md) / [ADR-0029](0029-parent-as-switchboard.md).

## Decision

> **A shared-memory block lives until the reader says it is done with it,
> not until a timer guesses so.** After the parent reads a reply, it sends
> the worker `{"type": "consumed", "call_id": N}`; the worker frees that
> call's shm blocks and held tensors on receipt. The retention TTL
> survives only as the crash fallback, for a parent that died before
> acking.

Mechanics (`_persistent_worker.py`, parent `subprocess.py`):

- The worker serializes a reply into shm blocks and hands the metadata to
  the parent, then **keeps** the blocks alive in a per-call keeper
  (`TensorKeeper` / `ShmKeeper`, keyed by `call_id`).
- The parent reconstructs the reply (mapping or copying those blocks),
  then sends `consumed` with that `call_id`. The worker drops the keeper
  entry; the OS frees the block when the last handle closes.
- If no ack arrives, a TTL sweep (`TENSOR_KEEPER_TTL`, 60 s) frees the
  block anyway -- the fallback for a crashed or wedged parent, never the
  primary path.
- **Materialize-on-receipt is the sibling rule** ([ADR-0029](0029-parent-as-switchboard.md)):
  a value the receiver holds but cannot reconstruct (an `OpaquePayload`)
  is copied into receiver-owned memory *before* the ack, so the receipt
  no longer references the sender's shm and survives the sender's death.

The ownership rule in one line: **the side that produced the block frees
it, on a signal from the side that read it** -- never on a clock.

## Context

The predecessor design used the 60 s TTL as the *primary* keeper: the
worker freed a reply's blocks 60 s after creating them, hoping the parent
had read them by then. A timer cannot be correct here, because "has the
reader finished" is not a function of elapsed time:

- the parent may be slow (a multi-gigabyte deserialize, a suspended
  laptop, GC pressure);
- the parent may re-enter the worker mid-read (a VRAM-eviction callback,
  [ADR-0025](0025-vram-co-management.md));
- a fat multi-output reply starts every block's clock at creation, so
  output #1 can expire while output #10 is still being serialized.

The concrete failure this caused was the **WinError 2 crash class**: a
lib-less host held an `OpaquePayload` whose frames pointed at worker shm
that the TTL had already freed; forwarding the receipt later hit a dead
shm name. The fix pair -- event-driven release (this ADR) plus
materialize-on-receipt (0029) -- removed timer-based correctness from the
transport entirely.

The protocol is single-in-flight per worker ([ADR-0020](0020-concurrency-and-env-granularity.md)),
so `call_id` correlation is unambiguous: the parent acks exactly the reply
it just read, and the worker has at most a handful of un-acked calls
outstanding.

## Consequences

- Worker memory for a reply is held for microseconds-to-milliseconds (the
  read + ack round-trip) instead of a fixed 60 s, so peak shm footprint
  tracks in-flight replies, not wall-clock.
- Correctness no longer depends on the reader being "fast enough"; the TTL
  is now a pure liveness backstop and can be raised without risk.
- The protocol assumes the parent always acks a reply it consumed. A
  parent bug that reads-but-forgets-to-ack degrades to the old behavior
  (freed at TTL) rather than leaking -- a safe failure.
- **Double-crash shm leak (known gap):** if *both* parent and worker are
  SIGKILLed, no ack and no TTL sweep runs, so the reply's `/dev/shm`
  blocks leak until reboot. The startup sweep (`wrap.py`) reclaims stale
  sockets and temp dirs but not orphaned shm blocks; closing that is a
  small future item, tracked on the [roadmap](../../roadmap.md).
- Any future move off single-in-flight (the pending map,
  [ADR-0010](0010-wire-protocol-and-transport.md) v2 item 2) must preserve
  per-`call_id` ack semantics -- the keeper is keyed on it.
