# Commands

`comfy-env` has five subcommands.

| Command | What it does |
|---|---|
| [`install`](#comfy-env-install) | Build/refresh every isolated env for a pack |
| [`init`](#comfy-env-init) | Scaffold a config file in the current directory |
| [`info`](#comfy-env-info) | Show the detected runtime (OS, python, torch, accelerator) |
| [`settings`](#comfy-env-settings) | TUI for debug-logging categories (`~/.comfy-env/debug.env`) |
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
| `--dry-run` | Runs the derivation and stops before `pixi install`: discovers every env, resolves the torch/CUDA combo and the CUDA-wheel URLs, and **writes the `pixi.toml` of every env whose [fast key](seals.md) missed** (or that sits on a fallback combo) -- the manifests plus the printed log *are* the report. Envs whose fast key matches are never re-derived, so on a clean install it writes nothing; for the stale ones it skips the identity comparison and writes the manifest unconditionally. Nothing is downloaded and no env is created or modified. (Rewriting a stale env's manifest is harmless to a live install, since workers launch with `pixi run --as-is` and a real install re-derives from config, not from these files.) |

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

Prints the detected runtime -- OS, platform tag, python, torch, and the
accelerator (CUDA version, GPU name, compute capability) -- **and then every
materialized env**, with the stack each was built for:

```
Environments (/home/you/.ce/envs)
========================================
  geometrypack-nodes_py313-torch2.8-cu128
      py313-torch2.8-cu128  <- this stack
  geometrypack-nodes_py310-torch2.10-cpu
      py310-torch2.10-cpu   from ComfyUI-GeometryPack/nodes/comfy-env.toml
  trellis2-nodes
      unstamped (predates stamping; cannot be verified)
```

Each stack is read from that env's own `env.stamp.json`, not parsed out of
the directory name, so an env [adopted under an older
spelling](adr/0039-env-directory-naming.md) reports correctly. `<- this
stack` marks the one this ComfyUI would bind; `unstamped` marks an env from
before stamping, which cannot be verified and is therefore always safe to
delete.

`--json` emits the runtime block as machine-readable JSON. **This is the
block to paste into a bug report**, and the one command to ask for when
someone says a pack is not loading.

## `comfy-env settings`

One TUI, one tab, one file:

| Tab | Toggles | Persisted to |
|---|---|---|
| Debug logging | ten stderr-narration switches -- a master (`COMFY_ENV_DEBUG=1` = everything) plus per-subsystem categories: node inputs/outputs, VRAM around node calls, tensor serialization, CUDA IPC, worker lifecycle, worker watchdog, model registration, metadata scans, env install | `~/.comfy-env/debug.env` |

Every toggle is also just an env var, and the env var wins over the file.

There was a second tab, persisting comfy-env's feature flags to
`~/.comfy-env/settings.env`. Both are gone: nothing on the ComfyUI runtime
path imported the module that read that file, so those toggles reported
themselves as on and reached no worker. comfy-env's remaining feature flags
are environment variables only -- the full list is in the
[Settings reference](settings.md), debug categories under
[Debug logging](settings.md#debug-logging).

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
