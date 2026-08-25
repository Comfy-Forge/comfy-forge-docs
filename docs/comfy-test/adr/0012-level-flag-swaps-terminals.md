# ADR-0012: `--level` swaps terminal levels, it does not truncate

**Status:** **superseded (2026-08)** -- the `--level` flag was deleted. What a
run does is decided by `[test] levels` in `comfy-test.toml` and nothing else.
Kept because the reasoning explains why the flag existed and why its
replacement is not a rename.

## What was decided

> **`--level X` where X is a *terminal* level removes the other terminal
> levels from the run and appends X; where X is not terminal, it truncates
> the ladder at X.** The terminal set was `STATIC_CAPTURE`, `VALIDATION`,
> `EXECUTION_LIGHT`, `EXECUTION`.

So `--level execution_light` on a config that listed `execution` ran the
config's early levels plus `execution_light`, and **not** `execution` -- even
though `execution_light` sits earlier in the enum.

## Why it existed

The levels form one ladder, but its top is a *choice*, not a sequence: a run
ends in exactly one runtime level. `static_capture`, `validation`,
`execution_light` and `execution` are four answers to "how thoroughly do we
exercise this at the end", not four things you want in a row.

Per-lane CI needed to vary that ending without a `comfy-test.toml` per lane:
macOS was to pick `execution_light` ([ADR-0011](0011-execution-light-is-a-level.md)),
Linux and Windows `execution`. One config, one flag per lane.

## Why it was deleted

**The premise turned out to be false.** All four hosted lanes passed the same
`--level execution`; not one used the flag to vary its terminal. The per-lane
variation the flag was built for never shipped, and the lane that motivated it
ran `execution` like everyone else. What remained was a flag whose entire cost
was borne and whose benefit was zero.

Three things were wrong with it beyond that:

- **It made the lane, not the pack, the authority on what got tested.** The
  levels that actually ran were a property of a YAML file in this repository,
  so a pack author reading their own `comfy-test.toml` could not tell what CI
  would do.
- **The truncation silently dropped work.** `custom` is the last member of the
  enum, so `--level execution` -- passed by every lane -- cancelled a
  configured `custom` hook without a word. A flag that quietly removes a check
  the author asked for is worse than no flag.
- **The name lied.** "Level" reads like a ceiling; for terminal levels it
  behaved like a replacement. That surprise was the reason this record existed
  at all.

Nothing was lost by removing it. Standalone static analysis -- the one use that
did not want a full run -- is `comfy-test lint` and `comfy-test coverage`,
which need neither an environment nor a server. Varying the terminal level per
lane, if it is ever actually wanted, is a per-lane config key
(`[test.<lane>]`), where it is visible to the person whose pack it is.

## Alternatives considered at the time

- **Plain truncation ("run up to N").** Runs two runtime levels when the
  config's terminal sits below the flag; doubles the slowest stage.
- **A separate `comfy-test.toml` per lane.** Multiplies the file that
  [ADR-0006](0006-config-is-a-hard-fail-allowlist.md) already treats as
  safety-critical, and guarantees drift between copies.
- **A dedicated `--runtime-level` flag.** Honest, and rejected as an extra
  concept for a one-line behaviour. Superseded along with the rest.

## Consequences of the removal

- `[test] levels` is the single source of truth for what a run does. A lane
  cannot add or remove a level.
- `provenance.levels` in `results.json` still records what actually ran, and
  now always agrees with the config.
- A configured `custom` hook runs, because nothing truncates the list any more.
- Lanes that genuinely need a cheaper ending must say so in the pack's config;
  there is no command-line override to reach for.
