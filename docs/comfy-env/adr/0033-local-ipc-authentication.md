# ADR-0033: Local IPC authentication

**Status:** accepted (shipped 0.4.18) -- records the threat model and the
handshake design, which lived only in code and a `fixbugs`-branch mention
in [ADR-0026](0026-trust-and-supply-chain.md).

## Decision

> **The parent proves who it is talking to before it speaks the protocol.**
> A per-spawn random authkey is verified as the worker's first frame; on
> Linux the kernel's peer credentials are checked too; the address and key
> travel through the worker's environment, never its command line.

Concretely (`subprocess.py`, worker `_persistent_worker.py`):

- At spawn the parent mints `secrets.token_hex(32)` and passes it plus the
  socket address in the worker's **environment**
  (`COMFY_ENV_IPC_ADDR` / `COMFY_ENV_IPC_AUTHKEY`) -- **not argv**, which
  is world-readable via `/proc/<pid>/cmdline`.
- The worker connects and sends `{"authkey": ...}` as its **first frame**;
  the parent rejects the connection (and kills the worker) if it is absent
  or wrong, before any payload is deserialized.
- On Linux `AF_UNIX` the parent additionally checks `SO_PEERCRED` and
  refuses a peer whose uid is not the parent's own.

## Context -- the threat this closes

The worker socket is reachable by other local processes, and the channel
carries **pickled payloads** straight into `pickle.loads` in the ComfyUI
process (the rung-5 fallback, [ADR-0005](0005-tiered-tensor-serialization.md)).
Before 0.4.18 the parent did a bare `accept()` with no peer check, so:

- On Linux the socket is an **abstract-namespace** socket -- no filesystem
  permissions, and its name is enumerable by any local user via
  `/proc/net/unix`; the address was also on the worker's argv.
- On Windows the fallback is a **TCP loopback** listener, connectable by
  any local process.

So any local process could connect first and feed a pickle to the parent
-- a **cross-user local code-execution path on the multi-user Linux
servers** [ADR-0008](0008-graceful-degradation-everywhere.md) names as an
audience. Note this is *not* covered by
[ADR-0011](0011-isolation-before-sandboxing.md)'s "no regression vs
vanilla" argument: vanilla ComfyUI does not listen on a pickle-speaking
socket, so this was a hole comfy-env *introduced* and therefore owes a
fix, not a deferral.

## What is covered, and the residual Windows gap (stated honestly)

- **Linux `AF_UNIX`:** strong. `SO_PEERCRED` blocks a foreign-uid peer at
  the kernel level, and the authkey blocks a same-uid impostor that
  guessed the enumerable socket name. Both must pass.
- **Windows TCP loopback:** the authkey is the **only** gate -- Windows
  has no `SO_PEERCRED` equivalent wired here. The key lives in the child's
  environment block, which is **not** readable across users by default, so
  cross-user connection is still blocked; but a **same-user** process can
  read another same-user process's environment and could in principle
  replay the key. On a single-user desktop (the Windows norm) that is
  self-attacks-self, i.e. nothing; on a shared Windows host it is a weaker
  guarantee than Linux, and multi-user Windows deployments should know
  that.

## Not a sandbox

This authenticates the *peer*; it does not sandbox the *worker*. A
compromised or malicious worker still owns the parent, because the
transport hands it a pickle rung -- that is [ADR-0011](0011-isolation-before-sandboxing.md)'s
scope, and the pickle-rung-opt-in precondition recorded in
[ADR-0026](0026-trust-and-supply-chain.md) is what eventually closes it.
Authentication answers "is this my worker?", not "is my worker safe?".

## Consequences

- The multi-user-server caveat that
  [ADR-0026](0026-trust-and-supply-chain.md) put on comfy-env is lifted
  for the *authentication* dimension on Linux; it remains, weaker, for
  Windows same-user (above) and entirely for the pickle-rung dimension
  (0011).
- Tested by `tests/test_ipc_security.py` (wrong-key rejection; the address
  absent from argv; the worker sends the key as its first frame).
- Any transport rework (the pending map) must keep the authkey as the
  first frame and the address out of argv; both are load-bearing, not
  incidental.
- The Windows peer-credential gap and the pickle-rung dependency are the
  two items that would move on the [ADR-0017](0017-pre-1-0-no-backward-compatibility.md)
  security clock at the rollout tripwire.
