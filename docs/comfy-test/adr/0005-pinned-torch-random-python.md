# ADR-0005: Pinned torch family, randomly sampled Python

**Status:** accepted (2026-08)

## Decision

> **The torch family is pinned to a hand-maintained, known-aligned triple
> (`TORCH_TRIPLES` in `common/config.py`), installed before the pack's
> requirements so nothing upgrades it. The Python version is drawn at
> random per run from 3.10-3.13.**

Two opposite treatments of the same problem -- combinatorial explosion --
chosen because the two axes fail differently.

## Context

**torch.** `torch`, `torchvision` and `torchaudio` must be version-aligned;
they are released in lockstep but not always published together. Letting a
resolver pick produced venvs with `torch 2.12` next to a `torchaudio` built
against 2.11 -- an environment no user has, failing in ways no user will
hit. Wrong-environment failures are worse than no coverage: they burn
maintainer time on phantom bugs.

**Python.** A real 4-way matrix quadruples every lane. But interpreter
breakage is real and cheap to hit (3.13 removals, 3.10 syntax), and it is
*uniformly distributed across runs* -- if a pack is broken on 3.12, a
random sample finds it within a few pushes. Coverage is probabilistic but
the expected time-to-detection is short and the cost is 1x, not 4x.

## Alternatives rejected

- **Free resolution for torch** (`pip install torch`): version skew, above.
- **A full Python matrix:** 4x runner cost for an axis where sampling
  converges quickly. Rejected on budget, revisitable if failures cluster.
- **A single pinned Python:** cheapest, and blind to the entire interpreter
  axis -- which is the one that breaks packs on ComfyUI upgrades.
- **Pinning torch to `latest`:** available as an opt-out (`torch_version`),
  not the default, because "latest" is a moving target that makes a red run
  un-reproducible.

## Consequences

- **A re-run may not reproduce a failure.** Different draw, different
  interpreter, possibly green. This is the single most confusing behaviour
  in the tool, which is why `provenance.python_version` is recorded in
  `results.json` -- read it before concluding a fix worked.
- `TORCH_TRIPLES` is a human maintenance burden: torch ships, the table
  needs a row. `torch_version = "latest"` is the escape hatch when the table
  lags.
- The pin is applied by the **fresh** install path. Attach lanes
  ([ADR-0003](0003-two-install-paths-attach-and-fresh.md)) inherit whatever
  their cached environment was built with, so those lanes do not exercise
  this decision at all -- another reason `install_mode` is recorded.
- Python is sampled *per run*, not per lane, so different lanes in one
  matrix may test different interpreters. The dashboard cell tells you
  which.
