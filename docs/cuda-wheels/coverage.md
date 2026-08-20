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
We do not skip aarch64 and we set the same glibc floor as PyTorch (2.28)

**Packages can override our build default policy**: for example, sageattention has a kernel floor (sm_80).

The mechanism -- how the PCWM becomes grid rows, and how a package's config
becomes CI jobs -- is walked through in
[How does a package become build jobs?](build-process.md#how-does-a-package-become-build-jobs)
-- along with how to add a package of your own.

!!! info ""
    *The upstream index this coverage tracks is not a tidy place:
    [Upstream PyTorch quirks](upstream-quirks.md)*