# ADR-0020: Concurrency and env granularity -- the pack is the unit

**Status:** accepted (2026-08-13) -- records two coupled decisions that
shipped undocumented; the content-addressed env-identity direction is
accepted but unimplemented.

## Decision

> **The pack is the unit of concurrency, and the unit of fate.** One
> worker, one lock, per environment: everything a pack does serializes
> through a single subprocess, and everything that dies when that
> subprocess dies belongs to that one pack. Disk may be shared;
> processes are not.

### Concurrency: one worker + one lock per env

`_WORKER_POOL` is keyed by env dir; each entry is one `SubprocessWorker`
guarded by one reentrant lock. Every path into a worker -- the
executor's node call, an aiohttp proxy route, a VRAM-eviction
`send_command` re-entering mid-call (the reason the lock is an RLock),
the metadata scan -- serializes on it. Stated plainly, the consequences
nobody wrote down before:

- **Two nodes of the same pack can never execute concurrently**, even
  if ComfyUI's executor someday runs graph branches in parallel.
  Cross-pack parallelism exists (different envs, different workers);
  intra-pack parallelism does not.
- **A pack's HTTP routes block behind any running node call** from the
  same pack, up to the call timeout
  ([ADR-0018](0018-worker-call-timeout.md)).
- CPU-bound work serializes per pack regardless of core count; the
  parallelism story for heavy geometry is *inside* the native library
  (OpenMP etc.), not across nodes.

Rejected alternatives, with reasons so they stay rejected:

- **N workers per env**: multiplies the standing cost ADR-0001
  measured (~420 MB torch import + CUDA context *per worker*),
  fragments the by-reference object cache and model residency across
  workers (a mesh handle created in worker 1 is unreachable from
  worker 2), and turns "the pack crashed" into "some fraction of the
  pack crashed." Revisit only when a real workload demonstrates
  intra-pack parallelism worth those costs -- and then as an explicit
  opt-in with cache affinity, not a default.
- **Worker-per-node-class**: the same costs at finer grain; model
  residency (the whole point of persistence, ADR-0001) dies.
- **Async multiplexing over one worker**: changes nothing -- the
  worker executes one node at a time regardless; only the *dispatch*
  bookkeeping improves (that part is wanted: ADR-0010 v2 item 2).

### Granularity: env identity today, content-addressing accepted

Env identity is `<plugin>-<subdir>` plus an ABI tag
(`environment/cache.py`) -- **per pack**, even when two packs resolve
to byte-identical dependency closures. The 2026-08 review priced this:
pixi/uv hardlinking already dedupes most *disk*, so the real duplicate
cost is standing *process* memory -- which is a worker question, not an
env question, and the answer above (plus the idle reaper,
[ADR-0019](0019-worker-lifecycle.md)) addresses it.

Accepted direction: **content-addressed env identity** -- env = hash of
the resolved dependency closure, pack names become aliases -- so
identical closures share one materialized env on disk. Explicitly
bounded: env-sharing does **not** imply worker-sharing. Merging workers
across packs was considered and rejected: one shared `sys.modules`
namespace reintroduces the collision class
[ADR-0015](0015-declared-wire-types.md) just eliminated at the
serializer layer, and -- decisive -- a CGAL segfault in pack A would
destroy pack B's resident models mid-workflow. Fate isolation per pack
is the product ([ADR-0001](0001-process-isolation-via-persistent-subprocess-workers.md)),
not an implementation accident.

The counterargument to content-addressing, recorded so the future
implementer prices it: shared env identity couples rebuilds -- one pack
adding a dependency changes its closure hash and re-materializes what
was previously shared, so "shared" env disk is a cache, never a
guarantee, and the alias layer must handle two packs diverging without
either noticing.

## Context

The 2026-08 dossiers converged on "the unit of concurrency is the whole
pack" as the most load-bearing decision never written anywhere. The
single-in-flight *transport* was canonized in ADR-0010, but its
premise -- one caller at a time -- is already violated by three
concurrent entry paths, all made safe by the one lock; and the
`aiohttp` route path gives the latent desync a real trigger, which is
why the pending-map work is scheduled. Meanwhile the per-pack *memory*
cost argued for env dedup, and the review's cross-examination
established that the honest split is: dedupe disk by content, never
merge processes.

## Consequences

- Pack authors can rely on ordering: within a pack, node executions and
  route handlers never interleave. (Some packs implicitly depend on
  this today; it is now a promise instead of an accident.)
- When ComfyUI's executor parallelizes, comfy-env's story is
  cross-pack parallel, intra-pack serial -- and the pending map is the
  prerequisite for even that being safe.
- *Resolved 0.4.18 (was a constraint on that promise):* the parent's
  serialization layer used to hold per-call protocol state in shared
  module globals (`_active_worker_pool`, `_gpu_zero_copy_demoted`), safe
  under one lock but racy across two workers' locks (a pack-A route call
  concurrent with a pack-B node call could cross them). That state moved
  to a thread-local `_call_state`, so the concurrent-deserialize race is
  closed. The remaining prerequisite for real cross-pack parallelism is
  the pending map (ADR-0010 v2 item 2), which must preserve this
  per-call scoping.
- The object cache and model registry stay single-worker concepts;
  nothing needs distributed invalidation.
- A future content-addressed store changes `environment/cache.py`
  identity and the workspace layout ([ADR-0007](0007-machine-wide-workspace-with-per-env-manifests.md))
  but must not change pool keying: `_WORKER_POOL` stays keyed by pack
  env, one worker each, whatever disk they share.
