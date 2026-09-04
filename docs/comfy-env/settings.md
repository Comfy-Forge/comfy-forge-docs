# Settings reference

Every comfy-env setting, its default, and how to change it.

## How settings resolve

Three tiers, highest priority first. All settings are **machine-global**:
1. **Environment variable** -- `COMFY_ENV_POOL_IPC=1 python main.py`
2. **Persistent file** -- `~/.comfy-env/settings.env`, plain `KEY=VALUE`
   lines; edited comfortably via the `comfy-env settings` TUI.
3. **Built-in default**

Truthy values for boolean env vars: `1`, `true`, `yes` (case-insensitive).

## General settings

| Env var | default | meaning |
|---|---|---|
| `COMFY_ENV_POOL_IPC` | **off** | **Experimental, Linux-only, known-unsound** pool-based zero-copy GPU transfer. Enabling it prints a loud warning; do not use outside experiments -- see [ADR-0030](adr/0030-gpu-platform-floors.md) / [ADR-0005](adr/0005-tiered-tensor-serialization.md). |

## Memory management

Full reference, including what each level requires of your ComfyUI:
[comfy-env's approach to memory management](memory-approach.md). Design and measurements:
[ADR-0038](adr/0038-the-memory-floor.md).

| Env var | default | meaning |
|---|---|---|
| `COMFY_ENV_MEMORY_MANAGEMENT` | `auto` | Ordered level: `off`, `ledger`, `paged`, `shared`, or `auto`. `auto` picks the highest your ComfyUI and pack environment support and logs a line naming what stopped it. An explicit level too high for the host runs the highest available, loudly; it never refuses to start a pack. |
| `COMFY_ENV_MEMORY_OBSERVER` | **off** | Registers a read-only listener in ComfyUI's loaded-model list so the Free-memory button reaches packs and host memory pressure is visible. Off because this is the one remaining coupling with a breakage history; see [ADR-0038](adr/0038-the-memory-floor.md). |

!!! note "Replaces seven separate variables"

    `COMFY_ENV_WORKER_AIMDO`, `COMFY_ENV_PIN_SPLIT`, `COMFY_ENV_PIN_FLOOR`,
    `COMFY_ENV_PIN_RESERVE`, `COMFY_ENV_PIN_SHARE`,
    `COMFY_ENV_PIN_HEADROOM` and `COMFY_ENV_RESIDENCY_REFRESH` are gone.
    The features they gated are either derived from the level above or were
    deleted outright: the pin-split allocation half never shipped and was
    removed once it was clear that ComfyUI's own `ensure_pin_budget` is what
    actually bounds pinning, and that the same ceiling sizes each model's
    host buffer.

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

Same three-tier resolution, persistent file `~/.comfy-env/debug.env`,
TUI: the Debug tab of `comfy-env settings`. `COMFY_ENV_DEBUG=1` turns everything on;
individual categories:

1. `COMFY_ENV_DEBUG_SERIALIZE` -- tensor/shm serialization
2. `COMFY_ENV_DEBUG_IPC` -- socket frames
3. `COMFY_ENV_DEBUG_WORKER` -- worker lifecycle
4. `COMFY_ENV_DEBUG_MODELS` -- model registry/eviction
5. `COMFY_ENV_DEBUG_META` -- metadata scans
6. `COMFY_ENV_DEBUG_INSTALL` -- env building
7. `COMFY_ENV_DEBUG_INPUTS_OUTPUTS` -- per-call I/O summaries
8. `COMFY_ENV_DEBUG_VRAM` -- VRAM polling
9. `COMFY_ENV_DEBUG_WATCHDOG` -- worker watchdog thread dumps

Workers cannot import the settings module (different env), so debug env
vars are forwarded to and parsed by workers directly.

Other `COMFY_ENV_*` variables you may see in a worker's environment
(`COMFY_ENV_SERIALIZER_FILES`, `COMFY_ENV_ACCEL_PKGS`, ...) are internal
plumbing -- the parent->worker spawn channel, documented in
[The process boundary](process-boundary.md#the-spawn-time-channel). Never
set them.
