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

## Paths

| Env var | default | meaning |
|---|---|---|
| `COMFY_ENV_ROOT` | `%LOCALAPPDATA%\Programs\comfy-env` (Windows), `~/.ce` (Unix) | Override the machine-wide workspace root where envs materialize (ADR-0007). |
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
