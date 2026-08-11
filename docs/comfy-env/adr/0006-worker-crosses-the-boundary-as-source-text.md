# ADR-0006: Worker crosses the boundary as source text

**Status:** accepted

## Decision

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
  imported (it is stdlib-only at module scope), the parent already uses its
  shared walker, and the worker re-implements it by neglect. Deleting the
  worker's copy is v2 work item 3 in
  [ADR-0010](0010-wire-protocol-and-transport.md). The wire protocol
  (length-prefixed JSON + named strategies) is the contract; changes must
  land on both sides.
- Version skew between parent and worker code cannot happen: the parent
  always ships the worker source it was released with.
