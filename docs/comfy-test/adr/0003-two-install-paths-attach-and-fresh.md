# ADR-0003: Two install paths -- attach and fresh

**Status:** accepted (2026-08); surfaced in `results.json` as
`provenance.install_mode` after the 2026-08 adversarial review found the
distinction was invisible to consumers.

## Decision

> **comfy-test runs in two modes, and a green result means different things
> in each.** In **fresh** mode it builds the venv, clones ComfyUI, installs
> the pack and boots the server itself. In **attach** mode the *lane* has
> already done all of that and hands comfy-test a live `--server-url`; the
> INSTALL level then does nothing but discover paths.

Hosted CPU lanes use attach. CUDA lanes, local runs, and the Desktop lanes
use fresh.

## Context

Hosted GitHub runners are slow and uncached. Building a venv, resolving the
torch family, cloning ComfyUI and installing a pack on every job put the
CPU matrix over the time budget. The lanes therefore prebuild that
environment in YAML behind an `actions/cache` key and reuse it across runs;
comfy-test is invoked afterwards with `--server-url`, and `levels/install.py`
takes its attach branch.

Self-hosted CUDA lanes have no such pressure (the box is warm, the GPU is
the scarce resource, and the whole point is to exercise real wheel
installation), so they let comfy-test do the work.

The consequence nobody had written down: **on an attach lane, INSTALL is a
no-op.** A green `linux-cpu` cell says "your pack works in a prebuilt
environment", not "your pack installs cleanly from scratch". Those are
different claims, and the second one is the one most authors think a CI
badge is making.

## Alternatives rejected

- **One path everywhere (always fresh).** Correct in principle and what the
  README implied for a year. Rejected on wall-clock: the hosted matrix
  became the slowest thing in the repo, and cache-less installs of the torch
  family dominated every job.
- **One path everywhere (always attach).** Would make the fresh-install
  failure mode -- the single most common real-world bug class for node packs
  -- permanently untestable.
- **Auto-detecting the mode and reporting "install: skipped".** Considered;
  the level list already shows INSTALL as run. The honest fix was to record
  the mode as provenance rather than to fake a skip.

## Consequences

- `results.json` carries `provenance.install_mode` (`attach` | `fresh` |
  `desktop`). Any dashboard, badge or rollup that treats all green cells as
  equivalent is overstating attach lanes.
- The hosted cache key includes only platform and Python version, so ComfyUI
  and the torch family stay frozen at whatever HEAD populated the key until
  GitHub evicts it. An attach lane therefore does **not** exercise the
  pinning described in [ADR-0005](0005-pinned-torch-random-python.md).
- Reproducing a red attach-lane result locally requires building the same
  env by hand; the run URL and provenance block are the only record of what
  it contained.
- If you want a lane that genuinely proves installability, run a dispatch
  (fresh) lane. Documented in
  [Platforms and lanes](../lanes.md).
