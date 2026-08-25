# ADR-0016: The branch level is never dropped

**Status:** accepted (2026-08-16)

## Decision

> **Run output is always `{logs}/{node}-{HHMM}/{branch}/{platform}-{backend}/`
> -- the branch level is never omitted.** When `--branch` is not passed it
> defaults to the node repo's checked-out git branch (detached HEAD -> short
> SHA; not a git repo -> `local`), flattened to one path segment. Consumers
> (`publish`, the dashboard, external `show`/`cds` tooling) may assume the
> three-level shape unconditionally.

## Context

The output tree is namespaced this way because a run is a **matrix**: one pack,
tested across `{branch} x {platform}-{backend}` cells that the publish step
aggregates into one gh-pages dashboard
([ADR-0015](0015-publish-is-a-separate-job.md)). `publish.py` walks
`(branch, platform, results_dir)` tuples and the dashboard has a per-branch
index -- the branch level is part of the contract.

The defect this fixes: `run.py` wrote `run/branch/platform` only when
`--branch` was passed, and `run/platform` otherwise -- and git branch was never
auto-detected. So a run without `--branch` produced `CADabra-1424/windows-cpu/`,
which `publish`, the dashboard, and the external `cds show` tool all reject
with "No branch folders found -- expected
`{run}/{branch}/{platform}/results.json`". A shape with a level that can
silently vanish is not a shape. The branch the run tested is already
knowable -- one `git rev-parse --abbrev-ref HEAD` on the node dir -- so the
level can always be filled without a required flag.

## Alternatives rejected

- **Keep the branch level optional (status quo).** Every downstream consumer
  already assumes it is present; the common local invocation
  (`comfy-test run` with no `--branch`) then produces output no tool can read.
  A bug, not a design.
- **Require `--branch` on every run.** Pushes a mandatory flag onto every local
  invocation for a value the machine can compute from the checkout. A default
  that is knowable should not be required human input.

## Consequences

- `run.py` detects the branch and always emits the level; `--branch` becomes an
  *override* of the detected default, not the toggle for the level's existence.
  For a local-dir run `--branch` is only a label -- it does not check out or
  fetch that branch, so the honest default is the branch actually checked out.
- `publish`, the dashboard, and `cds show` can assume `{run}/{branch}/{platform}`
  unconditionally.
- The run-id stamp is `{node}-{YYYYMMDD-HHMM}` -- a date plus hour:minute, **no
  seconds** (by request: readable, not a wall of digits). The date kills the
  cross-day collision (a plain `HHMM` reused a previous day's folder, leaving a
  stale mtime that `cds`, sorting by recency, silently skipped). Same-*minute*
  collisions remain possible but are accepted as rare -- adding seconds was
  explicitly declined.
