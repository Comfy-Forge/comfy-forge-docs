# Worker lifecycle

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
workflows, even `/object_info` never *spawn* a worker -- proxies answer
from the [metadata snapshot](live-dropdowns.md). They may *talk* to one,
though: a proxy's `INPUT_TYPES` calls `_refresh_combo_options`, which
sends `refresh_input_types` to this env's worker only if it is already
alive **and** idle (`send_command_no_spawn`), and falls back to the cached
options when the worker is busy, dead, or was never started. A worker
comes into existence only once a node from its env actually **executes**.

The first call pays for: materializing the worker source into a temp dir
([ADR-0006](adr/0006-worker-crosses-the-boundary-as-source-text.md)),
launching the env's interpreter (which imports torch -- the dominant cost,
seconds to tens of seconds), the auth handshake, the config push, and the
transport canary ([the spawn-time channel](process-boundary.md#the-spawn-time-channel)).
A worker whose CPU-tier canary fails verification is refused, not used
(`verify_transport` raises). A GPU-tier canary failure, or a device-UUID
mismatch between parent and worker, is a *demotion*: the worker is still
used, with `gpu_zero_copy_ok` cleared so its CUDA tensors take the CPU
shared-memory path.

## Life: warm, single-file, and paid for

Once up, the worker stays alive across executions -- that is the point:
the second call skips the torch import entirely. The standing cost of a
warm worker is **~180-550 MB host RAM plus a CUDA context** (and VRAM for
whatever models it holds).

That VRAM is released two ways, not one. A host-side idle sweep (a 10 s
daemon timer in `pool.py`) sends `full_release` to workers that have sat
idle long enough, and the worker gives back everything it holds, **and**
ComfyUI's own eviction reaches into the worker from outside: comfy-env
registers a stand-in for each of the worker's models in
`current_loaded_models`, so upstream's `free_memory` can unload it exactly
as it unloads a host model
([comfy-env's memory management](memory-approach.md),
[ADR-0038](adr/0038-the-memory-floor.md)). An earlier draft of this line
said outside eviction no longer happens; that describes a design that was
reversed before it shipped.

- **One call at a time.** A worker serves a single in-flight call
  ([ADR-0020](adr/0020-concurrency-and-env-granularity.md)). Eviction
  commands are a partial exception: they are answered mid-call only while
  the worker is blocked in `_call_parent` waiting on its own callback
  (progress, VRAM budget). A worker in pure compute answers nothing, and a
  parent thread trying to `send_command` to it gives up after
  `_COMMAND_LOCK_TIMEOUT` (30 s) rather than block ComfyUI.
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
| 1 | **Crash or timeout** (segfault in a native lib, [ADR-0018](adr/0018-worker-call-timeout.md) kill) | The in-flight call fails with a named error; the worker object is permanently retired. The next call gets a **fresh worker with a bumped generation** -- the caller never sees a dead worker, only a new one. The crash costs the call and the worker's caches, nothing the parent holds. Every kill path -- this one, the health-check restart, the accept timeout after spawn, and shutdown -- goes through one `_kill_tree()`: the worker is spawned in its own process group, so the kill reaches the Python under the `pixi run` wrapper, not just the wrapper (until 2026-09-12 it reached only the wrapper, and the real worker lived on with its VRAM, parent pid 1). |
| 2 | **Clean ComfyUI stop** (Ctrl-C, normal exit) | An atexit hook sends every worker a `shutdown` frame, waits 5 s, then kills; temp dirs are removed. Workers die with the parent. |
| 3 | **ComfyUI killed hard, worker idle** (SIGKILL, crash, OOM -- atexit never runs) | The idle worker is blocked reading its socket; the parent's death closes it, the read fails, and the worker's own loop exits promptly. No parent needed. |
| 4 | **ComfyUI killed hard, worker mid-computation** | The worker is not reading the socket, so it does not notice. It **finishes the running call for nobody** -- holding its RAM and VRAM the whole time -- and only exits when it tries to *send* its reply: `transport.send` raises on the dead socket, the error handler tries to send an error frame, that raises again unhandled, and the process dies. A worker deep in a 30-minute bake outlives its parent by up to 30 minutes. |
| 5 | **The sweep at next startup** | The backstop for anything left behind: the next `register_nodes()` runs `_cleanup_stale_workers`, which kills `persistent_worker.py` processes whose parent pid no longer exists, unlinks dead-owner socket files (macOS only -- the `unix://` filename embeds the owning pid; Linux uses the abstract namespace and leaves no file), and removes `comfyui_pvenv_*` temp dirs no live process has in its cwd or command line. Orphans are recognised by the **host pid baked into the worker's temp-dir name** (`comfyui_pvenv_<hostpid>_…`), not by the immediate parent: under `pixi run` a worker whose wrapper died has parent pid 1 and one whose host died has a live wrapper, so a parent test caught neither. A worker whose host is gone is killed as a group. Workers from an older comfy-env, with no host pid in the name, fall back to the parent test. |

## The subtlest rule: what replacement must preserve

When a worker is replaced (case 1), its
[`SubprocessModelPatcher`](adr/0035-duck-typed-model-proxy.md)s are
*deregistered but deliberately kept alive*, because the restart can fire
**inside** ComfyUI's `free_memory` iteration -- mutating the model list or
letting the old patchers be garbage-collected mid-iteration would corrupt
upstream's loop.
Stale patchers are quarantined as already-offloaded and released on the
next registration. ADR-0019 records both halves; any change to restart
handling must keep them.

## Where to go next

- [ADR-0019](adr/0019-worker-lifecycle.md) -- the lifecycle decision record,
  including the `_STALE_PATCHERS` invariant in full.
- [The process boundary](process-boundary.md) -- everything that crosses
  during each phase of this lifespan.
- [comfy-env's memory management](memory-approach.md) -- what a worker's models cost and
  who can evict them.
