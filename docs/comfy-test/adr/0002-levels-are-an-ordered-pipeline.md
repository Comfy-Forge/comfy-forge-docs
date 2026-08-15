# ADR-0002: Levels are an ordered pipeline, not a test suite

**Status:** accepted (2026-08)

## Decision

> **Execution order *is* the declaration order of the `TestLevel` enum, and
> prerequisites are a static dict.** A level is a stage that takes a
> `LevelContext` and returns an enriched one; `resolve_dependencies()` pulls
> in whatever a requested level needs.

The list in `comfy-test.toml` is a *set*, not a sequence: reordering it
changes nothing, because the manager iterates the enum
(`orchestration/manager.py`), not the config.

## Context

The stages are not independent. INSTALL produces the paths REGISTRATION
needs; REGISTRATION boots the server INSTANTIATION talks to; the JAVASCRIPT
level scans a `web/` directory that some packs only materialise once the
server has run prestartup. Any "suite" abstraction would have to re-derive
that ordering at runtime anyway.

Making the order intrinsic buys three things: `--level X` means "everything
up to X" without a scheduler; a failure attributes to a named stage rather
than to whichever test happened to run first; and adding a check forces the
question *where in the pipeline does this belong?* -- which is how the
JAVASCRIPT level ended up after REGISTRATION rather than beside SYNTAX.

## Alternatives rejected

- **pytest-style independent tests with fixtures.** The fixtures would be
  "a built environment" and "a running server", i.e. the pipeline, wearing a
  costume -- with the ordering guarantees now implicit in fixture scope.
- **A DAG / plugin scheduler.** More expressive than the problem: the
  dependency graph is a straight line with two optional side-branches. The
  real risk being defended against is not insufficient expressiveness but
  hand-copied level lists drifting out of sync, which is what
  `tests/test_levels.py` guards.
- **Config order is execution order.** Tempting and wrong: it invites a
  config that runs EXECUTION before INSTALL and fails obscurely.

## Consequences

- Adding a level means editing the enum and the dependency dict; there is no
  registration hook. Deliberate -- the ordering is the design, so it should
  be edited in one visible place.
- Enum order is a compatibility surface: inserting a level between two
  others changes what `--level X` runs for everyone.
- `DEFAULT_LEVELS` is a *subset* of the enum, so opt-in levels (COVERAGE,
  JAVASCRIPT, CUSTOM) exist in the ladder without running by default.
- The ordering also determines what a partial run proves: a green
  INSTANTIATION says nothing about EXECUTION, and the dashboard shows only
  the levels that ran (`provenance.levels`).
