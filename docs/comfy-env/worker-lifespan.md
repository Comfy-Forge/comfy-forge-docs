# Worker lifespan

*When a worker is born, what it costs while alive, and every way it dies --
including what happens to it when ComfyUI itself stops. The policy is
[ADR-0019](adr/0019-worker-lifecycle.md); this page is the plain-language
tour, with the crash cases spelled out.*

The one-sentence contract, from the ADR: **a worker is disposable and its
replacement is invisible** -- spawned lazily on first use, verified before
trusted, killed without ceremony, replaced behind a generation counter.
Everything resident in a worker is a cache that must be rebuildable, never
the only copy of anything.

## Birth: lazy, verified, then trusted

Nothing spawns at ComfyUI startup. Browsing the node menu, loading
workflows, even `/object_info` never touch a worker -- proxies answer from
the [metadata snapshot](dynamic-combos.md), and the live parts (file
dropdowns) are re-computed parent-side. A worker exists only once a node
from its env actually **executes**.

The first call pays for: materializing the worker source into a temp dir
([ADR-0006](adr/0006-worker-crosses-the-boundary-as-source-text.md)),
launching the env's interpreter (which imports torch -- the dominant cost,
seconds to tens of seconds), the auth handshake, the config push, and the
transport canary ([the spawn-time channel](process-boundary.md#the-spawn-time-channel)).
A worker whose canary fails verification is refused, not used.

## Life: warm, single-file, and paid for

Once up, the worker stays alive across executions -- that is the point:
the second call skips the torch import entirely. The standing cost of a
warm worker is **~180-550 MB host RAM plus a CUDA context** (and VRAM for
whatever models it holds, which ComfyUI can evict through the
[patcher machinery](sharing-one-gpu.md)).

- **One call at a time.** A worker serves a single in-flight call
  ([ADR-0020](adr/0020-concurrency-and-env-granularity.md)); eviction
  commands are the exception, answered even mid-call.
- **Health checks are idle-only.** A worker idle for more than 60 s gets a
  `ping` before its next call; a busy worker is never pestered.
- **What accumulates inside** -- loaded models, the object cache, JIT state
  -- is all rebuildable. That invariant is what makes every death below
  survivable.

An **idle reaper** (kill workers untouched for a configurable window,
respawn indistinguishable from first use) is decided as direction in
ADR-0019 but not yet built: today a warm worker lives until something on
this page kills it.

## Death, all five ways

| # | Trigger | What happens |
|---|---|---|
| 1 | **Crash or timeout** (segfault in a native lib, [ADR-0018](adr/0018-worker-call-timeout.md) kill) | The in-flight call fails with a named error; the worker object is permanently retired. The next call gets a **fresh worker with a bumped generation** -- the caller never sees a dead worker, only a new one. The crash costs the call and the worker's caches, nothing the parent holds. |
| 2 | **Clean ComfyUI stop** (Ctrl-C, normal exit) | An atexit hook sends every worker a `shutdown` frame, waits 5 s, then kills; temp dirs are removed. Workers die with the parent. |
| 3 | **ComfyUI killed hard, worker idle** (SIGKILL, crash, OOM -- atexit never runs) | The idle worker is blocked reading its socket; the parent's death closes it, the read fails, and the worker's own loop exits promptly. No parent needed. |
| 4 | **ComfyUI killed hard, worker mid-computation** | The worker is not reading the socket, so it does not notice. It **finishes the running call for nobody** -- holding its RAM and VRAM the whole time -- and only exits when it returns to the socket for the next request. A worker deep in a 30-minute bake outlives its parent by up to 30 minutes. |
| 5 | **The sweep at next startup** | The backstop for case 4 and anything else left behind: the next `register_nodes()` reaps workers whose parent pid no longer exists, plus their dead-owner socket files and unused temp dirs -- the pid is embedded in the socket filename precisely so this check is possible. |

## The subtlest rule: what replacement must preserve

When a worker is replaced (case 1), its
[`SubprocessModelPatcher`](sharing-one-gpu.md)s are *deregistered but
deliberately kept alive*, because the restart can fire **inside** ComfyUI's
`free_memory` iteration -- mutating the model list or letting the old
patchers be garbage-collected mid-iteration would corrupt upstream's loop.
Stale patchers are quarantined as already-offloaded and released on the
next registration. ADR-0019 records both halves; any change to restart
handling must keep them.

## Where to go next

- [ADR-0019](adr/0019-worker-lifecycle.md) -- the lifecycle decision record,
  including the `_STALE_PATCHERS` invariant in full.
- [The process boundary](process-boundary.md) -- everything that crosses
  during each phase of this lifespan.
- [Sharing one GPU](sharing-one-gpu.md) -- what a worker's models cost and
  who can evict them.
