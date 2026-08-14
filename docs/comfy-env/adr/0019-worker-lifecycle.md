# ADR-0019: Worker lifecycle

**Status:** accepted (2026-08-13) -- records behavior that shipped
undocumented; the idle reaper is decided here as direction, not yet
implemented. Companion: [ADR-0018](0018-worker-call-timeout.md)
(timeout policy), [ADR-0020](0020-concurrency-and-env-granularity.md)
(what a worker *is*).

## Decision

> **A worker is disposable and its replacement is invisible.** Spawned
> lazily on first use, verified before trusted, killed without ceremony
> on crash or timeout, and replaced behind a generation counter -- the
> caller never handles a dead worker, only a fresh one. Everything
> resident in a worker (models, object cache, JIT state) is a cache
> that must be rebuildable, never the only copy of anything.

The state machine, as shipped:

1. **Absent -> spawning** (lazy): `_ensure_started()` on first call.
   Spawn writes the worker source + `_ipc_shared.py` to a temp dir
   ([ADR-0006](0006-worker-crosses-the-boundary-as-source-text.md)),
   launches the env's interpreter, exchanges config, and runs the
   canary handshake ([ADR-0005](0005-tiered-tensor-serialization.md))
   -- a worker whose CPU tier fails verification is refused, not used.
2. **Ready -> serving**: calls are single-in-flight per worker
   (ADR-0020). Every call currently begins with a health ping
   (ADR-0010 defect list; scheduled for removal with the pending-map
   work). Response frames for call N are pinned worker-side until the
   parent's `{"type": "consumed", "call_id": N}` ack; the 60 s TTL
   sweep is the crash fallback only (0.4.15 -- timer-based correctness
   is not to be reintroduced).
3. **Dead** (crash, timeout kill per ADR-0018, or EOF): the
   `SubprocessWorker` object is permanently retired. The pool
   (`_WORKER_POOL`, keyed by env dir) swaps in a fresh worker with a
   **bumped generation** on the next call; stale generations are never
   resurrected.
4. **Replacement bookkeeping -- the `_STALE_PATCHERS` invariant** (the
   subtlest rule in the codebase, promoted here out of a comment in
   `wrap.py:_cleanup_stale_patchers`): when a worker is replaced, its
   `SubprocessModelPatcher`s are *deregistered but deliberately kept
   alive*. The restart callback can fire **inside** ComfyUI
   `free_memory`'s iteration over `current_loaded_models`
   (`model_unload -> send_command -> _ensure_started -> _on_restart`),
   so (a) the list must not be mutated from the callback -- captured
   indices would be invalidated -- and (b) the old patchers must not be
   GC'd -- `LoadedModel._model` is a weakref, and the finalizer would
   pop entries mid-iteration. Stale references are released on the next
   patcher registration. Any change to restart handling must preserve
   both halves.
5. **Log plumbing is part of the protocol**: the worker hijacks
   `builtins.print` and the root logger into synchronous in-band
   socket sends, so worker output arrives ordered with protocol frames
   instead of interleaving on stderr. Cost, accepted knowingly: a
   chatty native library inflates call latency, because its output
   rides the RPC stream.

**The idle reaper -- decided as direction.** ADR-0001 measured the
standing cost of a warm worker (~180-550 MB RAM plus a CUDA context and
VRAM where applicable) and named reaping "the refinement worth having";
nobody ever decided it. Decision: comfy-env **will** reap workers after
a configurable idle window (models evicted through the normal patcher
machinery first, so nothing is lost that ComfyUI didn't already
consider evictable), with re-spawn on next use indistinguishable from
first use. Unscheduled; recorded so the next person touching the pool
builds toward it rather than away from it.

## Context

Both 2026-08 review dossiers independently ranked the undocumented
lifecycle as the largest gap between shipped behavior and recorded
reasoning: restart semantics were mentioned in five ADRs and specified
in none, the `_STALE_PATCHERS` dance existed only as a code comment,
and "what does a crash cost mid-workflow" had no written answer. The
answer, made explicit: a crash costs the in-flight call (a named
error), the worker's resident caches (rebuilt on demand), and nothing
held by the parent -- materialized receipts
([ADR-0015](0015-declared-wire-types.md)) survive the producer's death
by construction.

## Consequences

- Crash tolerance is a product feature, not error handling: native
  geometry libraries (CGAL, OCC, bpy) segfault on degenerate input as
  a matter of routine, and the blast radius is one call + one pack's
  caches.
- Anything a worker holds must be reconstructible: model paths and
  config dicts cross the boundary, live handles do not (confirmed
  ecosystem-wide by the 2026-08 census) -- worker death therefore
  never strands unique state.
- The generation counter is the invalidation authority: any future
  by-reference scheme must tag references with it and fail loudly on
  mismatch (the census killed by-reference for now; if it returns,
  this is its contract).
- Two hand-maintained interleave loops (parent receive, worker
  `_call_parent`) dispatch protocol frames today; the pending-map work
  (ADR-0010 v2 item 2) replaces both and removes the per-call ping.
  This ADR constrains that work: replacement must preserve the
  consumed-ack lifetime protocol and the restart invariants above.
