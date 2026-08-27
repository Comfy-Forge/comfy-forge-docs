# Coverage

## What packages do we compile for?

As of August 2026, 42, see the full list at README.md

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
ARM is a **default platform**, not an opt-in. Every package in the farm
builds `linux_aarch64` unless it opts *out*; the platform was promoted after
the pilot went 112/112 green. This matters more than it sounds: the ARM lane
is resolved by a **different function with different inputs** -- it never
consults a package's x86 `arch_list` / `arch_list_by_cuda` fields and never
consults the policy's `arch_exceptions` table. A package with a carefully
reasoned x86 arch floor gets none of it on ARM. See
[How a cell gets its arch list](arch-selection.md).

The arch lists are the farm's **own** policy, not a mirror of torch's
wheels. That distinction is the whole point of the arch-policy decision:
mirroring upstream was the previous approach and it was abandoned. The
`arch_exceptions` table **adds** archs back for specific torch minors (its
polarity was inverted in August 2026); it does not record drops. And "per
platform" does not hold either -- the aarch64 table is a separate,
independently derived policy with no cu12.4 row, and exceptions never apply
to it.

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
