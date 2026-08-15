# ADR-0004: Mocking is earned by probing, never declared

**Status:** accepted (2026-08)

## Decision

> **Whether a CUDA package is mocked is decided by looking for it on disk in
> the materialized comfy-env environment -- not by the `--cuda` flag, not by
> the lane name, not by the declaration in `comfy-env.toml`.** A package is
> mocked if and only if the probe fails to find it
> (`levels/install.py`, `_cuda_wheel_present`).

## Context

Node packs declare CUDA-only dependencies (`flash_attn`, `cumesh`, ...)
that node code imports at module scope. On a CPU lane those imports would
crash registration, so comfy-test installs an empty stub module in their
place.

The question is *when*. The obvious answer -- "mock them on CPU lanes" --
is wrong in both directions:

- comfy-env inlines cuda-wheel URLs into the generated `pixi.toml` when a
  GPU is present, so on some GPU runs the wheels genuinely are installed and
  mocking them would replace working code with a stub;
- on a GPU host where wheel resolution failed, the packages are absent
  despite `--cuda`, and *not* mocking them turns registration into an import
  crash.

The flag describes intent. The filesystem describes reality. Only one of
them can be trusted to decide whether `import flash_attn` will work.

The scar is recorded in the source: comfy-env's environment layout changed,
the probe kept looking at a stale `.ce` path, found nothing, and silently
concluded that *every* declared CUDA package was absent -- mocking all of
them **for weeks** on runs that had the real wheels installed. Green
results, wrong verdicts.

## Alternatives rejected

- **Trust `--cuda` / the lane name.** Simple, and produces exactly the two
  failure modes above.
- **Trust the declaration in `comfy-env.toml`.** Declaring a dependency is
  not evidence it materialised.
- **Import-and-catch (try the real import, mock on ImportError).** Loses the
  distinction between "absent" and "present but broken" -- and a
  half-initialised CUDA module can crash the interpreter rather than raise.

## Consequences

- comfy-test is **coupled to comfy-env's on-disk layout** (ABI-tagged env
  directories under `<comfyui>/.ce/`). A layout change breaks the probe.
  That coupling is the price of a truthful answer.
- Because a failed probe is indistinguishable from a genuinely empty
  environment, "no environment found at all" is logged as a **resolution
  failure**, loudly, naming the paths it searched -- so the next layout
  change is a visible warning rather than a silent all-mock.
- Environments matched in a directory with no ABI tag are reported as
  unverified, since the build stack could not be confirmed.
- The mock list is printed on every run (`installed (no mock)` /
  `absent (will mock)`), so a wrong verdict is auditable from the log
  instead of requiring a debugger.
