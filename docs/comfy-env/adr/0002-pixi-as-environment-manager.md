# ADR-0002: pixi as environment manager

**Status:** accepted

## Decision

Use [pixi](https://pixi.sh): a fast Rust-based manager that speaks
**conda-forge and PyPI in the same `pixi.toml`**, ships a per-env lockfile,
installs entirely per-user with no admin rights or system-Python pollution,
and uses [uv](https://github.com/astral-sh/uv) underneath for the PyPI side.

An honesty note on what the lockfile buys (2026-08 review): **per-machine
solve determinism and local idempotence** -- skip-if-unchanged installs,
stamped and hash-checked. NOT cross-machine reproducibility: packs ship
`comfy-env.toml`, each machine generates its manifest from host detection
and solves fresh against the rolling channel, and the CUDA wheels currently
install outside the lock entirely (the
[two-system problem](../two-system-problem.md)). Cross-machine
reproducibility would arrive via CI-pre-solved lockfiles per env x ABI tag
(deferred; most valuable for the ComfyUI Desktop population).

Supporting choices:

- The pixi binary is **self-bootstrapped, pinned, and verified**
  (`packages/pixi.py`): a pinned `PIXI_VERSION` is downloaded as the
  official release archive, checked against sha256 hashes vendored from the
  release's `sha256.sum`, and installed to a comfy-env-owned, version-keyed
  path (`~/.comfy-env/pixi/<version>/`) -- never touching a user's own
  `~/.pixi` install. `pip install comfy-env` remains the only prerequisite;
  upgrading pixi is a one-constant, CI-tested change. (Originally this
  downloaded whatever `releases/latest` served, unpinned and unverified --
  identified as the system's worst tail risk in the 2026-08 review and
  fixed.)
- comfy-env acts as a **manifest compiler** (`packages/toml_generator.py`):
  it generates `pixi.toml` files rather than driving a package API. Unknown
  keys in `comfy-env.toml` are intended to pass through to the generated
  manifest untouched (in v0.4 only an allowlist actually does -- see
  [ADR-0003](0003-two-config-files-with-two-roles.md)), so pixi's full
  feature set stays reachable without comfy-env schema changes.
- uv is also used directly for main-env pip work (`install/helpers.py`
  `_find_uv()`), with plain pip as fallback.

## Context

Conda cannot be avoided -- but for precise, enumerable reasons, not "some
packages aren't on PyPI." The genuinely conda-only packages all belong to
one of **three pillars**:

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

The three pillars justify conda's *existence* in an env. Membership of each
package is then governed by a fourth principle: **native-lineage coherence**.
An env whose native closure comes from conda-forge (bpy linking conda's
ffmpeg, CGAL/VTK on conda's C++ runtime, conda's libomp) must take its OTHER
native-linked Python packages (`av`, opencv, pymeshlab) from conda-forge
too -- mixing a pip wheel's bundled dylibs with conda's builds inside one
process is the duplicate-native-library disease (dyld/symbol collisions,
OMP state corruption; worst on macOS, where a pip `av` in a conda env fails
unless host libraries leak in, defeating isolation). Conversely, ComfyUI's
host env takes `av` from pip correctly -- it is pip-lineage end to end. One
lineage per process; conda envs are conda-lineage by construction.

(Earlier versions of this ADR cited `av`/`ffmpeg` as packages that "cannot
be installed from PyPI" -- imprecise: PyAV ships bundled-FFmpeg wheels. The
real reason they come from conda here is lineage coherence, not
availability. Only pure-Python, zero-native-linkage deps are
lineage-neutral.)

Alternatives:

- **venv + pip/uv only** -- cannot deliver any of the three pillars: no
  wheel form exists for pillar 1, licensing forbids the vendored-wheel
  model for pillar 2, and PyPI has no solver-managed native-toolchain
  story for pillar 3.
- **conda/mamba directly** -- solves the native problem but PyPI interop is
  bolted on, solves are slower, and there is no single-manifest,
  single-lockfile story across both ecosystems.

## Consequences

- One manifest and one `pixi.lock` per env cover the conda and ordinary
  PyPI deps; the CUDA wheels remain outside the lock until Requires-Dist
  curation lands and the inlining path revives
  ([two-system problem](../two-system-problem.md)).
- Env materialization is fast (uv-backed) and deterministic per machine;
  unchanged envs are skipped via install hashes and validated stamps.
- comfy-env depends on GitHub availability to bootstrap pixi on first run.
- Anything pixi cannot express is out of scope by construction; in practice
  the passthrough design has kept the config schema tiny. The one painful
  instance -- CUDA wheels needing no-deps installs, which pixi cannot
  express -- forces a post-pixi uv side-channel; the exit paths (pixi
  PR #5464, or conda-forge-native publishing once torch coverage allows)
  are tracked in [The two-system problem](../two-system-problem.md).
