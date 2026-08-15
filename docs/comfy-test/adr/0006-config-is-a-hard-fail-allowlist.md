# ADR-0006: Config is a hard-fail allowlist

**Status:** accepted (2026-08)

## Decision

> **An unrecognised key or platform token in `comfy-test.toml` aborts the
> run with a named error.** Not a warning, not a silent default -- the run
> does not start.

Enforced in `common/config_file.py`: unknown platform tokens
(`load_config`), unknown keys under `[test.workflows]`, and unknown keys
under the sub-tables, which are dataclasses that reject surplus fields.

## Context

The config file decides *what gets tested*. A key nobody reads is not a
harmless no-op there -- it is a silent reduction in coverage that still
produces a green badge.

The scar is recorded in the source: a pack wrote `gpu = [...]` under
`[test.workflows]`, expecting it to select the GPU workflow set. No such
key existed. It was silently ignored, the runner fell through to its
default set, and **59 workflows ran on a lane configured for 3** -- with a
plausible-looking green result. Two hours of work went into interpreting
output that answered a question nobody had asked
(`config_file.py`, GeometryPack-2329).

The generalisation: a testing tool's config errors are not user errors, they
are *result-validity* errors. Permissiveness in a linter is kindness; in a
test harness it manufactures false confidence.

## Alternatives rejected

- **Warn and continue.** The industry default. Rejected because warnings
  scroll past in CI logs and the run still ends green -- exactly the failure
  mode that produced the incident.
- **Ignore unknown keys for forward compatibility** (so old comfy-test
  versions tolerate new config). Rejected under
  [comfy-env ADR-0017's](../../comfy-env/adr/0017-pre-1-0-no-backward-compatibility.md)
  pre-1.0 stance: a version mismatch should be loud, and pinning the tool is
  the correct fix.
- **Schema-validating with a permissive `extra` bucket.** Same failure with
  extra ceremony.

## Consequences

- Typos surface at startup, before any environment is built.
- The `gpu` key specifically gets a targeted hint in the error message,
  because it is the mistake that already happened once.
- **Asymmetry, deliberate:** a handful of *deprecated* workflow keys
  (`run`, `files`, `file`, `screenshot`) are still migrated silently by
  `WorkflowConfig.__post_init__` rather than rejected. Those were once
  valid, so failing them punishes users for our rename; unknown keys were
  never valid, so failing them is information. When the deprecation window
  closes they become unknown keys and inherit the hard failure.
- Config that parses is not config that is *correct* -- an allowlist cannot
  catch a valid key with a wrong value. See
  [ADR-0008](0008-platforms-are-opt-in.md) for the platform half.
