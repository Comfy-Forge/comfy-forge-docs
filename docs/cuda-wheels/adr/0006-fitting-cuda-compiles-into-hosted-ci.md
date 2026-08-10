# CW-ADR-0006: Fitting CUDA compiles into hosted CI

**Status:** accepted

## Context

CUDA compiles of packages like pytorch3d or natten run for many hours;
GitHub-hosted runners cap a job at 6 hours and offer limited disk. The
alternative -- self-hosted only -- couples the farm's availability to
personal hardware and blocks outside contributors from running builds.

## Decision

**GitHub-hosted runners are the default** (`ubuntu-22.04` /
`windows-2022`), with an opt-in `runner=self-hosted` input for homelab
machines. Three escape hatches keep hosted builds inside the caps:

1. **Disk freeing** -- delete the runner's dotnet/android/ghc/swift images
   up front (default on).
2. **Compile sharding** (`sharding: N`) -- N jobs each compile a subset of
   `.cu` files and upload object tarballs; a link-only job assembles the
   wheel. Implemented by injecting a source-filter/no-op-compile prelude
   into the build.
3. **Sequential checkpointing** (`sequential_checkpoint: <seconds>`) -- the
   compile runs under `timeout`; on expiry the ENTIRE `source/` tree
   (sources + build state) is tarred as an artifact -- PAX format to
   preserve nanosecond mtimes, because ninja's restat check breaks on
   re-cloned sources (build/-only checkpoints were tried and abandoned) --
   and a chained follow-up job resumes.

## Consequences

- Anyone can run the farm from a fork; no hardware coupling.
- Idempotent skip-existing (CW-ADR-0002) plus these hatches make even
  multi-day package builds converge across re-runs.
- **Verified defects (2026-08 audit):** the chain ladder is wired 2 links
  deep while comments claim 10 (a chain that runs out loses its compute --
  checkpoints are same-run artifacts); the chain `done` flag is a matrix
  job *output*, which is last-writer-wins across cells and can starve an
  unfinished cell. Direction: fix per-cell gating; move the known
  multi-hour packages to self-hosted runners (no 6h cap) and keep the
  chain machinery as the public-fork fallback.
- The injected-prelude sharding is coupled to torch/setuptools/ninja
  internals that shift between releases; nothing asserts the link job
  actually reused the shards (a silent full recompile just runs slow).
  Direction: assert object reuse loudly.
