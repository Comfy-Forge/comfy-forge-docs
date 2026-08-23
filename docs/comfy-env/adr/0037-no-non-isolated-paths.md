# ADR-0037: No non-isolated paths

**Status:** accepted (2026-08-23). Amends
[ADR-0008](0008-graceful-degradation-everywhere.md) (whose "terminal
fallback" prose this makes precise); executed under
[ADR-0017](0017-pre-1-0-no-backward-compatibility.md).

## Context

comfy-env shipped two off-switches from its earliest versions:
`COMFY_ENV_ISOLATE` / `[settings] isolate` (off = import every pack's
nodes in-process, no workers) and `COMFY_ENV_INSTALL_ISOLATED` /
`[settings] install_isolated` (off = `install()` skips materializing
pixi envs). Together they implied a supported **non-isolated mode**:
run comfy-env packs like ordinary custom nodes, in the host
environment.

The question of whether to keep them was put through a four-reviewer
adversarial debate (two arguing keep, two delete), with an issue survey
and independent code investigation on each side. The facts that
decided it:

- **Nobody set either flag.** Not one of 99 pack TOMLs carries a
  `[settings]` section; no CI workflow, no deployment script, no tool
  in or out of tree exports either variable. The only setter in
  existence was a test-suite speed hack.
- **The off-states never composed into a working mode.** With
  `isolate=0`, nodes are imported into the host interpreter — which,
  by the host-environment principle
  ([ADR-0003](0003-two-config-files-with-two-roles.md) /
  [ADR-0022](0022-comfy-env-placement-in-host-env.md)), contains
  comfy-env and nothing else. The import raises, the exception was
  swallowed into a log line, and every node in the pack silently
  vanished. With `install_isolated=0`, `install()` printed
  "Installation complete!" after skipping the entire workspace half.
  Each flag's off-state was a silent no-op with data loss; combined,
  they produced a ComfyUI in which nothing could ever work.
- **The demand behind them is real but is not for them.** Docker/CI
  users (comfy-env #2, #9; UniRig #8) want *build-time installation
  and an inert runtime* — which the bake-at-build story plus the
  fast-key zero-network warm path already provides (now documented in
  [Containers, CI & air-gapped](../containers.md)). The conda/envless
  audience (Sharp #50, the ~15 frozen `ENVLESS*` forks) wants packs to
  run in an environment they manage — which the dependencies make
  structurally impossible for most packs: **16 of 21 shipped packs
  declare conda-only dependencies** (`mesalib`, `xorg-libsm`, CGAL,
  `bpy` from a custom channel, `pythonocc-core` with its own Python
  pin) that have **no pip spelling**, and 19 of 21 need CUDA wheels
  resolved against host state (`+cu{XXX}torch{X.Y}` × `cp{py}` ×
  platform) that a static requirements file cannot encode. The forks
  are the empirical trial: fifteen dedicated repositories attempting
  exactly this mode, all frozen within one month of creation.
- **The flags had undisclosed blast radius.** `isolate=0` also
  disabled the macOS libomp segfault workaround and the
  base-directory fill-in — a switch named "run nodes in subprocess
  workers" whose off state re-enabled crashes.
- **Zero test coverage.** No behavioural test ever ran either off
  state; dead code (`_is_enabled()`, an unreachable disabled-branch)
  had already rotted unnoticed inside the flag's own paths.

## Decision

**comfy-env supports exactly one execution model: isolated.** There is
no supported configuration in which a pack that declares an isolation
env runs its nodes in the host process, and no supported configuration
in which `install()` is told not to materialize environments. The two
flags are removed outright (0.4.25, pre-1.0 under ADR-0017), not
deprecated.

What this does **not** remove:

- **Per-env automatic degradation** (ADR-0008). A missing or
  ABI-mismatched env still falls back to an in-process import attempt
  rather than blocking ComfyUI's boot. The distinction this ADR draws:
  degradation is **evidence-triggered accident recovery** (the env
  demonstrably is not there), never a **flag-triggered mode** (the
  user asking for un-isolated execution of an isolatable pack). With
  this decision, a failed fallback import is also loud — full
  traceback, and an all-sources-failed pack raises so ComfyUI marks it
  IMPORT FAILED instead of loading green with zero nodes.
- **Packs without configs.** A directory with no `comfy-env.toml` was
  never isolated and imports normally; that is not a mode, it is the
  absence of a declaration.
- **`COMFY_ENV_AUTO_INSTALL`** as the recovery hatch, and
  **`USE_COMFY_ENV`** (install-time helper switch), which are separate
  mechanisms.

Tombstones are **value-sensitive**, because the two stale values mean
different things: a *falsy* leftover (`COMFY_ENV_ISOLATE=0`, or
`[settings] isolate = false` in a pack TOML) is written intent the
system can no longer honor — running isolated against it silently is
exactly the silent-flip failure mode — so it fails loudly (env var:
boot error with a self-locating message; TOML key: `ValueError`
propagating to a visible IMPORT FAILED). A *truthy* leftover matches
the only behavior that now exists and merely warns. Keys the settings
TUI wrote into `~/.comfy-env/settings.env` (it saved every key, for
every user who ever opened it) are residue, not intent: skipped before
they reach the environment, cleaned on the next save, never an error.

## Alternatives rejected

- **A supported envless mode** (generated host requirements +
  cuda-wheel index pins). Buildable for the pure-pip minority of
  packs, structurally impossible for the conda-dep majority — a mode
  that works for some packs and cannot for others is a support-ticket
  generator with a feature's name. Rejected also on the maintainer's
  own criterion: a mode not worth one CI lane is not worth shipping.
- **Config-shape gating** (skip materialization when a config has only
  `[pypi-dependencies]`). Incoherent: every env unconditionally
  carries `python + pip + setuptools` plus the replicated torch pin —
  the interpreter itself is the isolation payload, not the conda
  packages.
- **Keeping the flags as documented-but-unsupported escape hatches.**
  An advertised knob whose off-position lands in a broken state is a
  trap, not an option; it converts one legible failure into three
  illegible ones.

## Consequences

- The 1.0 stable surface does not carry an execution-mode axis; the
  test matrix has one diagonal.
- The honest answers to the demand live in docs, not flags:
  [Containers, CI & air-gapped](../containers.md) for the Docker/CI
  audience; "not supported, and here is why" for envless.
- A future *designed* host-install story (e.g. detecting an
  already-conda-managed host env) remains possible — recorded on the
  roadmap as an unscheduled thought — and would arrive as its own
  ADR with its own dependency-delivery design, not as a revival of
  these flags.

## Revisit trigger

If the ComfyUI Registry's delisting of comfy-env-dependent packs (the
distribution-channel problem, current as of 2026-08) turns out to
hinge on the *existence* of a non-isolated execution mode rather than
on install-time behavior, this decision is the one to reopen — with
the 16/21 conda-dependency fact as the constraint any reopened design
must answer first.
