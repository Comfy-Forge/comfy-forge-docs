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
| [0009](0009-auditwheel-exclusions.md) | auditwheel exclusions: match sonames, assert the rest | Exclude only what torch already loads; every exclude is a potential ImportError, so detect the rest instead of pre-empting it. |
| [0010](0010-no-free-threaded-builds.md) | No free-threaded builds | `cp3XXt` is a separate ABI; supporting it doubles the Python axis for a population that does not exist yet. Revisit triggers named. |
| [0011](0011-torch-independent-packages.md) | Torch-independent packages lose the torch axis | A package that never links libtorch is built once per (cuda, python, platform); identified by DT_NEEDED, not by Requires-Dist. |
| [0012](0012-arch-list-policy.md) | Arch lists: per-CUDA policy, clamped by torch runnability | Derived per CUDA from PyTorch's own union minus dead population (−5.0), clamped by toolkit + torch bounds; +PTX per major family. Supersedes 0005's mirror-PyTorch rule. |
| [0013](0013-arch-verification.md) | The arch list is asserted, not assumed | **Proposed.** cuobjdump post-build assertion, FP8/arch-gate lint, per-wheel provenance and build_epoch. |
| [0014](0014-zero-shim-sharding.md) | Zero-shim sharding | `sharding: N` is the whole opt-in: an nvcc-seat wrapper hash-partitions TUs; shards hand off a content-addressed ccache; the link job replays and asserts ≥90% hits. |
| [0015](0015-linux-aarch64-opt-in.md) | linux_aarch64 as an opt-in platform | Per-package platform opt-in; own ARM arch table (Thor native on 13.x); cu124 unbuildable (sbsa repo starts at 12.5); piloted green on cc_torch. |
