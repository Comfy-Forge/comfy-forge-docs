# ADR-0008: Platforms are an opt-in allowlist

**Status:** accepted (2026-08)

## Decision

> **Only the platforms listed in `[test.platforms] platforms = [...]` run,
> and an unrecognised token aborts the run** (`common/config_file.py`).
> There are no per-platform boolean toggles.

## Context

The alternative shapes were tried and both misbehave.

**Default-all** (every known platform runs unless disabled) sounds
generous, but the platform list grows over time: adding `windows-portable`
to the registry would silently enlist every existing consumer into a lane
they never asked for, on runners they may not have, and their dashboards
would sprout red cells for a platform they do not ship on. Growth in the
registry should not rewrite everyone's CI.

**Booleans** (`linux = true`, `macos = false`) put the taxonomy in the
config file. Every new platform is then a new key, old configs cannot
express platforms that did not exist when they were written, and the
"unknown key" rule ([ADR-0006](0006-config-is-a-hard-fail-allowlist.md))
cannot distinguish a typo from a platform this version does not know.

A list of tokens validated against the registry
([ADR-0007](0007-platform-registry-is-the-source-of-truth.md)) is the shape
that makes both problems go away: adding a platform changes nobody's
behaviour, and a typo is a named error at startup.

## Alternatives rejected

- **Default-all with opt-out** -- silently enlists consumers on registry
  growth.
- **Per-platform booleans** -- taxonomy leaks into config; typos become
  unknown keys indistinguishable from version skew.
- **Warn on unknown tokens and run the rest** -- rejected for the same
  reason as [ADR-0006](0006-config-is-a-hard-fail-allowlist.md): a
  misspelled platform then produces a green run that tested less than the
  author believes.

## Consequences

- Every consumer must state its platforms explicitly. Slightly more
  boilerplate, in exchange for a config that means exactly what it says.
- Aliases exist so historical spellings keep working; they resolve in the
  registry, not in the config parser.
- A platform in the registry with no runner wired (`rocm`) is selectable in
  principle and fails loudly in practice, which is the correct order: the
  vocabulary can lead the infrastructure without silently pretending.
- Because the list is a set of tokens rather than flags, the same file can
  be consumed by the CI matrix and by a local `--platform` run without
  translation.
