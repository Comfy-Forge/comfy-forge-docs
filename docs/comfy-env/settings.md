# Settings reference

Every comfy-env setting, its default, and how to change it.

## How settings resolve

Four tiers, highest priority first -- **most specific wins** (a per-pack
declaration is more specific than a global environment variable):

1. **Per-pack `[settings]`** in that pack's `comfy-env-root.toml`, using
   the short keys below:

    ```toml
    [settings]
    isolate = false          # this pack's nodes run in-process
    ```

2. **Environment variable** -- `COMFY_ENV_ISOLATE=0 python main.py`
3. **Persistent file** -- `~/.comfy-env/settings.env`, plain `KEY=VALUE`
   lines; edited comfortably via the `comfy-env settings` TUI. Loaded with
   `setdefault`, so it fills *unset* env vars and can never override an
   explicitly-set one.
4. **Built-in default**

Truthy values for boolean env vars: `1`, `true`, `yes` (case-insensitive).

## General settings

| Env var | short key (`[settings]`) | default | meaning |
|---|---|---|---|
| `COMFY_ENV_ISOLATE` | `isolate` | **on** | Run isolated nodes in subprocess workers. Off = everything imports in-process (isolation disabled, banner still prints). |
| `COMFY_ENV_INSTALL_ISOLATED` | `install_isolated` | **on** | `install()` materializes pixi envs (the workspace half). Off = plugin half only. |
| `COMFY_ENV_AUTO_INSTALL` | `auto_install` | **off** | Materialize a missing env at `register_nodes()` time. Off by default because installs take minutes and block startup. |
| `COMFY_ENV_POOL_IPC` | `pool_ipc` | **off** | **Experimental, Linux-only, known-unsound** pool-based zero-copy GPU transfer. Enabling it prints a loud warning; do not use outside experiments -- see [ADR-0030](adr/0030-gpu-platform-floors.md) / [ADR-0005](adr/0005-tiered-tensor-serialization.md). |
| `COMFY_ENV_WORKER_VRAM_BUDGET` | `worker_vram_budget` | `0` (auto) | Worker VRAM budget in GB for the budget-negotiation callback. |

## Transport

| Env var | default | meaning |
|---|---|---|
| `COMFY_ENV_TRANSPORT_PROBE` | **on** | The canary handshake (ADR-0005): round-trips a tensor through the production serialization path at worker creation; broken CPU tier refuses the worker, broken GPU tier demotes it loudly. Set `0` to skip. |

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

## Internal (set by comfy-env itself -- not user-facing)

| Env var | set by | consumed by |
|---|---|---|
| `COMFY_ENV_ACCEL_PKGS` | `register_nodes()` from `[cuda].packages` | metadata scan's top-level-import check ([accelerator rule](accelerators.md)) |
| `COMFY_ENV_SERIALIZER_FILES` | `register_nodes()` from `[types]` custom entries (`serialization.py` paths) | worker startup, to load custom type serializers ([ADR-0015](adr/0015-declared-wire-types.md)) |

`COMFY_TEST_MOCK_PACKAGES` is the comfy-test harness's variable
(interpreted by comfy-env at import; see the accelerator page for its
planned retirement).
