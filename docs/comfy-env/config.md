# Config reference: the two TOML files

## Files

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

## Every key at a glance

**`comfy-env-root.toml`** -- closed schema: these two sections and nothing
else. Any other top-level table is a hard parse error.

| Section | What it is |
|---|---|
| `[node_packs]` | Peer node packs to install -- git-ref-pinned table form per ADR-0016 |
| `[types]` | Wire types this pack puts on sockets: `"builtin"` or `"custom"` |

**`comfy-env.toml`** -- open schema, four buckets. Anything not listed as
ours/rewritten/refused is passthrough by definition.

| Key(s) | Bucket | Fate |
|---|---|---|
| `python` | ours | the env's interpreter pin (quoted string, 3.10 minimum) |
| `[cuda]` | ours | packages resolved to prebuilt wheel URLs at install time |
| `[env_vars]` | ours | env vars on this env's workers and scans -- never reaches pixi |
| `[options]` | ours | runtime knobs (`health_check_timeout`) -- never reaches pixi |
| `[dependencies]`, `[pypi-dependencies]`, `[target.*]`, `[activation]`, `[tasks]`, `[pypi-options]`, `[system-requirements]`, `[workspace]` | passthrough | forwarded verbatim into the generated `pixi.toml` (`[activation]` and `workspace.channels` are *merged* with comfy-env's own entries) |
| `torch` / `torchvision` / `torchaudio` pins | rewritten | stripped and replaced with the workspace-wide pin, with a log line |
| `[environments]`, `[feature.*]` | refused | compiler-owned: the manifest is single-feature/single-environment by design |
| `workspace.name` / `.version` / `.platforms` | refused | compiler-owned: env identity and host-derived platforms |
| `[node_packs]`, `[types]` | refused | root-file sections -- the two files do not share a vocabulary |
| `[serializers]` | refused | removed 0.4.16; declare `[types]` in the root file instead |

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

## Parsing

Parsing lives in `config/__init__.py` (`parse_config`); the compile step that
consumes the result is `packages/toml_generator.py`. The governing rule is
**honest passthrough**
([ADR-0013](adr/0013-env-file-passthrough-contract.md)): comfy-env keeps as
little knowledge of pixi's language as it can get away with, and forwards the
rest untouched.

Every key in `comfy-env.toml` falls into one of four buckets:

- **Ours** — comfy-env consumes it and emits something else. Exactly four keys.
- **Passthrough** — copied into the generated `pixi.toml` untouched.
- **Rewritten** — passed through, but not unchanged. Only the torch family.
- **Refused** — a hard error, because comfy-env generates it.

### 1. Ours — translated, never forwarded

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
something pixi does understand:

| You write | What it becomes | When |
|---|---|---|
| `python = "3.11"` | `[feature.<env>.dependencies] python = "3.11.*"` | build |
| `[cuda] packages` | resolved wheel URLs, installed after pixi (see [cuda-wheels](../cuda-wheels/index.md)) | build |
| `[env_vars]` | environment variables set on the worker process at spawn | run |
| `[options]` | runtime knobs (`health_check_timeout`) | run |

The bottom two never reach `pixi.toml` in any form. `[env_vars]` in particular
is **not** `[activation.env]`: it is applied when comfy-env spawns the metadata
scan and the persistent worker, so it affects those processes and nothing else.

Careful with `cuda`: it is not a pixi *table*, but it **is** a valid key
*inside* `[system-requirements]`. Different thing, and comfy-env sets that one
itself from the host.

The **root** file owns two more, `[node_packs]` and `[types]`, and the two
files do not share a vocabulary: a root-only section in an env file is a hard
error, and so is the reverse. `[serializers]` is refused by name -- it became
`[types]` in 0.4.16 ([ADR-0015](adr/0015-declared-wire-types.md)).

### 2. Passthrough -- forwarded verbatim

Everything else — `[tasks]`, `[activation]`, `[pypi-options]`,
`[system-requirements]`, `[target.*]`, `[dependencies]` — is copied into the
generated `pixi.toml` at **feature** level. comfy-env does not validate it;
pixi does, because pixi is pinned ([ADR-0002](adr/0002-pixi-as-environment-manager.md))
and its parser is the only authority worth trusting. Use whatever pixi magic
you like.

Two of these are merged rather than copied, so comfy-env's own entries do not
clobber yours: `[activation]` (your `env` entries survive alongside
`KMP_DUPLICATE_LIB_OK`), and `workspace.channels` (unioned).

### 3. Rewritten on the way through

Forwarded, but not unchanged: **torch-family pins**. `torch`, `torchvision`
and `torchaudio` in `[dependencies]` or `[pypi-dependencies]` are stripped and
replaced with the workspace-wide pin, and you get a log line saying so. The
parent and every worker must share one identical torch — tensors cross the
process boundary over torch's private multiprocessing ABI, which has no
version handshake ([ADR-0001](adr/0001-process-isolation-via-persistent-subprocess-workers.md)).

### 4. Refused -- hard error

Setting these is a hard error, because comfy-env generates them and a second
author would break the manifest's shape:

| Key | Why |
|---|---|
| `[environments]`, `[feature.*]` | the per-env manifest is single-feature / single-environment by design ([ADR-0007](adr/0007-machine-wide-workspace-with-per-env-manifests.md)) |
| `workspace.name`, `workspace.version` | env identity |
| `workspace.platforms` | derived from the host machine |

Errors and warnings elsewhere: typos inside comfy-env's own sections
(`[cuda]`, `[options]`) warn and continue; an invalid `[types]` value is a
parse error; an unquoted `python = 3.10` is a parse error too, because TOML
reads it as the float `3.1`; and a `python` pinned below 3.10 is rejected --
comfy-env supports 3.10+ only, matching ComfyUI's own `requires-python`.

## `comfy-env-root.toml` (pack root)

```toml
# ComfyUI-MyPack/comfy-env-root.toml

# Other ComfyUI node packs this pack depends on. Installed by install():
# cloned from GitHub (git, zip fallback) or downloaded from the Comfy
# Registry into custom_nodes/, then their requirements.txt + install.py run.
[node_packs]
# table form with an exact git ref -- the required shape per ADR-0016
OtherPack = { github = "https://github.com/x/OtherPack", tag = "v1.2.0" }
# short form (unpinned) and registry entries are deprecated by ADR-0016
# (unpinned = unreproducible; registry versions are mutable/unsigned) --
# still accepted by 0.4.x code, rejected once 0016 enforcement lands.
ComfyUI-GeometryPack = "https://github.com/PozzettiAndrea/ComfyUI-GeometryPack"

# Wire types this pack puts on node sockets (ADR-0015).
# "builtin" = automatic transport (tensors/arrays/dicts), listed for
# humans and tests; "custom" = serialize/deserialize functions in
# ./serialization.py at the pack root. Validated at register_nodes():
# a "custom" socket with no registration is a loud startup error.
[types]
TRIMESH    = "custom"
SKELETON   = "builtin"
INTRINSICS = "builtin"

# NOTE: this file is a CLOSED schema. [node_packs] and [types] are the
# only sections accepted; anything else -- [dependencies], [cuda],
# [env_vars], a removed [settings], or a typo'd section name -- is a hard
# parse error, not a silently-ignored no-op. Installing packages from the root
# file would violate the host-environment principle above, so anything
# installable belongs in a subdirectory comfy-env.toml.
```

Notes:

- `[node_packs]` and `[types]` are the load-bearing sections:
  `install()` consumes the first; `register_nodes()` validates and
  loads the second (see [custom wire types](serializers.md)). Settings
  are machine-global env vars ([settings reference](settings.md)) --
  the per-pack `[settings]` section was removed in 0.4.25.

### `[node_packs]` -- every spelling the code accepts

Two sources, checked in this order per entry (`packages/node_packs.py`):
**registry first, then github**. An entry with neither logs a warning and is
skipped. After cloning/downloading, the peer's own `requirements.txt` is
pip-installed and its `install.py` run -- the standard ComfyUI install flow.

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
when neither is set). Registry entries and unpinned github entries are
**deprecated by ADR-0016** (unpinned = unreproducible; registry versions are
mutable and unsigned) -- still accepted by 0.4.x, rejected once enforcement
lands.
- `[apt]` / `[brew]` **no longer exist** (removed 2026-08): they predate
  the realization that everything they would deliver installs through
  pixi/conda-forge -- declare the equivalent conda package under a
  subdirectory's `[dependencies]` instead (e.g. `mesalib` replaces apt
  `libgl1-mesa-glx`). A leftover `[apt]` or `[brew]` table in a root file
  is now a **parse error**, not a silent no-op -- delete it.
- The root file's schema is **closed**: `[node_packs]` and
  `[types]` are the only accepted sections, and any other top-level table
  raises at load time (`config/__init__.py`, `ROOT_ALLOWED_SECTIONS`).
  This is deliberate -- a no-op `[env_vars]` shipped in the flagship pack
  for months before the schema was closed.
- The root file never creates an isolation env; workspace discovery looks
  only for `comfy-env.toml` files.

## `comfy-env.toml` (`nodes/` or `nodes/<subdir>`)

The real-world reference is
[GeometryPack's `nodes/comfy-env.toml`](https://github.com/PozzettiAndrea/ComfyUI-GeometryPack)
-- one env serving 161 nodes. Condensed and annotated:

```toml
# ComfyUI-MyPack/nodes/comfy-env.toml
# Presence of this file = this directory runs in its own isolated env,
# named <plugin>-<subdir> ("mypack-nodes"), materialized machine-wide.

# Interpreter for the env (defaults to the host's python; 3.10 minimum)
python = "3.11"

# CUDA-compiled packages, resolved from the cuda-wheels index at install
# time for this machine's exact (cuda, torch, python) combo. Skipped
# entirely on machines with no NVIDIA GPU.
[cuda]
packages = ["cumesh", "faithc-aot"]

# --- everything below is pixi passthrough: forwarded verbatim into the
# ---  generated pixi.toml, whether or not it appears in this example.
# ---  Only the compiler-owned keys are refused; see the intro. ---

# Conda packages (this is WHY pixi: these do not exist on PyPI)
[dependencies]
cgal = "*"
igl = "*"
trimesh = "*"
bpy = { version = "*", channel = "pozzettiandrea" }

# Extra conda channels
[workspace]
channels = ["conda-forge", "pozzettiandrea"]

# PyPI packages (installed by uv under pixi, same lockfile)
[pypi-dependencies]
pymeshfix = "*"
xatlas = "*"

# Platform-specific deps, pixi target syntax
[target.linux-64.dependencies]
mesalib = "*"
libglu = "*"

[target.win-64.pypi-dependencies]
msvc-runtime = "*"

# --- comfy-env-consumed sections ---

# Environment variables for this env's workers and metadata scans
[env_vars]
KMP_DUPLICATE_LIB_OK = "TRUE"

# Worker tuning
[options]
health_check_timeout = 5.0   # seconds; per-env worker ping timeout

# NOTE: [node_packs] and [types] are ROOT-file sections and
# are REJECTED here. [serializers] no longer exists anywhere (removed
# 0.4.16, hard error with a migration message): declare wire types in
# the root file's [types] and put custom serializers in
# <pack>/serialization.py (ADR-0015).
```

Notes:

- **Env naming**: `<plugin>-<subdir>`, `ComfyUI-`/`comfyui_` prefix
  stripped, lowercased, non-`[a-z0-9-]` collapsed to dashes
  (`comfyui-sam3/nodes` -> `sam3-nodes`). On disk the directory is
  additionally ABI-qualified (`sam3-nodes-py313-torch2-10-cu128`) so
  different stacks never share an env
  ([ADR-0007](adr/0007-machine-wide-workspace-with-per-env-manifests.md)).
- `[cuda]` packages resolve to wheel URLs at install time and are written
  into `pixi.toml` as direct-URL pypi-dependencies (0.4.31) -- inside
  `pixi.lock`, no second install system (see [install()](install.md)). They
  also define the package list for the
  [accelerator import rule](accelerators.md).
- One pack can mix modes: `nodes/main/` with no config imports in-process,
  `nodes/cgal/` with a config gets its own env.
- Minimal useful file: an **empty** `comfy-env.toml` already gives the
  directory its own interpreter and env; add sections as needed.