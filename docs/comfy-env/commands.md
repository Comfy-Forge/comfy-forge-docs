# Commands

`comfy-env` has seven subcommands. Two matter day to day --
[`install`](#comfy-env-install) and [`gc`](#comfy-env-gc) -- and two exist
mainly for bug reports ([`info`](#comfy-env-info),
[`doctor`](#comfy-env-doctor)).

| Command | What it does |
|---|---|
| [`install`](#comfy-env-install) | Build/refresh every isolated env for a pack |
| [`init`](#comfy-env-init) | Scaffold a config file in the current directory |
| [`info`](#comfy-env-info) | Show the detected runtime (OS, python, torch, CUDA, GPU) |
| [`doctor`](#comfy-env-doctor) | Environment report plus pointers for deeper checks |
| [`settings`](#comfy-env-settings) | TUI over `~/.comfy-env/settings.env` |
| [`debug`](#comfy-env-settings) | Same TUI, opened on the Debug-logging tab |
| [`gc`](#comfy-env-gc) | List (and optionally delete) orphaned envs |

`comfy-env --version` prints the installed version.

## `comfy-env install`

The CLI face of [`install()`](install.md): discovers every bindable
`comfy-env.toml` under the pack, resolves the cuda-wheels combo, writes the
per-env manifests, and runs `pixi install` for each stale env. Everything on
that page applies; the flags are the only CLI-specific part:

| Flag | Meaning |
|---|---|
| `--dir`, `-d` | The pack directory. **Use this.** Without it, the config is resolved from the *current* directory, which fails from the ComfyUI root -- `comfy-env install --dir <pack>` is the spelling that works from anywhere, and the one error messages print. |
| `--config`, `-c` | Explicit config path, for a config living somewhere unusual. |
| `--dry-run` | Derive and report; write no manifests, install nothing. |

Exit is non-zero on failure, with the reasons batched per
[When it fails](install.md#when-it-fails).

## `comfy-env init`

Writes a starter config in the current directory:

| Invocation | Creates |
|---|---|
| `comfy-env init` | `comfy-env-root.toml` (pack root: `[node_packs]`, `[types]`) |
| `comfy-env init --isolated` | `comfy-env.toml` (an isolated env definition) |

Refuses to overwrite an existing file unless `--force` is passed. The two
files' roles are [Config reference](config.md).

## `comfy-env info`

Prints the detected runtime -- OS, platform tag, python, torch, CUDA, GPU
name and compute capability. `--json` emits the same as machine-readable
JSON. This is the block to paste into a bug report.

## `comfy-env doctor`

The environment report from `info`, plus pointers. It deliberately does
**not** import packages to check them: the host-env principle guarantees an
isolated env's dependencies are absent from the host interpreter, so an
import-based check reported every *working* install as broken. For package
and accelerator checks it points at `comfy-test lint --check accel`, which
resolves real import names from `env.stamp.json` instead of guessing
(`faithc-aot` installs `faithcontour`).

## `comfy-env settings`

Tabbed TUI over the persistent settings file
(`~/.comfy-env/settings.env`). The settings themselves -- what exists, what
each does, env-var precedence -- are the
[Settings reference](settings.md). `comfy-env debug` is the same TUI opened
on the Debug-logging tab.

## `comfy-env gc`

Lists every env in the machine-global workspace that no installed node
references, with sizes. Envs are 6-11 GB each and **nothing else ever
deletes one** -- every ABI bump and every pack rename orphans a full copy,
so without this the workspace only grows
([ADR-0028](adr/0028-workspace-disk-lifecycle.md)).

| Flag | Meaning |
|---|---|
| *(none)* | Dry run: list candidates and sizes, delete nothing. |
| `--delete` | Actually delete what the dry run listed. |
| `--comfyui-dir` | Which ComfyUI's `custom_nodes` anchors the referenced set (default: auto-detect from cwd). |

!!! warning "The referenced set is per-install"
    "Referenced" means referenced by *this* ComfyUI install. On a machine
    with several installs (or several stacks), another install's live env is
    unreferenced from here and **will be listed**. Run the dry run, read the
    list, then `--delete`. This is why dry-run is the default.

`comfy-env cleanup` is a deprecated alias.
