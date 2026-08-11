# ADR-0003: Two config files, two roles

**Status:** accepted; adversarially reviewed 2026-08 (two independent
reviewers + debate) -- verdict **sound-with-repairs**. Known defects and the
agreed repair list are recorded below; the factoring itself stands.

## Decision

Two files with sharply separated roles:

- **`comfy-env-root.toml`** (pack root): `[node_reqs]` dependencies on
  other ComfyUI node packs, plus pack-level `[settings]`. Nothing else --
  a root `[env_vars]` has no effect (worker env vars belong in the
  subdirectory `comfy-env.toml`) and `install()` warns if one is present.
  **Never touches the Python environment** -- PyPI deps stay in
  `requirements.txt`, per ComfyUI convention. (Early versions also planned
  `[apt]`/`[brew]` system packages; that idea predates realizing everything
  those would deliver installs through pixi/conda-forge -- the keys were
  removed in the 2026-08 cleanup.)
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

## 2026-08 review: known defects and agreed repairs

What the review confirmed about the design: the split carves at a real
joint (root = shared-state mutation, env file = hermetic env definition);
dependency-locality is the payoff; presence-as-switch is a good trade given
graceful degradation (ADR-0008). What it found broken is **enforcement** --
the roles are enforced by filename convention only, and several documented
behaviors are hoped, not checked:

- **Allowlist-not-passthrough** (see Decision above) -- the headline gap.
- **Env-name collisions cause silent rebuild thrash**: identity derives from
  folder names using only the last path segment; duplicate names silently
  overwrite each other's install hashes and share one env dir, producing a
  permanent multi-GB reinstall loop with no diagnostic.
- **Discovery/binding asymmetry**: install materializes any discovered
  `comfy-env.toml` (recursive glob, no fences); runtime binds only `nodes/`
  or `nodes/<subdir>`. Configs elsewhere (vendored trees, deeper nesting)
  become multi-GB envs that are never used, silently.
- **`python = 3.10` unquoted** is a TOML float and silently becomes `"3.1"`.
- **Cache identity is wrong in both directions**: the install hash covers
  raw config bytes (comment edits force rebuilds) but not derivation results
  (a fallback-combo env never upgrades when the missing wheel is later
  published; GPU-presence flips do not move the hash), and `auto_install`
  uses a different torch pin rule and writes no hash at all.
- **Settings precedence is inverted** vs. its own documentation: a pack's
  `[settings]` beats the user's explicit `COMFY_ENV_*` env var.
- ~~pixi is bootstrapped unpinned from `releases/latest`~~ -- fixed
  2026-08: pinned version, sha256-verified, comfy-env-owned path
  ([ADR-0002](0002-pixi-as-environment-manager.md)).
- Dead config (note: `[apt]`/`[brew]` were removed outright in the 2026-08
  cleanup -- pre-pixi legacy): root `[dependencies]` (consumer has zero
  callers), subdir `[node_reqs]` and subdir `[settings]` (parsed, never
  consumed); one runtime consumer re-parses the TOML directly instead of
  using the config layer.

**Agreed repair order** (all backward-compatible; no format change):
pin + checksum the pixi bootstrap; loud errors on duplicate env names and
warnings on unbindable configs; validation batch (warn on every dropped or
unrecognized key, reject float `python`, add `schema = 1`); single parser +
dead-plumbing deletion; settings-precedence fix (user env var wins); hash
the *generated* manifest instead of input bytes and unify `auto_install`
with the install path.

## Considered alternatives (2026-08)

- **pyproject.toml for everything** -- rejected. The env config is compiled
  into a generated `pixi.toml` regardless of source filename, so moving it
  buys a misleading `pixi shell` dev loop (missing compiler-injected torch
  pins and CUDA wheels) at real migration cost. Claims that a nested
  pyproject confuses ruff/uv were checked and are largely folklore; the
  decision does not rest on them.
- **pyproject `[tool.comfy-env]` for the root role only** -- rejected for
  now. The separate file's *presence* is load-bearing signal: free
  "uses comfy-env" detection (`test -f`, `ls`), a clean ecosystem adoption
  metric via filename search, and decoupling from Registry-metadata merge
  traffic. Its lack of validation is closable with a `doctor` check.
- **Env identity from pyproject `[project].name`** -- superseded by a
  simpler idea: an optional declared `name` key in comfy-env's own files.
  Declared identity beats path-derived identity, and a name inside
  `comfy-env.toml` survives both pack-folder renames (zip `-main` suffixes,
  fork clones) and subdir renames -- which `[project].name` cannot cover.
  Rename-orphaning is rare in practice (`comfy-env gc` recovers the disk),
  so this is a polish item; the duplicate-name error above is the part that
  matters.
- **Single root file with path-keyed env sections / raw per-dir pixi.toml
  with no compiler / one workspace manifest with per-env features** -- each
  rejected: respectively blast-radius concentration (the v0.3 failure
  ADR-0007 fixed), loss of host-side torch/ABI coordination (the compiler's
  irreducible job), and literally the v0.3 design.
- **Long-term direction** (not scheduled): split config by churn rate and
  blast radius -- a small versioned root file carrying declared identity,
  env scopes, and placement policy, plus a real per-dir `pixi.toml` as the
  dependency manifest, with the compiler reduced to a pin-injector that
  logs every key it overrides, and CI-published per-platform `pixi.lock`
  files as the reproducibility contract.
