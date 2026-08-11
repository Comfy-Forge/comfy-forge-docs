# CW-ADR-0004: Combo-encoded local versions + METADATA patching

**Status:** accepted

## Decision

- Append a **local version tag** encoding the combo:
  `<pkg>-<version>+cu<CCC>torch<M.m>-cp<PY>-...whl`
  (e.g. `flash_attn-2.8.3+cu124torch2.4-cp311-cp311-win_amd64.whl`). v2
  keeps the dot in the torch version; the v1 index stripped it and survives
  only as the compat shim (CW-ADR-0003).
- **Patch the wheel's internal METADATA to match the filename**
  (`scripts/patch_wheel_version.py`: rewrite `Version:`, rename
  `.dist-info`, rebuild `RECORD` hashes). uv hard-fails on
  filename/METADATA version mismatch, so this is required, not cosmetic.
- Linux wheels go through `auditwheel repair` to `manylinux_2_35`,
  **excluding** libcuda/libcudart/libtorch/libc10 -- driver and torch libs
  must come from the host env, never be vendored.
- Builds pin exactly `torch==<ver>+cu<short>` from PyTorch's own index, so
  every wheel is tied to a torch family -- the same family pin comfy-env
  replicates into its generated envs
  ([comfy-env ADR-0004](../../comfy-env/adr/0004-prebuilt-cuda-wheel-index.md)).

## Context

Every wheel is compiled for exactly one (CUDA x torch) pairing, but wheel
filenames only encode package version, Python ABI, and platform. Without
the pairing in the *version*, two wheels for different CUDA/torch combos
would collide, and resolvers could not select by combo.

## Consequences

- One release can hold the full combo matrix per package without
  collisions; resolvers select by substring/spec on the local tag.
- Local version tags make the wheels **unpublishable to PyPI** -- accepted;
  Releases + Pages is the distribution channel (CW-ADR-0002/0003).
- Known repack gaps (2026-08 audit): a `<name>-<ver>.data/` directory is
  not renamed (wheel-spec violation if a package ever ships one -- none do
  today), and zip repack drops POSIX exec bits/symlinks. Direction: inject
  the local version at build time so the wheel is *born* correct and the
  repack step disappears.

## Direction: Requires-Dist curation (planned)

The same METADATA pass currently leaves `Requires-Dist` as upstream wrote
it, which is wrong for the artifact we ship (2026-08 audit of live wheels):
spconv leaks build-time tools (`pccm`, `ccimport`, `pybind11`) as runtime
deps and pins sibling `cumm` to a spec that resolves to the WRONG artifact
on PyPI; gsplat declares bare `torch`, inviting resolvers to re-decide the
one thing consumer envs pin deliberately. This is why comfy-env must
install these wheels with `--no-deps`, outside its lockfile (the
"two-system problem" --
[comfy-env docs](../../comfy-env/two-system-problem.md)).

Planned: a per-package `requires_dist_overrides` field in `packages/*.yml`,
applied in this same rewrite step -- strip build-tool leakage, rewrite
sibling farm packages to exact local-version pins, keep genuine runtime
deps (incl. runtime-JIT toolchains like gsplat's `ninja`). Several wheels
(sageattention, cc_torch, fused_ssim) already declare nothing and need no
changes. Result: resolver-safe wheels that consumers can inline as ordinary
URL dependencies, hashed, inside their lockfiles.
