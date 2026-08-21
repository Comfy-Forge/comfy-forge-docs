# CW-ADR-0006: Fitting CUDA compiles into hosted CI

**Status:** accepted

## Decision

> **GitHub-hosted runners by default, with escape hatches for the 6-hour
> cap.** Not self-hosted-only (couples the farm to personal hardware and
> locks out forks); disk freeing, compile sharding, and checkpoint
> chains keep hosted builds converging across re-runs.

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

## Context

CUDA compiles of packages like pytorch3d or natten run for many hours;
GitHub-hosted runners cap a job at 6 hours and offer limited disk. The
alternative -- self-hosted only -- couples the farm's availability to
personal hardware and blocks outside contributors from running builds.

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

## Amendments (post-implementation)

- **Sharding is now zero-shim on Linux** — see
  [CW-ADR-0014](0014-zero-shim-sharding.md). The per-package shard shims
  this ADR's mechanism required are obsolete except natten's (cmake,
  Windows).
- **The 6-hour cap has a second, sneakier form**: GitHub applies a *default*
  `timeout-minutes: 360` to every job, **including on self-hosted runners**
  where no platform cap exists. The build jobs now set
  `timeout-minutes: 2880` — a no-op on hosted runners (hard-capped at 6h
  regardless) that removes the phantom cap from the homelab.
- **The queue has its own cap**: a job that waits more than 24 hours is
  discarded. Dispatching more work than the pool clears in a day starves
  the tail — observed live when ~2,100 queued jobs killed 13 whole runs.
  Batch dispatches to what ~40 runners clear well inside 24h.
- **ToS note**: chaining jobs past the 6-hour cap was reviewed against
  GitHub's Actions policy and is squarely within intended use (building
  and publishing this repository's software); the cap's documented
  consequence is cancellation, not sanction.

---

**Amended (Comfy-Forge line):** the `runner=self-hosted` opt-in described
above was removed — the farm is hosted-runners-only. Sharding and the
sequential-checkpoint chain are the only long-build strategies; the
`timeout-minutes` phantom-cap workaround went with the homelab support.
