# Settings reference

Every comfy-test setting, its default, and how to change it. These are
**machine-global** knobs -- what a run prints, where it writes, and how much
it tells you when something goes wrong. Per-pack choices (which lanes,
which levels, which workflows) live in
[`comfy-test.toml`](config.md) instead.

## How settings resolve

Three tiers, highest priority first:

1. **Environment variable** -- `COMFY_TEST_VERBOSE=1 comfy-test run`
2. **Persistent file** -- `~/.comfy-test/settings.env`, plain `KEY=VALUE`
   lines; edited comfortably via the `comfy-test settings` TUI.
3. **Built-in default**

The persistent file is loaded with `setdefault`, so an environment variable
always wins over it.

Truthy values for boolean env vars: `1`, `true`, `yes`. Anything else --
including `0`, `false`, and any typo -- reads as off.

## General

| Env var | default | meaning |
|---|---|---|
| `COMFY_TEST_RUN_CONSUMER` | **on** | Discover the pack's user-facing workflows (`example_workflows/` and its aliases -- see [what a pack looks like](using.md#what-a-pack-looks-like)). Turning this off makes those workflows invisible to the run. |
| `COMFY_TEST_RUN_DEV` | **on** | Discover dev-only workflows from the `tests/` subfolder of any workflow directory. |
| `COMFY_TEST_VERBOSE` | off | Echo every ComfyUI server line, not just the interesting ones. |
| `COMFY_TEST_SHOW_CONSOLE_ERRORS` | off | Surface browser console **errors** in the run output. |
| `COMFY_TEST_SHOW_CONSOLE_WARNINGS` | off | Surface browser console **warnings** in the run output. |
| `COMFY_TEST_VRAM_DEBUG` | off | VRAM accounting log lines during execution. |

!!! warning "The two discovery switches change what is tested"
    `COMFY_TEST_RUN_CONSUMER` and `COMFY_TEST_RUN_DEV` are not output
    verbosity -- they decide **which workflow files exist as far as the run is
    concerned**. With both off, every workflow-driven level has nothing to do.
    They default on; leave them there unless you are deliberately narrowing a
    local run.

## Paths

| Env var | default | meaning |
|---|---|---|
| `COMFY_TEST_LOGS_DIR` | `~/comfy-test-logs` | Where run output trees are written. This is the directory CI uploads as an artifact. |
| `COMFY_TEST_WORKSPACE_DIR` | `~/test_workspaces` | Where environments are built (venvs, ComfyUI clones, portable extracts). **Not** part of the CI artifact path. |
| `COMFY_TEST_LOCAL_UTILS` | *(unset)* | Directory holding local checkouts of `comfy-env` / `comfy-test` / `comfy-3d-viewers`. When set, they are installed editable so `install.py` exercises your working tree instead of the published release. |

`comfy-test paths --set` writes the first two persistently; it runs
automatically on your first non-attach run if they are unset.

### Docker host artifacts

`comfy-test docker` keeps its host-side artifacts under a single root,
picked in this order:

1. `COMFY_TEST_DOCKER_ROOT`, if set
2. Windows: the first Trusted Developer Volume with enough free space
   (`fsutil devdrv enum`) -> `<drive>:\docker`
3. Windows fallback: `C:\docker`
4. Otherwise: `~/.comfy-test/docker`

The layout under that root is `logs/`, `stage/`, `installers/`,
`workspaces/`, `env-cache/`, `artifacts/`. Each component can be overridden
individually, and an explicit override always beats the derived default:

| Env var | overrides |
|---|---|
| `COMFY_TEST_DOCKER_ROOT` | the root itself |
| `COMFY_TEST_LOGS_DIR` | `<root>/logs` |
| `COMFY_TEST_DOCKER_STAGE_DIR` | `<root>/stage` (Windows robocopy staging) |
| `COMFY_TEST_INSTALLER_CACHE` | `<root>/installers` (auto-downloaded driver/git installers) |
| `COMFY_TEST_INSTALLERS_DIR` | a directory of **pre-staged** installers; leave unset to auto-download |
| `COMFY_TEST_DOCKER_ARTIFACT_PATH` | where `docker build --save` writes `<image>.tar.zst` |

## Debug logging

Off by default, one category per subsystem. Same three-tier resolution as
above; the `comfy-test settings` TUI exposes them on their own tab.

| Env var | covers |
|---|---|
| `COMFY_TEST_DBG_WORKER` | worker subprocess IPC |
| `COMFY_TEST_DBG_SCREENSHOT` | screenshot and frame capture |
| `COMFY_TEST_DBG_WEBSOCKET` | WebSocket messages to and from ComfyUI |
| `COMFY_TEST_DBG_VALIDATION` | workflow validation tiers |

## Set by the harness, not by you

These are written by CI, the docker/VM wrappers, or the desktop runner, and
are listed so a value you see in a log is identifiable. Setting them by hand
is not supported.

- **Run identity and provenance** -- `COMFY_TEST_RUN_URL` (deep-link back to
  the GHA run), `COMFY_TEST_NODE_SHA`, `COMFY_TEST_NODE_URL`,
  `COMFY_TEST_NODE_BRANCH`, `COMFY_TEST_NODE_NAME`.
- **Lane and backend selection** -- `COMFY_TEST_LANE` (raises if it
  disagrees with the host), `COMFY_TEST_BACKEND`, `COMFY_TEST_CUDA`,
  `COMFY_TEST_PYTHON_VERSION`, `COMFY_TEST_TORCH_VERSION`.
- **Sandbox and container context** -- `COMFY_TEST_IN_DOCKER`,
  `COMFY_TEST_IN_SANDBOX`, `COMFY_TEST_SANDBOX_ROOT`,
  `COMFY_TEST_SESSION_USER`.
- **comfy-env interop** -- `COMFY_ENV_CUDA_VERSION`, `COMFY_ENV_CACHE_DIR`,
  set so wheel resolution works on CPU-only CI
  (see [Relationship to comfy-env](index.md#relationship-to-comfy-env)).

!!! note "`COMFY_TEST_TORCH_VERSION` outranks your config"
    When it is set, it wins over `[test] torch_version` in
    `comfy-test.toml`. CI sets it per lane, which is why a local run and a CI
    run can pin different torch versions from the same config file. The value
    that actually ran is recorded in `provenance.torch_version` -- read that,
    not the config.
