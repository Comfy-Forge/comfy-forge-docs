# Config reference: the two TOML files

## Two files

Two files, two roles ([ADR-0003](adr/0003-two-config-files-with-two-roles.md)):

1. **`comfy-env-root.toml`** at the pack root:

     - Declares other node packs that this node pack depends on or uses
     - Declares node pack custom type serializers

3. **`comfy-env.toml`** at `nodes/` or `nodes/<subdir>`.

    - Gives that directory its own isolated pixi environment.
    - The file's *presence* is the isolation switch.

Those two locations are the only ones supported: discovery and the runtime
binder deliberately match, so a config anywhere else is simply not seen
rather than silently materialized-but-unused.

## Example node pack
Using [ComfyUI-GeometryPack](https://github.com/PozzettiAndrea/ComfyUI-GeometryPack)
as the example:

```
ComfyUI/custom_nodes/
`-- ComfyUI-GeometryPack/
    +-- comfy-env-root.toml     <- FILE 1: pack-wide. [types], [node_packs].
    |                              Declares no environment.
    +-- requirements.txt        <- one line: comfy-env. The host env gets
    |                              nothing else.
    +-- prestartup_script.py       setup_env()
    +-- install.py                 install()
    +-- __init__.py                register_nodes()
    `-- nodes/
        +-- comfy-env.toml      <- FILE 2: everything below is ONE isolated
        |                          env (cgal, igl, pyvista, trimesh, ...)
        +-- boolean/
        +-- remeshing/
        +-- skeleton/, uv/, io/, analysis/, ...   (29 dirs in total)
```

GeometryPack puts the env file at `nodes/`, so the whole directory is a
single environment.

The case where a pack needs **two or more environments** is also supported!

Node pack authors can put one `comfy-env.toml` in each `nodes/<subdir>/` instead -- one env per subdir,
named `<pack>-<subdir>`.

## comfy-env.toml

**`comfy-env.toml`** is open schema.
comfy-env translates this .toml file into a pixi.toml config file format.
Every key in `comfy-env.toml` falls into one of four buckets:

- **Ours**: comfy-env consumes it and emits something else. Exactly four keys.
- **Passthrough**: copied into the generated `pixi.toml` untouched.
- **Rewritten**: passed through, but not unchanged. Only the torch family.
- **Refused**: a hard error, because comfy-env generates it.

| Key(s) | Bucket | Fate |
|---|---|---|
| `python` | ours | the env's interpreter pin (quoted string, 3.10 minimum) |
| `[cuda]` | ours | packages resolved to prebuilt wheel URLs at install time |
| `[env_vars]` | ours | env vars on this env's workers and scans -- never reaches pixi |
| `[options]` | ours | runtime knobs -- never reaches pixi. Exactly one exists today: `health_check_timeout` (seconds, per-env worker ping timeout, default 5.0); `call_timeout` is planned ([ADR-0018](adr/0018-worker-call-timeout.md)) |
| `[dependencies]`, `[pypi-dependencies]`, `[target.*]`, `[activation]`, `[tasks]`, `[pypi-options]`, `[system-requirements]`, `[workspace]` | passthrough | forwarded verbatim into the generated `pixi.toml` (`[activation]` and `workspace.channels` are *merged* with comfy-env's own entries) |
| `torch` / `torchvision` / `torchaudio` pins | rewritten | stripped and replaced with the workspace-wide pin, with a log line |
| `[environments]`, `[feature.*]` | refused | compiler-owned: the manifest is single-feature/single-environment by design |
| `workspace.name` / `.version` / `.platforms` | refused | compiler-owned: env identity and host-derived platforms |
| `[node_packs]`, `[types]` | refused | root-file sections -- the two files do not share a vocabulary |
| `[serializers]` | refused | removed 0.4.16; declare `[types]` in the root file instead |

### The 4 kinds of keys in comfy-env.toml

#### 1. Ours — translated, never forwarded

**This bucket is exactly four keys**, and the list is closed: `parse_config`
consumes `python`, `[cuda]`, `[env_vars]` and `[options]`, and anything it does
not consume is passthrough by definition.

They do not exist in pixi at all. Pixi 0.75.0 accepts exactly seventeen
top-level tables:

```
workspace, package, target, dependencies, host-dependencies,
build-dependencies, constraints, exclude-newer, pypi-dependencies,
pypi-exclude-newer, dev, activation, tasks, feature, environments,
pypi-options, system-requirements
```
and rejects anything else by name.

So `[cuda]` in a manifest handed straight to pixi is a hard error
(`'cuda' was not expected here`). comfy-env consumes these itself and emits
something pixi understands.

| You write | What it becomes | When |
|---|---|---|
| `python = "3.11"` | `[feature.<env>.dependencies] python = "3.11.*"` | build |
| `[cuda] packages` | resolved wheel URLs, installed after pixi (see [cuda-wheels](../cuda-wheels/index.md)) | build |
| `[env_vars]` | environment variables set on the worker process at spawn | run |
| `[options]` | the one runtime knob: `health_check_timeout` (seconds, default 5.0) | run |

The bottom two never reach `pixi.toml` in any form. `[env_vars]` in particular
is **not** `[activation.env]`: it is applied when comfy-env spawns the metadata
scan and the persistent worker, so it affects those processes and nothing else.

Careful with `cuda`: it is not a pixi *table*, but it **is** a valid key
*inside* `[system-requirements]`. Different thing, and comfy-env sets that one
itself from the host.

#### 2. Passthrough

Most native pixi keys like `[tasks]`, `[activation]`, `[pypi-options]`,
`[system-requirements]`, `[target.*]`, `[dependencies]`... are copied into the
generated `pixi.toml` at **feature** level. comfy-env does not validate it;
pixi does, because pixi is pinned ([ADR-0002](adr/0002-pixi-as-environment-manager.md)).

Use whatever pixi magic you like.

#### 3. Rewritten on the way through

Forwarded, but not unchanged: **torch-family pins**. `torch`, `torchvision`
and `torchaudio` in `[dependencies]` or `[pypi-dependencies]` are stripped and
replaced with the workspace-wide pin, and you get a log line saying so.
The parent and every worker ideally share one identical torch for compatibility and disk space reasons.

#### 4. Refused

Setting these is a hard error, because using them in comfy-env would not be appropriate:

| Key | Why |
|---|---|
| `[environments]`, `[feature.*]` | the per-env manifest is single-feature / single-environment by design ([ADR-0007](adr/0007-machine-wide-workspace-with-per-env-manifests.md)) |
| `workspace.name`, `workspace.version` | env identity |
| `workspace.platforms` | derived from the host machine |

## comfy-env-root.toml

**`comfy-env-root.toml`** is closed schema: it can only have two sections and nothing
else. Any other top-level table is a hard parse error.

| Section | What it is |
|---|---|
| `[node_packs]` | Peer node packs to install: git-ref-pinned table form
| `[types]` | Wire types this pack puts on sockets: `"builtin"` or `"custom"`

Example:

```toml
[node_packs]
OtherPack = { github = "https://github.com/x/OtherPack", tag = "v1.2.0" }
ComfyUI-GeometryPack = "https://github.com/PozzettiAndrea/ComfyUI-GeometryPack"

[types]
TRIMESH    = "custom"
SKELETON   = "builtin"
INTRINSICS = "builtin"
```

Notes:

- `install()` consumes `[node_packs]`
- `register_nodes()` validates and loads `[types]` (see [custom wire types](serializers.md))

### `[node_packs]`
We can declare node packs to install together with our main one in various ways, both from the registry and from github.
After cloning/downloading, the peer's own `requirements.txt` is
pip-installed and its `install.py` run (the standard ComfyUI install flow)

| Spelling | Example | What happens |
|---|---|---|
| string shorthand | `Pack = "owner/Pack"` | `owner/repo` is normalized to `https://github.com/owner/repo`; full URLs pass through. Shallow clone of the default branch -- **unpinned, deprecated by ADR-0016** |
| `github` + `tag` | `Pack = { github = "owner/Pack", tag = "v1.2.0" }` | `git clone --depth 1 --branch v1.2.0` -- **the required shape per ADR-0016** |
| `github` + `branch` | `{ github = ..., branch = "dev" }` | shallow clone of that branch (moving ref -- unreproducible) |
| `github` + `commit` | `{ github = ..., commit = "abc1234..." }` | full clone + `git checkout <sha>` (arbitrary commits cannot be shallow-cloned) |
| `registry` | `{ registry = "pack-id" }` | zip download via `api.comfy.org/nodes/<id>/install` (latest version) |
| `registry` + `version` | `{ registry = "pack-id", version = "1.2.0" }` | same endpoint, pinned version |

`repo` is accepted as an alias for `github`. `tag`/`branch`/`commit` are
mutually exclusive in effect (tag wins over branch; commit only consulted
when neither is set).

!!! tip "One pack can mix modes"
    Isolation is per-directory, not per-pack: `nodes/main/` with no config
    imports in-process like any vanilla pack, while `nodes/cgal/` with a
    `comfy-env.toml` gets its own env. Put the exotic dependencies behind a
    config and leave the lightweight nodes on the host runtime.