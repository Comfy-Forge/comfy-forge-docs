# ADR-0002: pixi as environment manager

**Status:** accepted

## Context

Conda cannot be avoided -- but for precise, enumerable reasons, not "some
packages aren't on PyPI." A fleet-wide audit (30 env manifests across the
maintainer's packs, 2026-08) found the genuinely conda-only surface is 14
package names across 8 repos, every one attributable to one of **three
pillars**:

1. **Non-Python system libraries with no wheel form.** Wheels package
   Python distributions; these are not Python. The headless GL/X stack
   (`mesalib`, `libglu`, `libglvnd`, `xorg-libsm`), `libstdcxx-ng`, and
   `pythonocc-core` (no PyPI distribution exists at any version). It also
   captures coupling cases: PanoPack needs conda `vtk` *because* conda
   VTK's RPATH reaches `$CONDA_PREFIX/lib` to dlopen conda's `libOSMesa` --
   a PyPI vtk wheel structurally cannot.
2. **Copyleft native libraries that cannot be legally vendored into
   wheels.** `cgal` and `bpy` are GPL. The wheel model vendors the native
   library INTO the artifact, fusing a GPL derivative work -- forcing
   copyleft or a commercial license onto the wheel and its consumers.
   Conda's separate-package model keeps the copyleft boundary at
   install-time aggregation by the *user's* package manager, with
   conda-forge carrying source-availability compliance.
3. **Root-free delivery of native toolchains.** Install-time compilation on
   end-user machines needs `c-compiler`/`cxx-compiler` and CUDA dev
   packages (`cuda-nvcc`, `cuda-cccl`, `cuda-cudart-dev` -- e.g. VoMP
   building `diff_gaussian_rasterization` at install time), plus custom
   native builds (`occt-rt`). conda-forge is the only channel delivering
   these per-user, solver-managed, without admin rights.

Everything outside the three pillars -- `av`, `ffmpeg`, scipy, pillow,
trimesh, and the rest of the ordinary scientific stack -- has official PyPI
wheels and should be declared as pip deps, not conda deps. (Earlier versions
of this ADR cited `av`/`ffmpeg` as the motivating examples; that was wrong
-- PyAV has shipped bundled-FFmpeg wheels since v9, and ComfyUI itself
pip-installs `av`. The conclusion stood; the evidence didn't.)

Alternatives:

- **venv + pip/uv only** -- cannot deliver any of the three pillars: no
  wheel form exists for pillar 1, licensing forbids the vendored-wheel
  model for pillar 2, and PyPI has no solver-managed native-toolchain
  story for pillar 3.
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
  [ADR-0003](0003-two-config-files-with-two-roles.md)), so pixi's full
  feature set stays reachable without comfy-env schema changes.
- uv is also used directly for main-env pip work (`install/helpers.py`
  `_find_uv()`), with plain pip as fallback.

## Consequences

- One manifest and one `pixi.lock` per env cover both conda and PyPI deps.
- Env materialization is fast (uv-backed) and reproducible.
- comfy-env depends on GitHub availability to bootstrap pixi on first run.
- Anything pixi cannot express is out of scope by construction; in practice
  the passthrough design has kept the config schema tiny. The one painful
  instance -- CUDA wheels needing no-deps installs, which pixi cannot
  express -- forces a post-pixi uv side-channel; the exit paths (pixi
  PR #5464, or conda-forge-native publishing once torch coverage allows)
  are tracked in [The two-system problem](../two-system-problem.md).
