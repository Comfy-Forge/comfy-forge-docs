# ADR-0002: pixi as environment manager

**Status:** accepted

## Context

Isolated node environments must be able to use ComfyUI functions and return
types, which drags in packages like `av` and `ffmpeg` -- native dependencies
that **cannot be installed from PyPI**. The README states it bluntly: "Using
conda CANNOT be avoided."

Alternatives:

- **venv + pip/uv only** -- cannot provide ffmpeg, CGAL, Blender's `bpy`,
  mesa, and similar conda-forge-only native stacks.
- **conda/mamba directly** -- solves the native problem but PyPI interop is
  bolted on, solves are slower, and there is no single-manifest,
  single-lockfile story across both ecosystems.

## Decision

Use [pixi](https://pixi.sh): a fast Rust-based manager that speaks
**conda-forge and PyPI in the same `pixi.toml`**, ships a real lockfile
(reproducible envs across machines), installs entirely per-user with no
admin rights or system-Python pollution, and uses
[uv](https://github.com/astral-sh/uv) underneath for the PyPI side.

Supporting choices:

- The pixi binary is **self-bootstrapped** (`packages/pixi.py`): downloaded
  to `~/.pixi/bin/` from GitHub latest-release URLs if missing, so `pip
  install comfy-env` is the only prerequisite.
- comfy-env acts as a **manifest compiler** (`packages/toml_generator.py`):
  it generates `pixi.toml` files rather than driving a package API. Unknown
  keys in `comfy-env.toml` are intended to pass through to the generated
  manifest untouched (in v0.4 only an allowlist actually does -- see
  [ADR-0003](0003-two-config-files-with-two-roles.md))
  ([ADR-0003](0003-two-config-files-with-two-roles.md)), so pixi's full
  feature set stays reachable without comfy-env schema changes.
- uv is also used directly for main-env pip work (`install/helpers.py`
  `_find_uv()`), with plain pip as fallback.

## Consequences

- One manifest and one `pixi.lock` per env cover both conda and PyPI deps.
- Env materialization is fast (uv-backed) and reproducible.
- comfy-env depends on GitHub availability to bootstrap pixi on first run.
- Anything pixi cannot express is out of scope by construction; in practice
  the passthrough design has kept the config schema tiny.
