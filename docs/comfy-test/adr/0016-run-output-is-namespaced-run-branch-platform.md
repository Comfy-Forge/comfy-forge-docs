# ADR-0016: Run output is `run / branch / platform`, always

**Status:** accepted (2026-08-16); `run.py` migration pending.

## Decision

> **Every run writes to
> `{logs}/{node}-{YYYYMMDD-HHMMSS}/{branch}/{platform}-{backend}/`, and all
> three namespacing levels are always present.** The branch level is never
> dropped: when `--branch` is not given it defaults to the node repo's
> checked-out git branch (detached HEAD -> short SHA; no git -> `local`). The
> run id carries a full, sortable date-time stamp, not `HH:MM`. Every consumer
> -- `publish`, the dashboard generator, external `show`/`cds` tooling -- may
> assume the three-level shape unconditionally.

## Context

The output tree is namespaced this way because a run is a **matrix**: one pack,
tested across `{branch} x {platform}-{backend}` cells that the publish step
aggregates into a single gh-pages dashboard
([ADR-0015](0015-publish-is-a-separate-job.md)). `publish.py` walks
`(branch, platform, results_dir)` tuples; the dashboard's per-branch index
([ADR-0015](0015-publish-is-a-separate-job.md) "per-branch dashboards exist")
assumes a branch level. The shape *is* the contract.

Two failures prompted this record:

1. **The branch level was optional.** `run.py` wrote `run/branch/platform` only
   when `--branch` was passed, and `run/platform` otherwise -- and git branch
   was never auto-detected. A local `comfy-test run --video` (no `--branch`)
   therefore produced `CADabra-1424/windows-cpu/`, which `publish`, the
   dashboard, and the external `cds show` tool all reject with "No branch
   folders found -- expected `{run}/{branch}/{platform}/results.json`". A shape
   with a level that can silently vanish is not a shape.

2. **The run id was `HH:MM` only** (`strftime("%H%M")`). Two runs in the same
   minute collided on both the log dir and the workspace dir -- forcing
   `--force`, which on Windows then failed to delete the read-only `.git` pack
   files of the previous run. Two runs a *day* apart also collide
   (`CADabra-1424` is not dated), silently merging or overwriting. And the id
   does not sort chronologically.

## Alternatives rejected

- **Keep the branch level optional (status quo).** Every downstream consumer
  already assumes it is present; making it conditional means each must handle a
  2-level *and* a 3-level tree, and the common local invocation produces output
  no tool can read. The bug, not a design.
- **Require `--branch` on every run.** Pushes a mandatory flag onto every local
  invocation for a value that is already knowable -- the checked-out branch is
  one `git rev-parse --abbrev-ref HEAD` away, and comfy-test already shells git
  for the commit hash. A default the machine can compute should not be a
  required human input.
- **Keep `HH:MM`, lean on `--force`.** `--force` is a destructive overwrite that
  hides the collision instead of preventing it, and it is exactly the operation
  that fails on Windows `.git` objects. Collision-avoidance belongs in the name,
  not in a delete.
- **A per-run UUID.** Collision-free but unreadable and unsortable; it discards
  the human-scannable `node + when` that makes a logs directory browsable. The
  date-time stamp is collision-free *and* legible *and* sortable.
- **Nest as `{node}/{timestamp}/...`.** Marginally tidier grouping per node, but
  it breaks the existing flat `{node}-{stamp}` run id that CI artifact names and
  `cds` already key on. Not worth the churn; the flat id with a full stamp
  solves the actual problem.

## Consequences

- `run.py` gains git-branch detection and always emits the branch level;
  `--branch` becomes an *override* of the detected default, not the thing that
  toggles the level's existence. Detached HEAD resolves to the short SHA; a
  non-git node dir resolves to `local`.
- The run id becomes `{node}-{YYYYMMDD-HHMMSS}`; the **workspace** dir
  (`get_workspace_dir()/{run_id}`) uses the same id, so back-to-back runs no
  longer collide and `--force` is no longer needed to run twice in a minute.
- Any tooling that parsed the old `Name-HHMM` id must be updated. Externally
  this is only the maintainer's `cds`/comfy-ci helpers; note it there.
- `publish`, the dashboard, and `cds show` can assume `{run}/{branch}/{platform}`
  unconditionally and drop their "no branch folder" fallbacks.
- Runs sort chronologically by name within a node, and a day's runs stop
  overwriting each other.
