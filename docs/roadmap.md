# Roadmap / TODO

The standing work list, distilled from the 2026-08 reviews (three-reviewer
docs audit, the ADR-0002 adversarial panel, and the fleet conda survey).
Items link to the ADR or page that carries their full rationale. Done items
stay listed (struck) so the list doubles as a change record.

## cuda-wheels

1. **Requires-Dist curation** -- per-package `requires_dist_overrides`
   applied in the existing METADATA rewrite: strip build-tool leakage
   (spconv), pin sibling packages to exact farm builds, keep true runtime
   deps (gsplat's `ninja` is real: runtime JIT). Unblocks lockfile-visible
   inlining in comfy-env.
   ([CW-ADR-0004](cuda-wheels/adr/0004-combo-encoded-versions-and-metadata-patching.md))
2. **Paginate `generate_index.py`'s Releases API call** -- at release #31
   packages silently vanish from the index (currently ~27; a dated time
   bomb), and assert the generated index never loses packages vs the
   previous deploy.
3. **Version-aware rebuild skip** -- `generate_matrix.wheel_exists` matches
   combo tags with no version component, so version bumps rebuild nothing
   without `--overwrite`.
4. **Mutable `-latest` release hygiene** -- old-version wheels accumulate
   and the index orders them first (a live `cumesh-vb` 0.0.1-over-1.0
   case); delete superseded assets on publish.
   ([CW-ADR-0002](cuda-wheels/adr/0002-rolling-releases-as-wheel-storage.md))
5. **`packages.json` manifest** published with the index (name, combo, url,
   sha256) so consumers stop regex-scraping HTML and gain hash
   verification; dissolves the hand-synced torch-family tables.

## comfy-env

1. **Revive the inlining path** once curated wheels land -- URL
   pypi-dependencies in generated manifests, side-channel + `--no-cache`
   retired.
   ([The two-system problem](comfy-env/two-system-problem.md))
2. **Finish the orphaned system-env path** -- `_collect_root_conda_deps`
   is defined and never called; wire it as the shared GL/ffmpeg runtime
   layer.
3. **libomp dedupe beyond macOS; stop blanket `KMP_DUPLICATE_LIB_OK`** --
   the enforcement arm of the lineage-coherence principle
   ([ADR-0002](comfy-env/adr/0002-pixi-as-environment-manager.md)).
4. ~~Housekeeping: `[apt]`/`[brew]` removed (pre-pixi legacy);
   `workspace.py` docstring now describes reality; dead
   `_read_env_torch_version` deleted outright~~ -- done.
5. *Deferred by design*: uv-first materialization for envs with zero conda
   content; CI-pre-solved `pixi.lock` per env x ABI-tag for the ComfyUI
   Desktop population; py-rattler (watch item).
6. ~~Pin the pixi binary (version + sha256, version marker)~~ -- done.
7. ~~Canary transport handshake~~ -- done
   ([ADR-0005](comfy-env/adr/0005-tiered-tensor-serialization.md)).

## comfy-test

1. **Consume ACCELERATOR declarations** -- CPU lanes skip tagged nodes
   honestly (no empty-module mocks for declared packs); derive the
   cpu/cuda workflow splits from workflow content with the
   dispatcher-ambiguity rule and manual override
   ([accelerator declarations](comfy-env/accelerators.md)).
2. **CI reproducibility** -- pin the comfy-test version test jobs install
   (no `--upgrade` races); derive the random Python pick from `run_id` so
   re-runs reproduce; replace the gh-pages force-push with
   fetch-rebase-retry.
3. **comfy-env contract seam** -- stop importing `_abi_tag` / hardcoding
   the env layout; consume a stable `comfy-env info --json` instead.

## Upstream watch items

- **pixi PR [#5464](https://github.com/prefix-dev/pixi/pull/5464)**
  (per-dependency `no-deps`) -- would let manifests carry the wheels with
  zero farm changes; stalled, worth a nudge.
- **conda-forge pytorch coverage** -- feedstock is current (2.13, CUDA on
  Linux + Windows); the gate for conda-native publishing is our
  dependent-package matrix plus the build-lineage caveat for zero-copy.
  ([The two-system problem](comfy-env/two-system-problem.md))
