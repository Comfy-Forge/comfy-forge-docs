# Coverage

## What packages do we compile for?

As of August 2026, 39, see the full list at README.md

## What combos do we compile for?

**Exactly the ones PyTorch ships, minus what we deliberately skip.** 

Two named references define the target:

- **The P.C.W.M** -- the *PyTorch CUDA Wheel Matrix*: every
  (CUDA x torch x Python x platform) combination that
  `download.pytorch.org/whl/` actually publishes.
  The page is scraped daily-ish and results saved at `defaults/scraped_torch_matrix.json`.
  The grid in `defaults/python_cuda_torch_os_policy.yml` is **derived from it**, so a new torch release or
  CUDA line widens the farm automatically.

- **The P.A.M** -- the *PyTorch Arches Matrix*: which GPU architectures
  (`sm_75` ... `sm_120`) each of those combos is compiled for.

  Arches vary across CUDA versions, and unlike the
  combos, the arch lists are **not** derived -- they are an owned policy
  ([CW-ADR-0012](adr/0012-arch-list-policy.md)) that takes PyTorch's own lists
  into consideration. We keep them in `defaults/arch_policy.yml`.

What we skip, on purpose: pre-release Pythons, free-threaded builds
([CW-ADR-0010](adr/0010-no-free-threaded-builds.md)), and the cells upstream
never shipped ([CW-ADR-0007](adr/0007-phantom-combos-denylist.md)).
aarch64 is **opt-in per package** ([CW-ADR-0015](adr/0015-linux-aarch64-opt-in.md));
see [Linux aarch64](#linux-aarch64) below. On x86 we set the same glibc floor as
PyTorch (2.28); ARM repairs to `manylinux_2_39`.

The SASS sets match torch's own wheels **exactly**, per CUDA line and per
platform -- including Maxwell (5.0) on cu124/cu126 and the per-torch sm_70
drops tracked in `arch_exceptions`. The one deliberate divergence is PTX:
the farm always ships `+PTX` on the highest arch (forward compat for GPUs
newer than any SASS in the wheel), while torch's release wheels stopped
shipping PTX entirely in 2.13 -- a size trade-off that makes sense for a
250MB libtorch and not for kilobyte extension wheels.

**Packages can override our build default policy**: for example, sageattention has a kernel floor (sm_80).

The mechanism -- how the PCWM becomes grid rows, and how a package's config
becomes CI jobs -- is walked through in
[How does a package become build jobs?](build-process.md#how-does-a-package-become-build-jobs)
-- along with how to add a package of your own.

!!! info ""
    *The upstream index this coverage tracks is not a tidy place:
    [Upstream PyTorch quirks](upstream-quirks.md)*

## Linux aarch64

Opt-in per package ([CW-ADR-0015](adr/0015-linux-aarch64-opt-in.md)): today only
the pilot (`cc_torch`) carries ARM cells. The default stays
`platforms: ["linux", "windows"]`, so every other package is x86+Windows only.
The ARM arch lists that *would* apply live in `arch_policy_aarch64`.

### Why ARM gets its own fallback cell

comfy-env's tier-2 fallback is `cu12.8 / torch 2.8` on x86_64 but
**`cu13.0 / torch 2.10` on linux aarch64** (`packages/cuda_wheels.py`,
`FALLBACK_COMBO_AARCH64`). ARM is not a nudged variant of the x86 cell -- it
cannot be, for three independent reasons:

1. **`(12.8, 2.8)` has no ARM wheels at all.** PyTorch published no
   linux-aarch64 wheel for the whole 2.8 line on cu128 -- torch 2.8.0,
   torchvision 0.23.0 and torchaudio 2.8.0 are x86_64/Windows only there. The
   aarch64 cu128 build broke mid-cycle
   ([pytorch#157548](https://github.com/pytorch/pytorch/issues/157548)) and came
   back for 2.9.
2. **`(13.0, 2.8)` does not exist anywhere.** PyTorch's CUDA 13 line starts at
   torch 2.9, on any platform.
3. **Staying on 12.8/12.9 leaves Thor dead.** Their ARM arch list is
   `8.0;9.0+PTX;10.0;12.0+PTX`, and `sm_110` has no cubin at or below it -- the
   `10.0` cubin cannot cross a major and the `12.0` PTX sits above it -- so a
   Thor raises `cudaErrorNoKernelImageForDevice` at first kernel launch.

13.0's ARM list carries `11.0` natively, so every current ARM CUDA product is
covered: Grace (`sm_90`), GB200 (`sm_100`), Thor (`sm_110`), Orin (`sm_87`, via
the `8.0` cubin). From torchvision 0.25 / torchaudio 2.10 the ARM wheels are also
CUDA-tagged (`+cu130`) rather than the plain CPU-only builds cu128/cu129 carry at
that torch level.

The cost, stated plainly: **CUDA 13 requires driver r580+.**
