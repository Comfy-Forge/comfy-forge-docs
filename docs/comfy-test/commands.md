# Commands

`comfy-test` has ten subcommands. Most people only ever need two --
[`run`](#comfy-test-run) and [`publish`](#comfy-test-publish) -- but the static
checks are worth knowing about because they need no install and no server.

| Command | What it does |
|---|---|
| [`run`](#comfy-test-run) | Run the test levels against a pack |
| [`publish`](#comfy-test-publish) | Push a results tree to a repo's `gh-pages` |
| [`lint`](#comfy-test-lint) | Static checks only -- no env, no server |
| [`coverage`](#comfy-test-coverage) | Which registered nodes no workflow uses |
| [`paths`](#comfy-test-paths) | Show or configure where runs are written |
| [`settings`](#comfy-test-settings) | TUI over `~/.comfy-test/settings.env` |
| [`generate-index`](#comfy-test-generate-index) | Render the gh-pages index pages |
| [`docker`](#comfy-test-docker) | Image lifecycle and containerised runs |
| [`vm`](#comfy-test-vm) | Hyper-V baseline VM lifecycle |
| [`sandbox`](#comfy-test-sandbox) | Windows Sandbox runner |

## `comfy-test run`

Runs the [levels](levels.md) against a pack.

### Naming the pack

Four forms, resolved in this order:

| Form | What happens |
|---|---|
| `comfy-test run` | the pack in the current directory |
| `comfy-test run ../ComfyUI-MyPack` | an **existing local directory** -- used in place, never cloned |
| `comfy-test run owner/repo` | GitHub shorthand -- expanded to `https://github.com/owner/repo.git`, shallow-cloned to a tempdir |
| `comfy-test run https://github.com/…` | any git URL -- shallow-cloned |

Two edges worth knowing, because both produce a confusing error rather than an
obvious one:

- **The path check runs first.** A local directory literally named `foo/bar`
  wins over the GitHub reading of the same string.
- **The shorthand needs exactly one `/`.** `owner/repo/subdir` has two, so it
  is treated as a URL and fails in `git clone`.

Whatever the form, the directory must contain an **`__init__.py`** -- that is
what ComfyUI imports to load a pack, so without it nothing can register. This
is checked before any environment is built, rather than several minutes later.

**Selection**

| Flag | |
|---|---|
| `--level`, `-l` | run only up to this level; for the four terminal levels it *replaces* the configured terminal ([ADR-0012](adr/0012-level-flag-swaps-terminals.md)) |
| `--workflow`, `-W` | run a single workflow |
| `--branch`, `-b` | branch to **clone**. Remote forms only -- see below |

!!! warning "`--branch` is a clone argument, not a label"
    It also names the branch folder in the output path, and that is the trap:
    on a **local** checkout nothing gets checked out, but the results would
    still be filed under the branch you named. Publish those and you overwrite
    the real results for that branch with a run of different code.

    So `--branch` on a local path is now an error. The branch is detected from
    the checkout automatically -- real branch, else the short SHA on a detached
    HEAD, else `local`.

**Where and how it runs**

| Flag | |
|---|---|
| `--cuda` | real CUDA instead of mocking. Rejected on macOS |
| `--portable` | the Windows portable bundle. Windows only |
| `--desktop` | drive ComfyUI Desktop over CDP. macOS/Windows only, mutually exclusive with `--portable` |
| `--server-url` | attach to a server CI already booted, instead of building one |
| `--comfyui-version` | ComfyUI ref. **Tag or branch only** -- the clone is `--depth 1 --branch`, so a SHA fails |
| `--torch-version` | override the pinned torch triple |

**Escape hatches**

| Flag | |
|---|---|
| `--config`, `-c` | path to `comfy-test.toml` when it is not at the pack root. Plumbed from `test-matrix.yml`'s `config-file` input; you should not normally need it |
| `--force` | overwrite an existing workspace directory |

**Diagnostics**

| Flag | |
|---|---|
| `--novram` | pass `--novram` to ComfyUI |
| `--vram-debug` | log model load/unload with a per-module breakdown |
| `--monitor-progress PORT` | `--desktop` only: live viewer on `http://localhost:PORT/` |
| `--dev` | `--desktop` only: swap the installed node to its dev branch after install |
| `--refresh-app` | `--desktop` only: discard the cached Desktop install and reinstall |

!!! note "There is no `--lane` flag"
    The lane is derived from the host. Use `--portable` / `--desktop` /
    `--cuda`, or set `COMFY_TEST_LANE` (which raises if it disagrees with
    the machine you are on).

Exit code is 0 when every level passed. Output goes to
`<logs>/<pack>-<YYYYMMDD-HHMM>/<branch>/<os>-<install-method>-<backend>/` --
see [what a run does](index.md#what-a-run-does).

## `comfy-test publish`

Pushes a results tree to a node repo's `gh-pages` as a dashboard: branch
switcher, lane tabs, per-workflow cards with media and logs
([ADR-0015](adr/0015-publish-is-a-separate-job.md)).

```bash
comfy-test publish logs/SAM3/dev/macos-cpu       # one lane's results
comfy-test publish logs/SAM3                     # a whole run -- rglob'd
comfy-test publish logs/SAM3 --repo owner/repo   # explicit target
```

| Argument | |
|---|---|
| `results_dir` | a lane directory, or any parent -- it is searched recursively for `results.json` |
| `--repo`, `-r` | target `owner/repo`. Auto-detected from the git `origin` remote if omitted |

Publishing is a **separate job from testing** so a flaky push can be re-run
without repeating a slow test. Authentication uses `NODE_PAT` / `GH_TOKEN` /
`GITHUB_TOKEN` when set.

!!! warning "Nothing to publish without a results file"
    `results.json` is written only by the execution levels and the desktop
    driver. A run whose terminal level was `validation` or `static_capture`
    produces none, and `publish` will find nothing.

## `comfy-test lint`

The static checks, with **no environment and no server** -- fast enough for a
pre-commit hook.

```bash
comfy-test lint                       # all checks, current directory
comfy-test lint --check javascript    # just one
comfy-test lint --json --strict       # machine-readable, fail on warnings too
```

| Argument | |
|---|---|
| `path` | node pack directory (default: `.`) |
| `--check`, `-k` | `syntax`, `javascript`, `accel`, or `all` (default) |
| `--json` | machine-readable output |
| `--strict` | exit non-zero on warnings as well as errors |

## `comfy-test coverage`

Reports which registered nodes no workflow exercises. Static -- no install, no
imports.

```bash
comfy-test coverage -v          # also list tested nodes and which workflows use them
comfy-test coverage --strict    # exit non-zero if anything is untested
```

| Argument | |
|---|---|
| `path` | node pack directory (default: `.`) |
| `--workflows` | override the workflows directory |
| `--verbose`, `-v` | also list tested nodes and the workflows using them |
| `--json` | machine-readable output |
| `--strict` | exit non-zero if any registered node or declared input value is untested |

This is the [`coverage` level](levels/coverage.md) standalone, which makes it
the natural pre-commit companion to `lint`.

## `comfy-test paths`

```bash
comfy-test paths          # show where logs and workspaces go
comfy-test paths --set    # interactive setup wizard
```

Configures `COMFY_TEST_LOGS_DIR` and `COMFY_TEST_WORKSPACE_DIR` persistently.
The wizard runs automatically on your first non-attach run if they are unset.
See the [settings reference](settings.md).

## `comfy-test settings`

A tabbed TUI over `~/.comfy-test/settings.env` -- general toggles, debug
categories, and paths. Every value is also an environment variable; the
[settings reference](settings.md) lists them all.

## `comfy-test generate-index`

Renders the gh-pages index: per-branch lane tabs plus the root branch
switcher. This is the `test-matrix.yml` publish path, distinct from
`comfy-test publish`, which `dispatch-test.yml` uses.

| Argument | |
|---|---|
| `output_dir` | gh-pages **root** (contains per-branch subdirectories) |
| `--branch` | branch subdirectory to render |
| `--repo-name` | repository name for the page header |

## GPU lane operations

These three manage the hosts that CUDA lanes run on. They are operator tools,
not something a pack author needs -- see
[Lanes](lanes.md#gpu-lanes).

### `comfy-test docker`

Bare `comfy-test docker` defaults to `list`.

| Subcommand | |
|---|---|
| `list` | known images, local load state, SMB artifacts |
| `build` | build an image. `--tag`, `--save`, `--no-smoke`, `--artifact-path`, installer overrides |
| `run` | containerised test run. Takes a nodelink plus `--branch`, `--cuda`, `--portable`, `--workflow`, `--logs-dir`, `--persist`, `--keep-clone` |

Host artifacts live under one root, picked in this order: `COMFY_TEST_DOCKER_ROOT`,
then a Windows Trusted Dev Drive, then `C:\docker`, then `~/.comfy-test/docker`.

### `comfy-test vm`

Hyper-V baseline VM lifecycle for `windows-desktop-cuda` -- the lane that needs
an interactive desktop session *and* a GPU, which containers cannot provide.
Bare `comfy-test vm` defaults to `list`.

| Subcommand | |
|---|---|
| `list` | known VMs and their state |
| `build` | one-time host setup, optionally a fully unattended Windows install |
| `snapshot` | take a clean snapshot |
| `restore` | revert to a snapshot, optionally waiting for the runner to come up |
| `gpu` | `attach` / `detach` a GPU by device assignment |
| `share` | manage an SMB share that survives snapshot restores |

### `comfy-test sandbox`

Windows Sandbox runner -- GPU-PV maps the host driver store into a pristine
disposable guest, with no image build and no snapshots.

| Subcommand | |
|---|---|
| `status` | whether Windows Sandbox is available and configured |

!!! note "No `run` subcommand yet"
    Sandbox runs are driven from the library entry point, not the CLI. `status`
    is the only exposed subcommand today.
