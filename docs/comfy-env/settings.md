# Settings reference

Every comfy-env setting, its default, and how to change it.

## How settings resolve

Three tiers, highest priority first. All settings are **machine-global**
(the per-pack `[settings]` section was removed in 0.4.25):

1. **Environment variable** -- `COMFY_ENV_AUTO_INSTALL=1 python main.py`
2. **Persistent file** -- `~/.comfy-env/settings.env`, plain `KEY=VALUE`
   lines; edited comfortably via the `comfy-env settings` TUI. Loaded with
   `setdefault`, so it fills *unset* env vars and can never override an
   explicitly-set one.
3. **Built-in default**

Truthy values for boolean env vars: `1`, `true`, `yes` (case-insensitive).

## General settings

| Env var | default | meaning |
|---|---|---|
| `COMFY_ENV_AUTO_INSTALL` | **off** | Materialize a missing env at `register_nodes()` time. Off by default because installs take minutes and block startup. |
| `COMFY_ENV_POOL_IPC` | **off** | **Experimental, Linux-only, known-unsound** pool-based zero-copy GPU transfer. Enabling it prints a loud warning; do not use outside experiments -- see [ADR-0030](adr/0030-gpu-platform-floors.md) / [ADR-0005](adr/0005-tiered-tensor-serialization.md). |

## Paths

| Env var | default | meaning |
|---|---|---|
| `COMFY_ENV_ROOT` | `%LOCALAPPDATA%\Programs\comfy-env` (Windows), `~/.ce` (Unix) | Override the machine-wide workspace root where envs materialize (ADR-0007). |

## Debug logging

Same three-tier resolution, persistent file `~/.comfy-env/debug.env`,
TUI via `comfy-env debug`. `COMFY_ENV_DEBUG=1` turns everything on;
individual categories:

`COMFY_ENV_DEBUG_SERIALIZE` (tensor/shm serialization) ·
`COMFY_ENV_DEBUG_IPC` (socket frames) ·
`COMFY_ENV_DEBUG_WORKER` (worker lifecycle) ·
`COMFY_ENV_DEBUG_MODELS` (model registry/eviction) ·
`COMFY_ENV_DEBUG_META` (metadata scans) ·
`COMFY_ENV_DEBUG_INSTALL` (env building) ·
`COMFY_ENV_DEBUG_STACKTRACE` ·
`COMFY_ENV_DEBUG_INPUTS_OUTPUTS` (per-call I/O summaries) ·
`COMFY_ENV_DEBUG_VRAM` (VRAM polling) ·
`COMFY_ENV_DEBUG_WATCHDOG` (worker watchdog thread dumps)

Workers cannot import the settings module (different env), so debug env
vars are forwarded to and parsed by workers directly.

Other `COMFY_ENV_*` variables you may see in a worker's environment
(`COMFY_ENV_SERIALIZER_FILES`, `COMFY_ENV_ACCEL_PKGS`, ...) are internal
plumbing -- the parent->worker spawn channel, documented in
[The process boundary](process-boundary.md#the-spawn-time-channel). Never
set them.
