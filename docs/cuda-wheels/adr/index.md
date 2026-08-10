# cuda-wheels decision records

Nygard-style records for the
[cuda-wheels](https://github.com/PozzettiAndrea/cuda-wheels) wheel farm --
a separate series from the [comfy-env ADRs](../../comfy-env/adr/index.md).
Recorded retroactively 2026-08 from the maintainer's design plus two
independent audits; defects the audits verified are stated in the records
rather than hidden.

| ADR | Decision | One-liner |
|-----|----------|-----------|
| [0001](0001-declarative-package-configs.md) | Declarative package configs + patch scripts | A package is a YAML file and an optional Python patch script; the grid propagates to ~38 packages by inheritance. |
| [0002](0002-rolling-releases-as-wheel-storage.md) | Rolling per-package GitHub Releases as storage | One `<pkg>-latest` release holds every wheel; skip-existing makes CI idempotent. |
| [0003](0003-static-index-from-releases-api.md) | PEP 503 static index generated from the Releases API | Releases are the source of truth; Pages serves the index; v1 shim survives for legacy consumers. |
| [0004](0004-combo-encoded-versions-and-metadata-patching.md) | Combo-encoded local versions + METADATA patching | `+cu128torch2.9` in the filename and inside the wheel, so resolvers see one consistent artifact. |
| [0005](0005-shared-grid-and-arch-list-policy.md) | Shared (cuda x torch) grid + arch-list policy | One grid in `_defaults.yml`; arch lists mirror PyTorch's own build scripts, with +PTX always on the highest arch. |
| [0006](0006-fitting-cuda-compiles-into-hosted-ci.md) | Fitting CUDA compiles into hosted CI | Disk freeing, compile sharding, and checkpoint chains squeeze multi-hour builds under GitHub's 6-hour cap. |
| [0007](0007-phantom-combos-denylist.md) | Phantom combos: a curated denylist of upstream gaps | Cells upstream never published are skipped instead of failing at torch-install time. |
| [0008](0008-upstream-torch-watcher.md) | Upstream torch watcher | **Proposed.** A daily job that detects new upstream (cuda, torch) combos and builds them automatically. |
