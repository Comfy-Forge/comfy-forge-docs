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
`[settings]`, `[serializers]`) produce warnings. An optional `schema = 1`
key versions the format.

## `comfy-env-root.toml` (pack root)

```toml
# ComfyUI-MyPack/comfy-env-root.toml

# Other ComfyUI node packs this pack depends on. Installed by install():
# cloned from GitHub (git, zip fallback) or downloaded from the Comfy
# Registry into custom_nodes/, then their requirements.txt + install.py run.
[node_reqs]
# short form: name = github URL
ComfyUI-GeometryPack = "https://github.com/PozzettiAndrea/ComfyUI-GeometryPack"
# table form: pin a tag/branch/commit, or install from the Comfy Registry
OtherPack = { github = "https://github.com/x/OtherPack", tag = "v1.2.0" }
RegistryPack = { registry = "registry-pack-id", version = "1.0.3" }

# Per-pack overrides of comfy-env feature flags (short keys map to
# COMFY_ENV_* env vars; a pack's [settings] wins over env vars --
# most specific wins; see the settings reference).
[settings]
isolate = true            # COMFY_ENV_ISOLATE      (default true)
install_isolated = true   # COMFY_ENV_INSTALL_ISOLATED (default true)
auto_install = false      # COMFY_ENV_AUTO_INSTALL (default false)
pool_ipc = false          # COMFY_ENV_POOL_IPC     (default false)
worker_vram_budget = 0    # COMFY_ENV_WORKER_VRAM_BUDGET (GB, 0 = auto)

# NOTE: do NOT declare [dependencies] / [cuda] here. They are parsed but
# have no consumer at ROOT scope in v0.4 -- and installing packages from
# the root file would violate the host-environment principle above.
# Anything installable belongs in a subdirectory comfy-env.toml.
```

Notes:

- `[node_reqs]` and `[settings]` are the load-bearing sections: `install()`
  consumes the first, both `install()` and `register_nodes()` consult the
  second.
- `[apt]` / `[brew]` **no longer exist** (removed 2026-08): they predate
  the realization that everything they would deliver installs through
  pixi/conda-forge -- declare the equivalent conda package under a
  subdirectory's `[dependencies]` instead (e.g. `mesalib` replaces apt
  `libgl1-mesa-glx`). Unknown tables are harmless: the manifest generator
  only copies allowlisted keys.
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

# --- everything below is pixi passthrough (v0.4 caveat: only the
# ---  allowlisted tables shown here actually reach pixi.toml; see intro) ---

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

# Custom wire types: modules imported on BOTH sides for their
# register_serializer() side effects (ADR-0014). Payloads decompose into
# schema + shared-memory tensors -- never pickle; a side that cannot
# import the module passes the type through opaquely.
[serializers]
modules = ["my_pack.wire_types"]

# NOTE: [settings] and [node_reqs] are ROOT-file sections and are REJECTED
# here -- in an env file they never had any effect.
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
