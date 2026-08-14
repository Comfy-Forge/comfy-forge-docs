# ADR-0006: comfy-env is never installed into worker envs

**Status:** accepted

## Decision

> **comfy-env is never installed into worker envs -- the worker crosses
> the boundary as source text, never as an import.** Not comfy-env
> installed in every env (envs stay minimal and node-defined); not a
> shared package (the two interpreters may not even be the same Python)
> -- the parent ships the exact worker source it was released with,
> making version skew structurally impossible.

- The worker lives in a real module, `workers/_persistent_worker.py`
  (~1850 lines), but is **never imported by the parent**. The parent reads it
  as text at import time (`subprocess.py:106-109`) and materializes it into a
  temp directory, where the isolated interpreter executes it as a script.
- The small stdlib-only helper layer, `workers/_ipc_shared.py`, is **copied**
  into the same temp directory so the worker can `import _ipc_shared`
  directly. It deliberately imports nothing from comfy-env.
- The serialization stack exists **twice by design**: parent-side in
  `workers/_ipc_parent.py`, worker-side inside `_persistent_worker.py`. They
  implement the same wire protocol ([ADR-0005](0005-tiered-tensor-serialization.md))
  against potentially different torch builds.

## Context

The worker program runs in the isolated env's interpreter, which does **not
have comfy-env installed** -- the whole point is that the isolated env
contains only what the node declared. So the worker cannot `import comfy_env`
and share code with the parent the normal way. The two interpreters may even
be different Python versions with different installed packages.

An earlier iteration embedded the entire worker as a giant string constant
(`_PERSISTENT_WORKER_SCRIPT`) inside `subprocess.py` (~2500 lines in one
file) -- unreadable, unlintable, undiffable.

## Consequences

- The worker file gets real tooling again: syntax highlighting, ruff, diffs.
- comfy-env does not need to be installed (or even installable) in isolated
  envs; envs stay minimal and node-defined.
- The duplicated serialization logic must be kept in sync by hand. *2026-08
  correction:* the review showed this "standing tax" framing is half false --
  shipping the worker as source text does NOT force duplicating the stack.
  `_ipc_shared.py` is copied beside the worker precisely so it can be
  imported (it is stdlib-only at module scope), and *2026-08 update:* the
  worker's `_to_shm` now delegates to the shared walker. Residual forks:
  `SocketTransport` (the worker's copy lacks the parent's
  `MAX_MESSAGE_SIZE` check) and the side-specific `_from_shm` halves --
  finishing this is v2 work item 3 in
  [ADR-0010](0010-wire-protocol-and-transport.md). The wire protocol
  (length-prefixed JSON + named strategies) is the contract; changes must
  land on both sides.
- Version skew between parent and worker code cannot happen: the parent
  always ships the worker source it was released with. *This -- not the
  "different Pythons" line above -- is the strongest argument for
  source-text delivery, together with upgrade reach: `pip install -U
  comfy-env` upgrades the worker code for every env instantly, including
  envs materialized months ago, with no re-install. (A pinned
  `comfy-env-worker` wheel inside each env -- the main rejected
  alternative -- would rot against an upgraded parent and make a
  version handshake load-bearing.)*
- A constraint this delivery mechanism implies but nothing enforces:
  the worker and `_ipc_shared.py` must stay parseable by the **oldest
  Python any worker env uses** (the motivating examples above include
  3.9). CI runs host Pythons only; a `py_compile` lane at the floor
  version is the planned guard.
