# ADR-0003: Two config files, two roles

**Status:** accepted

## Context

Node packs have two very different needs that a single config file kept
conflating: (a) declaring dependencies on *other node packs* and per-pack
runtime configuration without touching any Python environment, and (b)
requesting a fully isolated Python environment. Some packs only need (a);
packs like ComfyUI-GeometryPack also need a whole conda stack (CGAL, `bpy`,
pyvista) plus CUDA wheels that cannot live in the host venv.

Underlying principle, stated explicitly: **comfy-env never installs anything
into the host environment.** The host env's only comfy-env-related content
is comfy-env itself (`pip install comfy-env`, via the pack's
`requirements.txt`). CUDA wheels, conda packages, and pip dependencies all
belong in isolated envs; a pack's own `requirements.txt` should converge to
exactly `comfy-env`. Remaining host-env stragglers in existing packs (e.g.
`trimesh`, `comfy-3d-viewers`) are slated for removal, not accommodation.

## Decision

Two files with sharply separated roles:

- **`comfy-env-root.toml`** (pack root): system packages (apt/brew) and
  `[node_reqs]` dependencies on other ComfyUI node packs. **Never touches the
  Python environment** -- PyPI deps stay in `requirements.txt`, per ComfyUI
  convention.
- **`comfy-env.toml`** (any subdirectory): the subdirectory gets its **own
  isolated Python environment** via pixi -- separate interpreter, conda
  packages, pip packages, and prebuilt CUDA wheels. Env name:
  `<plugin>-<subdir>`, `ComfyUI-` prefix stripped, lowercased
  (`environment/cache.py:get_env_name`).

Parsing (`config/__init__.py`) treats unknown TOML keys as **passthrough in
intent**: they are collected rather than rejected. In the v0.4
implementation, however, the manifest generator copies only an **allowlist**
into the per-env `pixi.toml` -- `[dependencies]`, `[pypi-dependencies]`,
`[target.*]`, `[pypi-options]`, `[system-requirements]`, and
`workspace.channels` (`toml_generator.py:263-310, 630`). Anything else --
`[tasks]`, `[activation]` (the generator emits its own), any typo'd table --
is **silently dropped**. That gap between intent and implementation is a
known defect: the repair direction is honest passthrough with a short
deny-list of compiler-owned keys, plus loud warnings for every dropped key.
The `[cuda]` section triggers wheel resolution
([ADR-0004](0004-prebuilt-cuda-wheel-index.md)); `[settings]` allows
per-node overrides of feature flags via `SETTINGS_KEY_MAP`.

## Consequences

- The lightest integration (root file only) adds node-dependency management
  and per-pack settings with zero isolation machinery -- never package
  installation into the host env. (A root-file `[cuda]` section has no
  consumer in v0.4 and would violate the principle above; it is
  reserved-to-delete, not reserved-to-implement.)
- Presence of `comfy-env.toml` *is* the isolation switch -- no separate flag
  to keep in sync.
- One pack can mix modes: `nodes/main/` imported in-process, `nodes/cgal/`
  isolated.
- The passthrough *intent* means comfy-env's schema never needs to chase
  pixi's feature set -- but until the allowlist gap above is repaired, keys
  outside the allowlist do NOT reach pixi, and typos anywhere (inside owned
  sections or out) are silently swallowed rather than caught.
