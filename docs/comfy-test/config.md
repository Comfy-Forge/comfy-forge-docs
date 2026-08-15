# `comfy-test.toml` reference

The config file lives at the root of your node pack. **Unknown keys are a
hard error** -- the run aborts before building anything
([ADR-0006](adr/0006-config-is-a-hard-fail-allowlist.md)), because a
silently-ignored key once produced a plausible green result for a run that
tested the wrong thing.

## Minimal file

```toml
[test]
name = "ComfyUI-YourPack"
levels = ["syntax", "install", "registration", "execution"]

[test.platforms]
platforms = ["linux-cpu", "windows-cpu"]

[test.workflows]
workflows = ["all"]
```

## `[test]`

| Key | Type | Default | Meaning |
|-----|------|---------|---------|
| `name` | string | **required** | Your pack's directory name (as installed under `custom_nodes/`). |
| `levels` | list | see below | Which levels to run; a set, not a sequence ([levels](levels.md)). |
| `comfyui_version` | string | `"latest"` | ComfyUI ref to test against. `latest` clones HEAD -- the resolved version and commit are recorded in results. |
| `python_version` | string | *random* | Drawn from 3.10-3.13 per run unless set ([ADR-0005](adr/0005-pinned-torch-random-python.md)). |
| `torch_version` | string | `"2.10.0"` | Key into the pinned torch triple table, or `"latest"` to opt out of pinning. |
| `extra_pip_indices` | list | `[]` | Additional pip index URLs (e.g. a CUDA wheel index). |
| `timeout` | int | `600` | Per-level timeout, seconds. |
| `res` | int | `1080` | Capture resolution (viewport height) for screenshots and video. |
| `custom` | string | none | Import path of a `run(ctx)` hook for the `custom` level. |

Default `levels`: `syntax`, `install`, `registration`, `instantiation`,
`static_capture`, `validation`, `execution`. The opt-in levels --
`coverage`, `javascript`, `custom` -- must be listed explicitly.

## `[test.platforms]`

```toml
[test.platforms]
platforms = ["linux-cpu", "windows-cuda", "macos-desktop"]
```

An allowlist of ids from the [platform table](lanes.md); unknown tokens are
an error, and there are no per-platform booleans
([ADR-0008](adr/0008-platforms-are-opt-in.md)).

Per-platform overrides use the platform's config key:

```toml
[test.windows_portable]
comfyui_portable_version = "v0.3.60"
skip_workflow = true
```

| Key | Type | Default | Meaning |
|-----|------|---------|---------|
| `enabled` | bool | `true` | Off switch for a listed platform. |
| `skip_workflow` | bool | `false` | Run the pipeline but not the workflows. |
| `comfyui_portable_version` | string | none | Pin the portable bundle (portable kinds only). |

## `[test.workflows]`

```toml
[test.workflows]
workflows = ["all"]          # or explicit names, or "!exclude_this"
cpu = ["light_workflow"]     # backend-specific subsets
cuda = ["all"]
timeout = 3600
```

| Key | Type | Default | Meaning |
|-----|------|---------|---------|
| `workflows` | list | `[]` | Which workflows to run. `"all"` selects everything in `workflows/`; a `"!name"` entry excludes. |
| `cpu` / `cuda` / `rocm` | list | `[]` | Backend-specific selection; overrides `workflows` on that backend. |
| `timeout` | int | `3600` | Per-workflow timeout, seconds. |

An empty list means *nothing runs* -- it is not a synonym for "all". The
`gpu` key does not exist and is specifically diagnosed in the error message,
because it is the typo that caused the incident behind
[ADR-0006](adr/0006-config-is-a-hard-fail-allowlist.md).

**Deprecated** (`run`, `screenshot`, `files`, `file`): migrated silently for
now, will become unknown keys -- do not use them in new configs.

## `[test.coverage]`

```toml
[test.coverage]
inputs = { GeomPackLoadMesh = { file_path = "3d/cube.glb" } }
```

Declares inputs so the `coverage` level can account for nodes that need
values to be exercised.

## `[test.javascript]`

```toml
[test.javascript]
namespaces = ["geometrypack"]
```

Extra allowed namespace prefixes for the isolation lint. Normally
unnecessary: the required namespace is derived from your pack's
`[tool.comfy] DisplayName`, lowercased. Declare only if your pack
legitimately ships more than one
([ADR-0014](adr/0014-javascript-isolation-is-static.md)).
