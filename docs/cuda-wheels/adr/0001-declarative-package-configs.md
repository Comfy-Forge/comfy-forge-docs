# CW-ADR-0001: Declarative package configs + patch scripts

**Status:** accepted

## Context

Every CUDA package (flash-attn, nvdiffrast, pytorch3d, ...) has its own
build quirks: source layout, extra deps, nvcc flags, MSVC bugs, submodules,
arch constraints. Encoding each as a bespoke shell script produces N
unmaintainable scripts; encoding nothing produces a build engine full of
special cases.

## Decision

A package is **one YAML file** (`packages/<name>.yml`: source repo + tag,
version, extra deps, nvcc flags, arch overrides, sharding/checkpoint knobs)
plus an optional **Python patch script** (`patches/<name>.py`) run against
the cloned source before building. The build engine
(`.github/actions/build-wheel`) is generic; all package specifics live in
config and patches.

Packages **inherit the shared build grid** (CW-ADR-0005) unless they
declare their own `build_matrix` -- so one edit to `_defaults.yml`
propagates a new torch/cuda to ~38 packages at once.

## Consequences

- Adding a package is a YAML file + maybe a patch script, reviewable in one
  screen; `packages/README.md` documents the fields.
- Per-package gates exist where reality demands them: `min_pytorch`
  (natten's hard assert), `arch_list_by_cuda` (Blackwell-only sageattn3,
  gsplat variants).
- The inheritance shortcut has one hand-maintained exception:
  `sageattn3.yml` declares its own combinations list and must be edited for
  every new torch minor, or it silently stops tracking the grid. (The
  watcher, CW-ADR-0008, takes this over.)
- There is no `max_pytorch`/exclude mechanism; a package that breaks on a
  bleeding-edge torch (mmcv is the documented candidate) simply fails its
  cells -- accepted as "survey" behavior.
