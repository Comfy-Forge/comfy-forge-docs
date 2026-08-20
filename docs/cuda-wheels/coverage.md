# Coverage

What is in the farm right now.

!!! info "Snapshot -- August 2026"
    Every number here moves. The grid is **derived from what PyTorch ships**,
    so it widens with each torch release, and the wheel count grows with every
    build. For current figures always trust the
    [dashboard](https://pozzettiandrea.github.io/cuda-wheels/dashboard/) and the
    [full build matrix](https://pozzettiandrea.github.io/cuda-wheels/matrix/),
    not this page.

| | at time of writing |
|---|---|
| package configs | 49 (plus the `defaults/` trio; kaolin was dropped — upstream caps torch at 2.8) |
| packages published | 39 |
| wheels published | ~7,700 |
| combinations per package | up to 250 (x86+Windows), +112 for aarch64 opt-ins |
| coverage | CUDA 12.4-13.2 x torch 2.4-2.13 x Python 3.10-3.14 x {Linux, Windows, and per-package linux_aarch64} |

The repository layout -- every directory, script and workflow, and the two
rules that keep them tidy -- is broken down in
[The build process](build-process.md).

## What combos do we compile for?

**Exactly the ones PyTorch ships, minus what we deliberately skip.** Two named
references define the target:

- **The P.C.W.M** -- the *PyTorch CUDA Wheel Matrix*: every
  (CUDA x torch x Python x platform) combination that
  `download.pytorch.org/whl/` actually publishes. Scraped daily-ish and
  rendered live at
  [/matrix/](https://pozzettiandrea.github.io/cuda-wheels/matrix/); the grid in
  the grid in `defaults/python_cuda_torch_os_policy.yml` is **derived from it** (via the committed `defaults/scraped_torch_matrix.json`), so a new torch release or
  CUDA line widens the farm automatically.
- **The P.A.M** -- the *PyTorch Arches Matrix*: which GPU architectures
  (`sm_75` ... `sm_120`) each of those combos is compiled for. Unlike the
  combos, the arch lists are **not** derived -- they are an owned policy
  ([CW-ADR-0012](adr/0012-arch-list-policy.md)) that uses PyTorch's own lists
  as input, kept in `defaults/arch_policy.yml`.

    Getting an arch list wrong is **silent** -- it fails *after* a successful
    install. A real case from this farm: `diso` cannot build for Maxwell at
    all, because `atomicAdd(double*, double)` does not exist below sm_60. Had
    its list merely been *wrong* rather than impossible, the wheel would have
    installed happily and died on first use. Two rules follow:

    - **`+PTX` rides per major family** (e.g. `9.0+PTX` *and* `12.0+PTX`), so
      wheels stay JIT-compatible with GPUs newer than the toolchain that
      built them -- PyTorch itself has rotated PTX off its recent releases.
    - **A package can override the policy**, because kernel floors belong to
      packages. Resolution order, highest first: per-combo `arch_list` in the
      package's own `build_matrix` &rarr; `arch_list_by_cuda[cuda]` &rarr;
      `arch_list` &rarr; the per-CUDA policy row in `defaults/arch_policy.yml`.

What we skip, on purpose: pre-release Pythons, free-threaded builds
([CW-ADR-0010](adr/0010-no-free-threaded-builds.md)), and the cells upstream
never shipped ([CW-ADR-0007](adr/0007-phantom-combos-denylist.md)).
**aarch64 is no longer skipped wholesale**: it is an opt-in platform per
package ([CW-ADR-0015](adr/0015-linux-aarch64-opt-in.md)), piloted green on
cc_torch — 112 ARM cells including Thor-native cu13.x builds; cu124 is
unbuildable on ARM (NVIDIA's sbsa repo starts at 12.5).

The mechanism -- how the PCWM becomes grid rows, and how a package's config
becomes CI jobs -- is walked through in
[How does a package become build jobs?](build-process.md#how-does-a-package-become-build-jobs)
-- along with how to add a package of your own.

!!! info ""
    *The upstream index this coverage tracks is not a tidy place:
    [Upstream PyTorch quirks](upstream-quirks.md)*
