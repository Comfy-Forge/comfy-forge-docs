# ADR-0021: The three-call contract

**Status:** accepted (2026-08-13) -- records the public API as shipped
since v0.x; it predates every other ADR in practice.

## Decision

> **A pack adopts comfy-env with three one-liners, and the file layout
> is the configuration.** No subclassing, no decorators, no
> registration API, no paths passed around -- each call infers the
> calling pack from the caller's own file location.

The entire pack-facing surface:

```python
# install.py
from comfy_env import install
install()

# prestartup_script.py
from comfy_env import setup_env
setup_env()

# __init__.py
from comfy_env import register_nodes
NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS = register_nodes()
```

- `install()` -- the install-time half: node-pack dependencies
  ([ADR-0016](0016-node-pack-dependencies.md)), then workspace
  materialization ([ADR-0007](0007-machine-wide-workspace-with-per-env-manifests.md)).
- `setup_env()` -- the prestartup half, running before ComfyUI's own
  imports: faulthandler, env hygiene, libomp dedupe.
- `register_nodes()` -- the runtime half: config discovery, `[types]`
  validation and serializer loading
  ([ADR-0015](0015-declared-wire-types.md)), metadata scan + proxy
  synthesis ([ADR-0023](0023-metadata-scan-and-proxy-synthesis.md)),
  worker pool wiring. Returns the two mappings ComfyUI expects, so the
  pack's `__init__.py` stays idiomatic.

**The mechanism, named honestly: stack-frame inspection.** Each call
takes `inspect.stack()[1].filename` and resolves the pack directory
from the *caller's file* (`register_nodes` in `wrap.py`; `setup_env`
falls back the same way when `node_dir` is omitted). That is the whole
trick -- the three files sit at the pack root, so frame filename ->
pack root -> configs, envs, `serialization.py`, everything.

Known failure modes of the trick, so nobody rediscovers them in
production:

- The calls must appear **directly in the pack's own files**. A shared
  helper that calls `register_nodes()` on a pack's behalf resolves to
  the *helper's* location. (Symmetrically, this is why the calls are
  one-liners: there is nothing worth wrapping.)
- Anything that divorces code from its file -- `exec` of loaded
  source, frozen/zipped importers, aggressive REPL use -- breaks
  inference. `setup_env(node_dir=...)` exists as the explicit escape
  hatch; the other two have not needed one yet, and adding parameters
  preemptively was rejected as boilerplate creep.

## Context

The alternatives all trade the one-liner away:

- **Explicit paths** (`register_nodes(__file__)` or a path argument):
  works everywhere, but every pack copies the same incantation and
  copy-paste drift becomes a support category. The implicit form has
  one implementation of the inference instead of fifty copies of the
  argument.
- **Package metadata / entry points**: ComfyUI packs are not installed
  Python packages -- they are directories dropped into `custom_nodes/`
  -- so `pyproject.toml` entry points have nothing to bind to.
- **A plugin base class**: more API surface, and it inverts the
  adoption story -- packs would import comfy-env types throughout
  instead of at three well-known files.

The 2026-08 review flagged that this -- the most user-visible design in
the project -- had no decision record, while noting the design itself
is good. This ADR is the record.

## Consequences

- Adoption cost is as close to zero as the ecosystem allows; the three
  files are also exactly the three hooks ComfyUI/Manager already
  execute, so comfy-env needs no loader integration of its own.
- The contract is stable API in the
  [ADR-0017](0017-pre-1-0-no-backward-compatibility.md) sense: even
  pre-1.0, these three signatures are the last thing that would ever
  change, because every pack's first three lines depend on them.
- `inspect.stack()` at import time costs microseconds and is paid three
  times per pack per boot -- not a performance surface.
- Tooling that loads pack files unconventionally (test harnesses,
  documentation generators) must either mimic the file layout or use
  the explicit `node_dir` escape hatch; comfy-test does the former.
