# Commands

`comfy-env` has six subcommands. Two matter day to day --
[`install`](#comfy-env-install) and [`gc`](#comfy-env-gc) -- and one exists
mainly for bug reports ([`info`](#comfy-env-info)).

| Command | What it does |
|---|---|
| [`install`](#comfy-env-install) | Build/refresh every isolated env for a pack |
| [`init`](#comfy-env-init) | Scaffold a config file in the current directory |
| [`info`](#comfy-env-info) | Show the detected runtime (OS, python, torch, CUDA, GPU) |
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
| `--dir`, `-d` | The pack directory. **Use this.** Without it, the config is resolved from the *current* directory, which fails from the ComfyUI root -- `comfy-env install --dir custom_nodes/<pack>` is the spelling that works from anywhere, and the one error messages print. |
| `--dry-run` | Runs the whole derivation and stops before `pixi install`: discovers every env, resolves the torch/CUDA combo and the CUDA-wheel URLs, and **writes each env's `pixi.toml`** -- the manifests plus the printed log *are* the report. Nothing is downloaded and no env is created or modified. (It does rewrite the per-env manifests on disk; harmless to a live install, since workers launch with `pixi run --as-is` and a real install re-derives from config, not from these files.) |

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
