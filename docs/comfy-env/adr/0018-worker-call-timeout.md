# ADR-0018: Worker call timeout -- the 600-second policy

**Status:** accepted (2026-08-13); the `[options] call_timeout` knob is
the implementation step, not yet landed

## Current behavior, stated honestly

Every proxied node call into a worker carries a timeout, defaulting to
**600 seconds**, hardcoded at the call sites (`SubprocessWorker.
call_module` / `call_method`: `timeout = timeout or 600.0`). No
configuration surface exists for it -- the only timeout in
`comfy-env.toml` today is `[options] health_check_timeout`, which
governs the 5-second liveness ping, a different thing.

When the timeout expires, the consequence is a guillotine, not a
retry: `_send_request` kills the worker process and permanently
retires the worker object (`self._shutdown = True`, the same flag a
deliberate shutdown sets), then raises `TimeoutError`. Everything
resident in that worker dies with it: loaded models, the by-reference
object cache, warm library state. The pool in `wrap.py` spawns a fresh
generation on the next call, which re-pays model load from disk.

The parent cannot do better *within the current protocol*: calls are
single-in-flight with no mid-call signal, so a node that is slow and a
worker that is hung are indistinguishable from outside. Killing is the
only safe response to the hung case -- a wedged native library (CGAL,
OCC, bpy) cannot be interrupted in-process on any platform we support.

## Decision

> **The timeout stays, the kill stays, the constant becomes the pack's
> to set.** A fixed 600 s is a policy about the caller's patience
> being imposed on workloads the caller knows nothing about.

1. `[options] call_timeout = <seconds>` in the env's
   `nodes/comfy-env.toml`, plumbed exactly like `health_check_timeout`
   (config parse -> `wrap.py` -> `SubprocessWorker` constructor ->
   default for `call_module`/`call_method`). Default remains 600.
2. Timeout expiry keeps its kill-the-worker semantics. The cache
   destruction is the documented cost of the only safe action, not a
   bug to soften.
3. The named successor is the mid-call heartbeat
   ([ADR-0010](0010-wire-protocol-and-transport.md) v2 item 9): once
   the worker can signal "alive, still computing" during a call, the
   timeout's job shrinks from "bound all computation" to "detect a
   dead heart," and slow-but-alive nodes stop being killable at any
   setting. Until then, `call_timeout` is the honest knob.

## Context

The 2026-08 adversarial review surfaced this as the largest gap
between documented reasoning and shipped behavior: "any node slower
than ten minutes is a crash that also destroys the pack's model and
object cache" appeared in no decision record, while the ecosystem's
own workloads (CGAL booleans, alpha wrap, high-density remeshing --
the flagship pack's daily bread) can legitimately exceed ten minutes
on dense inputs. A user losing a 9-minute computation to a hang is
served by the timeout; a user losing a 12-minute computation to the
*constant* is served by nobody.

Why per-env configuration rather than per-node: the env's
`comfy-env.toml` is the pack author's file, the pack author knows
which workloads are slow, and per-node knobs would push timeout policy
into node definitions comfy-env deliberately does not own. A pack with
one slow node sets the env-wide budget for it; the cost (other nodes
in that env inherit the long leash) only delays hang detection, never
correctness.

Why not remove the timeout entirely: a hung worker holds VRAM and a
lock; without a bound, one wedged native call silently freezes the
pack forever with no diagnostic. A loud kill after a declared budget
is strictly better than an invisible hang.

## Cancellation (amended 2026-08-14)

The same policy family, previously unwritten: **user cancellation is
cooperative-by-progress.** The parent checks ComfyUI's interrupt flag
only inside the progress-callback handler, and the worker raises only
when its progress hook receives the error back -- so **a node that
never reports progress is uncancellable**: the cancel button does
nothing until this ADR's timeout guillotines the worker. Decided, not
accidental: signal-based interruption of a wedged native library does
not work (the same reason timeout expiry kills rather than interrupts),
so cooperative cancel + kill-as-fallback is the honest pair. The
mid-call heartbeat (below) is the successor for *both* halves -- a
heartbeat channel is also a cancel channel, letting slow-but-alive
nodes see the interrupt without reporting progress.

## Consequences

- Packs with long-running nodes declare their patience once; losing a
  legitimate computation to a fixed constant stops being possible by
  design.
- The kill path's blast radius (model + object cache loss) is now
  documented where users and the future lifecycle ADR can see it,
  instead of living in a hardcoded constant's behavior.
- Setting a very large `call_timeout` trades hang detection for
  freedom -- acceptable per-pack, and obsoleted when the heartbeat
  lands (at which point the timeout reverts to a heartbeat-loss
  detector and this ADR gets a successor note).
- Node authors who want their slow nodes cancellable before the
  heartbeat exists have exactly one tool: report progress
  periodically. Worth a line in the pack-author docs.
