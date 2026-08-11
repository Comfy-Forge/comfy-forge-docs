# ADR-0003: Two config files, two roles

**Status:** accepted; adversarially reviewed 2026-08 (two independent
reviewers + debate) -- verdict **sound-with-repairs**. Known defects and the
agreed repair list are recorded below; the factoring itself stands.

## Decision

> **Two files, two roles: the pack root declares relationships, the
> subdirectory declares an environment.** Not one file doing both (the
> roles conflict -- root must never touch the Python environment); not a
> separate isolation flag -- the presence of `comfy-env.toml` IS the
> isolation switch.

Two files with sharply separated roles:

- **`comfy-env-root.toml`** (pack root): `[node_reqs]` dependencies on
  other ComfyUI node packs, plus pack-level `[settings]`. Nothing else:
  the root file has a **closed role schema** -- any other section (legacy
  keys, typos, env-file sections like `[env_vars]` or `[cuda]`) is rejected
  at parse time with a generic unsupported-section error. No backward
  compatibility for dead keys, by decision; no legacy key is named in code.
  **Never touches the Python environment** -- PyPI deps stay in
  `requirements.txt`, per ComfyUI convention. (Early versions also planned
  `[apt]`/`[brew]` system packages; that idea predates realizing everything
  those would deliver installs through pixi/conda-forge -- the keys were
  removed in the 2026-08 cleanup.)
- **`comfy-env.toml`** (`nodes/` or `nodes/<subdir>` -- the two shapes the
  runtime binder supports; discovery deliberately matches the binder
  exactly, fixed 2026-08): the subdirectory gets its **own
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

## 2026-08 review: remaining known defects

What the review confirmed about the design: the split carves at a real
joint (root = shared-state mutation, env file = hermetic env definition);
dependency-locality is the payoff; presence-as-switch is a good trade given
graceful degradation (ADR-0008). What it found broken was **enforcement**.
Most of the enforcement gaps have since been repaired (2026-08: pinned +
checksummed pixi, closed root role schema, duplicate-env-name hard errors,
discovery matched to the binder, string-only `python` pins, dead sections
and plumbing removed -- see git history for details). Still open:

- **Allowlist-not-passthrough** (see Decision above) -- the headline gap.
  Decided in [ADR-0013](0013-env-file-passthrough-contract.md) (honest
  passthrough; compiler-owned deny/rewrite/merge table; owned-section
  warnings; `schema = 1`); implementation pending.
- ~~Cache identity wrong in both directions~~ -- fixed 2026-08: rebuild
  decisions now key on the *derivation output* (canonical generated
  manifest + resolved wheel URLs), behind a pure-local fast key that keeps
  the all-clean path network-free. Comment edits never rebuild; envs
  stamped `:fallback` re-derive every run and self-upgrade when their
  missing wheel is published; GPU flips rebuild; `auto_install` shares the
  generator and pin rule and records the same identity. Legacy v1 hashes
  are grandfathered (no surprise rebuilds on upgrade).
- ~~Settings precedence inverted vs. its own documentation~~ -- resolved
  2026-08 **by documentation**: the code's behavior is intended -- a
  per-pack `[settings]` is *more specific* than a global env var and wins
  (specific beats general). The `settings.py` docstring and the docs were
  wrong, not the code; both now state the real order (pack `[settings]` >
  env var > `~/.comfy-env/settings.env`, which only fills unset vars >
  default).
- **Dead-config remainder** (root vs subdir matters here): the ROOT copies
  of `[node_reqs]` and `[settings]` are fully consumed (install plugin
  half; per-pack settings). The dead ones are the SUBDIR copies -- a
  `[node_reqs]` or `[settings]` inside `comfy-env.toml` is parsed but
  consumed by nothing. The config reference currently documents per-env
  `[settings]` as a feature, so this is an implement-or-un-document
  decision, not a deletion. One runtime consumer (`wrap.py`) still
  re-parses the TOML inline instead of using the config layer (deferred
  while that block carries in-flight serializer work).

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
