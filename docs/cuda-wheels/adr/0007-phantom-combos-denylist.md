# CW-ADR-0007: Phantom combos -- a curated denylist of upstream gaps

**Status:** accepted (direction: make it data, then self-maintaining)

## Decision

`PHANTOM_COMBOS`: an explicit denylist of `(cuda, torch, python, platform)`
tuples known to be unpublished upstream. Matrix generation silently drops
matching cells. Curation is manual: when a new combo lands, missing upstream
cells are discovered by build failure and added.

## Context

Upstream PyTorch does not publish every (cuda, torch, python, platform)
cell -- e.g. several cu129 Windows cells for torch 2.10/2.11 simply never
shipped. A grid cell whose torch wheel does not exist fails late and
confusingly: the matrix generates, the job spins up, and dies at
`uv pip install torch==...` inside the build-env setup. Nothing at
matrix-generation time validates a cell against upstream reality.

## Consequences

- Known-impossible cells cost zero CI and produce zero red noise.
- **Verified defect (2026-08 audit):** the list is hardcoded in TWO scripts
  (`generate_matrix.py` and `gap_analysis.py`) and the copies have already
  drifted -- the gap-analysis copy is missing entries, so its reports
  overcount gaps.
- **Direction (agreed):** move the list to one data file
  (`scripts/phantom_combos.json`) read by both scripts; then let the
  upstream watcher (CW-ADR-0008) maintain it automatically -- the same
  fetch that detects new combos also knows exactly which (python, platform)
  cells upstream did not publish, turning phantom curation from
  fail-then-edit into data derived at detection time.
