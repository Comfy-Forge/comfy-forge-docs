# Settings reference

Every comfy-env setting, its default, and how to change it.

## How settings resolve

Every setting on this page is an **environment variable** or a **built-in
default**, and all of them are **machine-global**. There is no settings file
and no per-pack override:

```
COMFY_ENV_POOL_IPC=1 python main.py
```

Boolean env vars are parsed by whichever module reads them, and the rules
differ (all case-insensitive):

- `COMFY_ENV_DEBUG*` and `COMFY_ENV_POOL_IPC` are **on only** for `1`, `true`
  or `yes`; anything else, including unset, is off.
- `COMFY_ENV_PIN_MARKS`, `COMFY_ENV_MIRROR_ARGS` and `COMFY_ENV_NODE_STATE`
  are **on unless** set to `0`, `false` or `off`.
- `COMFY_ENV_WORKER_AIMDO` is on for any non-empty value except `0`, `false`,
  `no` or `off`; unset means "no parent signal", which lands on the ledger.

The one exception is debug logging, which has a persistent file as well --
see [Debug logging](#debug-logging) for why that one can work and a general
settings file could not.

## General settings

| Env var | default | meaning |
|---|---|---|
| `COMFY_ENV_SCAN_TIMEOUT` | 300 | Seconds one pack's metadata scan (its import plus every node's `INPUT_TYPES`) may take before the whole scan process tree is killed and the startup log names the node it was on. Generous because a first import of a torch-heavy pack on a cold disk is legitimately slow; lower it while hunting a hang. |
| `COMFY_ENV_AFFINITY_PIN` | auto (WSL only) | Whether a worker pins itself to one CPU core for the duration of `import torch`. Needed on WSL2, whose per-core clocks are not synchronised (pytorch#129992); pure cost elsewhere, and until 2026-09-13 it was applied on every Linux to core 0, so parallel spawns queued their imports on one core. `1` forces the pin, `0` forbids it. |
| `COMFY_ENV_POOL_IPC` | **off** | **Experimental, Linux-only, known-unsound** pool-based zero-copy GPU transfer. Enabling it prints a loud warning; do not use outside experiments -- see [ADR-0030](adr/0030-gpu-platform-floors.md) / [ADR-0005](adr/0005-tiered-tensor-serialization.md). |

## Memory management

Full reference: [comfy-env's memory management](memory-approach.md). Design
and measurements: [ADR-0038](adr/0038-the-memory-floor.md).

| Env var | default | meaning |
|---|---|---|
| `COMFY_ENV_WORKER_AIMDO` | follow host | The parent writes `1` or `0` into every worker's environment to match its own `comfy.memory_management.aimdo_enabled`, so a worker pages exactly when the host does. Pin `0` (in the environment or a pack's `[env_vars]`) to stop a worker enabling comfy-aimdo, so it runs the legacy ledger instead of paging. Every failure path already falls through to the ledger; this forces it. A worker with the variable unset also lands on the ledger ("no parent signal"). The one memory switch an operator is likely to want, and the one comfy-env's own warning tells them to reach for. |
| `COMFY_ENV_RESIDENCY_REFRESH` | `boundary` | Whether the host applies the residency census riding every worker frame. `boundary` (or any value other than the ones below) applies it; `off`, `command`, `0` and `false` all do the same one thing -- skip the frame census. Command echoes (the reply to an eviction command) are applied regardless of this setting, so `command` and `off` are two spellings of one behaviour. Deliberately has no interval knob. |
| `COMFY_ENV_PIN_MARKS` | on | Gates the prompt-epoch pin marks that protect a worker's in-use models from its own pin eviction. Off restores byte-identical pre-mark behaviour. |
| `COMFY_ENV_NODE_STATE` | `sync` | Whether a node's mutated `self` state returns from the worker. `off` is the pre-2026-09 in-only wire. |
| `COMFY_ENV_NODE_STATE_MAX_BYTES` | 8 MiB | Per-attribute cap on returned state. Anything larger stays worker-held behind a named marker: never silently truncated, never shipped. |
| `COMFY_ENV_MIRROR_ARGS` | on | `0` disables the whole host-to-worker CLI flag mirror. A pack's `[env_vars]` cannot unset an args write, so this is the escape of last resort. |
| `COMFY_ENV_NO_MIRROR` | unset | Comma list of individual flags to withhold from the mirror. The global switch is too big a hammer: turning it off to escape one `--fast-disk` regression would also surrender the fp8 dtype mirror and reinstate a 2x footprint. |
| `COMFY_ENV_WORKER_ATTENTION` | follow host | `auto` restores the worker's own attention auto-probe, for a pack env richer than the host's. |

## Paths

| Env var | default | meaning |
|---|---|---|
| `COMFY_ENV_ROOT` | `%LOCALAPPDATA%\Programs\comfy-env` (Windows), `~/.ce` (Unix) | Override the machine-wide workspace root where envs materialize (ADR-0007). **Read [Drives and volumes](drives-and-volumes.md) first** -- moving the workspace without also moving the package cache silently disables dedup. |
| `COMFY_ENV_CUDA_WHEELS_INDEX` | `https://comfy-forge.github.io/cuda-wheels/` | Base URL of the [cuda-wheels index](../cuda-wheels/index.md). Point it at a mirror you host. A missing trailing slash is added for you. **This is a trust boundary** -- see the warning below. |

!!! danger "The wheel index is a trust boundary"
    Wheels resolved from `COMFY_ENV_CUDA_WHEELS_INDEX` are inlined into the
    generated manifest as direct-URL dependencies. They are hash-verified
    **only when the index anchor carries a `#sha256=` fragment** (the default
    index does); a mirror that omits fragments serves unverified binaries
    that execute at import time inside the isolated env. Point this only at
    an index you control, or trust as much as the default
    ([ADR-0026](adr/0026-trust-and-supply-chain.md)).

## Debug logging

Debug categories resolve from an environment variable **or** from the
persistent file `~/.comfy-env/debug.env` (plain `KEY=VALUE` lines, env var
wins), editable via `comfy-env settings`. `COMFY_ENV_DEBUG=1` turns everything
on; individual categories:

1. `COMFY_ENV_DEBUG_SERIALIZE` -- tensor/shm serialization
2. `COMFY_ENV_DEBUG_IPC` -- socket frames
3. `COMFY_ENV_DEBUG_WORKER` -- worker lifecycle
4. `COMFY_ENV_DEBUG_MODELS` -- model registry/eviction
5. `COMFY_ENV_DEBUG_META` -- metadata scans
6. `COMFY_ENV_DEBUG_INSTALL` -- env building
7. `COMFY_ENV_DEBUG_INPUTS_OUTPUTS` -- per-call I/O summaries
8. `COMFY_ENV_DEBUG_VRAM` -- VRAM polling
9. `COMFY_ENV_DEBUG_WATCHDOG` -- worker watchdog thread dumps

Workers cannot import any comfy_env module (different env), so debug env vars
are forwarded to and parsed by workers directly.

!!! note "Why debug has a file and general settings do not"
    A file tier only reaches readers that import the module which loads it.
    `comfy_env.debug` is imported on the ComfyUI runtime path, so a key in
    `debug.env` lands in `os.environ` before a worker is spawned and is
    inherited by it. `comfy_env.settings` loads no file: it is imported from
    `comfy_env/__init__.py` today only so its removed-variable tombstones
    can raise, but when the general `~/.comfy-env/settings.env` existed the
    module was off the ComfyUI runtime path entirely and the file was read
    only by the CLI and the installer -- a toggle that reported itself as on
    and changed nothing about how workers ran. It was deleted rather than
    wired up; the settings it held are environment variables now.

Other `COMFY_ENV_*` variables you may see in a worker's environment
(`COMFY_ENV_SERIALIZER_FILES`, `COMFY_ENV_ACCEL_PKGS`, ...) are internal
plumbing -- the parent->worker spawn channel, documented in
[The process boundary](process-boundary.md#the-spawn-time-channel). Never
set them.
