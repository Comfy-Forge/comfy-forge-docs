# ADR-0003: Two config files, two roles

**Status:** accepted

## Context

Node packs have two very different needs that a single config file kept
conflating: (a) declaring system packages and dependencies on *other node
packs* without touching the Python environment at all, and (b) requesting a
fully isolated Python environment. Packs like ComfyUI-TRELLIS2 only need CUDA
wheel resolution in the host env; packs like ComfyUI-GeometryPack need a whole
conda stack (CGAL, `bpy`, pyvista) that cannot live in the host venv.

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

Parsing (`config/__init__.py`) treats unknown TOML keys as **passthrough**,
not errors: they flow into the generated `pixi.toml` verbatim. The `[cuda]`
section triggers wheel resolution ([ADR-0004](0004-prebuilt-cuda-wheel-index.md));
`[settings]` allows per-node overrides of feature flags via `SETTINGS_KEY_MAP`.

## Consequences

- The lightest integration (root file only) adds CUDA wheel resolution and
  node-dependency management with zero isolation machinery.
- Presence of `comfy-env.toml` *is* the isolation switch -- no separate flag
  to keep in sync.
- One pack can mix modes: `nodes/main/` imported in-process, `nodes/cgal/`
  isolated.
- The passthrough rule means comfy-env's schema never needs to chase pixi's
  feature set; the flip side is that typos in known keys are not caught.
