# ADR-0012: `--level` swaps terminal levels, it does not truncate

**Status:** accepted (2026-08); flagged in the 2026-08 adversarial review as
the most surprising behaviour in the CLI.

## Decision

> **`--level X` where X is a *terminal* level removes the other terminal
> levels from the run and appends X; where X is not terminal, it truncates
> the ladder at X.** The terminal set is `STATIC_CAPTURE`, `VALIDATION`,
> `EXECUTION_LIGHT`, `EXECUTION` (`orchestration/manager.py`).

So `--level execution_light` on a config that lists `execution` runs the
config's early levels plus `execution_light`, and **does not** run
`execution` -- even though `execution_light` sits earlier in the enum.

## Context

The levels form one ladder, but its top is a *choice*, not a sequence: a run
ends in exactly one runtime level. `static_capture`, `validation`,
`execution_light` and `execution` are four answers to "how thoroughly do we
exercise this at the end", not four things you want in a row.

Per-platform CI needs to vary that ending without maintaining a separate
`comfy-test.toml` per platform: macOS must pick `execution_light`
([ADR-0011](0011-execution-light-is-a-level.md)), Linux and Windows want
`execution`. One config, one flag per lane.

A plain "run up to N" ladder cannot express that. Because `execution_light`
precedes `execution` in the enum, truncation at `execution_light` would be
correct -- but truncation at `execution` on a config listing
`execution_light` would run *both*, executing every workflow twice.

## Alternatives rejected

- **Plain truncation ("run up to N").** Runs two runtime levels when the
  config's terminal sits below the flag; doubles the slowest stage.
- **A separate `comfy-test.toml` per platform.** Multiplies the file that
  [ADR-0006](0006-config-is-a-hard-fail-allowlist.md) already treats as
  safety-critical, and guarantees drift between copies.
- **A dedicated `--runtime-level` flag** distinct from `--level`. Honest,
  and rejected only as an extra concept for a one-line behaviour; revisit if
  the surprise keeps costing people time.

## Consequences

- **The name lies a little.** "Level" reads like a ceiling; for terminal
  levels it behaves like a replacement. This is the documented gotcha, and
  the reason it earns a record rather than a footnote.
- The config's non-terminal levels (`syntax`, `install`, `registration`,
  `instantiation`, ...) are preserved untouched, so the flag never silently
  drops a check the author asked for.
- `provenance.levels` in `results.json` records what actually ran, which is
  the authoritative answer when the flag's semantics surprise someone.
- Passing a non-terminal level (`--level registration`) behaves exactly like
  truncation, which is what most people expect and why the surprise is rare
  but sharp.
