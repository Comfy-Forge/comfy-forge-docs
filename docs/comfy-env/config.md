# Config reference: the two TOML files

Two files, two roles ([ADR-0003](adr/0003-two-config-files-with-two-roles.md)):
`comfy-env-root.toml` at the pack root manages node dependencies and
pack-wide switches and **never touches the Python environment**;
`comfy-env.toml` at `nodes/` or `nodes/<subdir>` gives that directory its
own isolated pixi environment -- the file's *presence* is the isolation
switch. (Those two locations are the only ones supported: discovery and the
runtime binder deliberately match, so a config anywhere else is simply not
seen rather than silently materialized-but-unused.)

!!! warning "The host-environment principle"
    **comfy-env NEVER installs anything into the host environment.** The
    host env's only comfy-env-related content is `comfy-env` itself (the
    one line in your `requirements.txt`). CUDA wheels, conda packages, and
    pip dependencies all live in isolated envs -- there is no config key,
    and there will be no config key, that installs a library into ComfyUI's
    own environment.

Parsing lives in `config/__init__.py` (`parse_config`). One rule to know:
**honest passthrough**
([ADR-0013](adr/0013-env-file-passthrough-contract.md)) -- every table
comfy-env does not own is forwarded verbatim into the generated
`pixi.toml` at feature level (`[tasks]`, `[activation]` -- merged with the
compiler's entries -- future pixi tables, everything), and the *pinned*
pixi validates its own language at install. The exceptions are the
compiler-owned keys, which error loudly if you set them:
`[environments]`, `[feature.*]`, `workspace.name/version/platforms`
(host-derived identity); torch-family pins are rewritten to the host
family. Typos inside comfy-env's own sections (`[cuda]`, `[options]`,
`[settings]`) produce warnings; invalid `[types]` values are parse
errors.

## `comfy-env-root.toml` (pack root)

```toml
# ComfyUI-MyPack/comfy-env-root.toml

# Other ComfyUI node packs this pack depends on. Installed by install():
# cloned from GitHub (git, zip fallback) or downloaded from the Comfy
# Registry into custom_nodes/, then their requirements.txt + install.py run.
[node_reqs]
# table form with an exact git ref -- the required shape per ADR-0016
OtherPack = { github = "https://github.com/x/OtherPack", tag = "v1.2.0" }
# short form (unpinned) and registry entries are deprecated by ADR-0016
# (unpinned = unreproducible; registry versions are mutable/unsigned) --
# still accepted by 0.4.x code, rejected once 0016 enforcement lands.
ComfyUI-GeometryPack = "https://github.com/PozzettiAndrea/ComfyUI-GeometryPack"

# Per-pack overrides of comfy-env feature flags (short keys map to
# COMFY_ENV_* env vars; a pack's [settings] wins over env vars --
# most specific wins; see the settings reference).
[settings]
auto_install = false      # COMFY_ENV_AUTO_INSTALL (default false)
pool_ipc = false          # COMFY_ENV_POOL_IPC     (default false)

# Wire types this pack puts on node sockets (ADR-0015).
# "builtin" = automatic transport (tensors/arrays/dicts), listed for
# humans and tests; "custom" = serialize/deserialize functions in
# ./serialization.py at the pack root. Validated at register_nodes():
# a "custom" socket with no registration is a loud startup error.
[types]
TRIMESH    = "custom"
SKELETON   = "builtin"
INTRINSICS = "builtin"

# NOTE: this file is a CLOSED schema. [node_reqs], [settings] and [types]
# are the only sections accepted; anything else -- [dependencies], [cuda],
# [env_vars], a legacy [apt], or a typo'd section name -- is a hard parse
# error, not a silently-ignored no-op. Installing packages from the root
# file would violate the host-environment principle above, so anything
# installable belongs in a subdirectory comfy-env.toml.
```

Notes:

- `[node_reqs]`, `[settings]`, and `[types]` are the load-bearing
  sections: `install()` consumes the first, both `install()` and
  `register_nodes()` consult the second, and `register_nodes()`
  validates and loads the third (see
  [custom wire types](serializers.md)).

### `[node_reqs]` -- every spelling the code accepts

Two sources, checked in this order per entry (`packages/node_dependencies.py`):
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
- The root file's schema is **closed**: `[node_reqs]`, `[settings]` and
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

# Interpreter for the env (defaults to the host's python)
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

# NOTE: [settings], [node_reqs], and [types] are ROOT-file sections and
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
- `[cuda]` packages are **not** written into `pixi.toml` -- they install in
  a post-pixi `uv pip install --no-deps` pass (pixi cannot express no-deps;
  see [install()](install.md)). They also define the package list for the
  [accelerator import rule](accelerators.md).
- One pack can mix modes: `nodes/main/` with no config imports in-process,
  `nodes/cgal/` with a config gets its own env.
- Minimal useful file: an **empty** `comfy-env.toml` already gives the
  directory its own interpreter and env; add sections as needed.

## Real-world examples

- [ComfyUI-TRELLIS2](https://github.com/PozzettiAndrea/ComfyUI-TRELLIS2) --
  root file only today (node deps + env vars). Its CUDA packages are moving
  into an isolated env: as a principle, comfy-env never installs anything
  into the host environment
  ([ADR-0003](adr/0003-two-config-files-with-two-roles.md)).
- [ComfyUI-GeometryPack](https://github.com/PozzettiAndrea/ComfyUI-GeometryPack)
  -- both files; the heavyweight conda + CUDA isolation env.
- [cookiecutter-comfy-extension](https://github.com/PozzettiAndrea/cookiecutter-comfy-extension)
  -- scaffold with the minimal template.
