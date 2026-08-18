# CW-ADR-0009: auditwheel exclusions -- match sonames, assert the rest

**Status:** accepted (2026-08-18, after a measured survey of 3,869 published wheels)

## Decision

> **Exclude only what torch already loads, match the real soname, and detect
> everything else instead of pre-empting it.** Not a belt-and-braces list
> covering every CUDA library (each exclude is a live `ImportError` hazard, and
> measurement shows they are not being bundled); not per-package exclude
> config (three distinct libraries across the whole farm does not need a
> framework).

- Patterns are matched by `fnmatch` against the **soname**, not the filename.
  NVIDIA versions its libraries (`libcudart.so.12`); PyTorch does not
  (`libtorch.so`). Bare `libcudart.so` therefore matches nothing, and the
  torch patterns work only because torch sets no `SOVERSION`.
- Excluded libraries get **no rpath**, so an exclude is only safe for a
  library torch has already loaded at `import torch`.
- `libnvrtc` and `libnvrtc-builtins` are **deliberately bundled**.
- A post-repair assertion fails the build if any bundled library falls outside
  the known set, and a failed `auditwheel repair` is an error, not a warning.

## Context

The exclude list is the mechanism enforcing CW-ADR-0004's rule that driver and
torch libraries come from the host environment and are never vendored. Nine
patterns were passed; a 2026-08 audit found that seven worked, one was dead but
harmless, and `libcudart.so` had never matched anything -- so 1,438 of 3,869
published wheels carry a private `libcudart-<hash>.so.12.x`, in direct
violation of the ADR it was meant to implement.

Two competing proposals followed. One argued for adding excludes across the
whole CUDA math stack (cuBLAS, cuFFT, cuSPARSE, cuSOLVER, cuDNN, NCCL),
predicting that seven packages declaring `extra_cuda_components: cufft_dev`
would graft 267 MB each. A survey of every published wheel refuted it: exactly
**three** distinct libraries are bundled anywhere -- `libcudart` (1,438 wheels,
22 packages, 0.73 MB), and `libnvrtc` + `libnvrtc-builtins` (75 wheels, cumm
only). Zero cuBLAS, cuSPARSE, cuSOLVER, cuFFT, cuDNN, NCCL, nvJitLink. The
packages declaring `cufft_dev` carry only `libcudart`.

The decisive argument came from the proposal that lost. Because an excluded
library receives no rpath, it must already be resident when the extension is
`dlopen`ed -- so **every exclude is a potential `ImportError`**. `libcusolver`
is the worked example: torch reaches it only through a lazily-dlopened
`libtorch_cuda_linalg.so`, so excluding it would convert a fat wheel into
`ImportError: libcusolver.so.11`. The asymmetry settles the question: a missing
exclude costs megabytes, a wrong exclude costs a broken import.

Apparent patchiness in bundling (pytorch3d bundles in some combos and not
others) is not conditional repair. `nvcc` defaults to `-cudart static`, so many
extensions have no `DT_NEEDED` on `libcudart` at all and are repaired correctly
with nothing to bundle.

## Consequences

- `libcudart` stops being vendored going forward. Existing wheels are left
  alone: retro-repair would rewrite bytes under URLs consumers have already
  resolved, against CW-ADR-0002's acknowledged absence of hash pinning.
- The unmeasured tail (`mmcv`, `natten` and ten others absent from the survey
  snapshot) is covered by detection rather than speculation -- if one of them
  ever links cuBLAS, the build fails and names the library.
- `cumm` continues to ship ~104 MB of NVRTC per wheel, deliberately.
  `cumm/core_cc*.so` carries a hard `DT_NEEDED` on `libnvrtc`, and torch
  preloads NVRTC only when torch itself came from the pip `nvidia-*` wheels --
  so a conda or system-CUDA torch would leave cumm unable to import, and
  spconv with it, since spconv reaches NVRTC through importing cumm.
  Depending on `nvidia-cuda-nvrtc-cu12` instead was rejected: comfy-env
  installs these wheels with `--no-deps`, so the dependency would silently not
  be installed, trading a working 121 MB wheel for an `ImportError`.
- **Known defect:** `patches/pyg_lib.py` strips `LIBNVTOOLSEXT` from
  `TORCH_CUDA_LIBRARIES` but leaves `CUDA_NVRTC_LIB`, so pyg-lib's extension
  carries a `DT_NEEDED` on NVRTC. Inert today, because pyg-lib's wheels are
  unrepaired -- but it arms a 104 MB graft the moment repair reaches them. Fix
  it as part of that work, not before.
- **Known defect:** 51 published Linux wheels are tagged `linux_x86_64` rather
  than `manylinux`, i.e. they never went through repair -- 47 of them
  `cc_torch`. Cause unknown; a failed repair currently downgrades to a warning
  and ships the unrepaired wheel, which is the prime suspect.
- The Windows path is untouched. `auditwheel` is Linux-only by design and
  Windows extensions resolve CUDA through `PATH`/`CUDA_PATH`; 3,183 Windows
  wheels bundle nothing and work. Matching Linux's behaviour there would be
  symmetry for its own sake.
