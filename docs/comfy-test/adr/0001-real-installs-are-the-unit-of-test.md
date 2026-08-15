# ADR-0001: Real installs are the unit of test

**Status:** accepted (2026-08)

## Decision

> **Every level runs against a real virtual environment, a real ComfyUI
> checkout, and (from REGISTRATION onward) a live server process.**
> comfy-test never imports a mocked `comfy` module and never asserts against
> a stubbed node registry.

## Context

The bug class that breaks ComfyUI node packs in the wild is almost never
"the function returns the wrong tensor". It is:

- the pack does not install on Python 3.13 / Windows / a fresh venv;
- an import at module scope pulls a package that is not in
  `requirements.txt`;
- the node registers under a name that collides, or fails to register at
  all;
- it works on the author's machine, which has fifteen other packs
  installed.

None of those are reachable from a unit test that imports the pack with
`comfy` stubbed out. They are all install-time and import-time facts about a
real environment.

## Alternatives rejected

- **pytest with a mocked ComfyUI.** Cheap, fast, and blind to the entire
  failure class above. It also demands that packs be written to be
  importable without ComfyUI, which is a constraint on the author for the
  convenience of the harness.
- **Container images per pack.** Reproducible, but wrong on the axis that
  matters: users do not run packs in our container, they run them in their
  own ComfyUI install on Windows. (Containers survive as an optional GPU
  *host* mechanism -- see [ADR-0013](0013-desktop-is-driven-over-cdp.md) and
  the GPU host docs -- not as the unit of test.)
- **Testing only the node functions, with the author supplying fixtures.**
  Moves the interesting failures out of scope and makes the harness's
  verdict depend on fixture quality.

## Consequences

- Runs are slow and stateful. A CPU lane spends most of its wall-clock
  installing, which is what forced the attach/fresh split
  ([ADR-0003](0003-two-install-paths-attach-and-fresh.md)).
- Failures can come from the ecosystem rather than the pack: PyPI outages,
  a ComfyUI HEAD change, a torch release. That is a true cost, not a bug --
  those breakages *are* what users hit.
- The harness needs a real workflow to drive
  (`workflows/test.json`), so adoption requires an artifact from the author,
  not just a config file.
- Every level therefore has an environment prerequisite, which is what makes
  the pipeline ordered ([ADR-0002](0002-levels-are-an-ordered-pipeline.md)).
