# CW-ADR-0011: Torch-independent packages lose the torch axis

**Status:** accepted (2026-08-19) -- field to be added; tagging unchanged for now

## Decision

> **A package declares whether it links `libtorch`, and one that does not is
> built once per `(cuda, python, platform)` instead of once per torch.** Not a
> separate index root (fragments discovery for no gain); not a change to the
> local version tag yet (it would break comfy-env's resolver and every pinned
> URL already in the wild).

- New config field, default true: `links_torch: false`.
- `generate_matrix.py` collapses the torch axis for those packages, picking one
  canonical torch per CUDA line to build against.
- Wheels keep the existing `+cu128torch2.8` tag. For these packages the torch
  component is a fiction, and it stays a fiction deliberately -- see rejected
  alternatives.
- The per-combo index should list such a package under **every** torch
  directory for its CUDA version, because the wheel genuinely works with all of
  them.

## Context

The farm's grid is `architectures x CUDA x Python x torch x CPU x OS`. For a
package that never links `libtorch`, the torch axis is not a dimension at all:
the produced binary is byte-identical across torch versions. Building it over
the shared 21-pairing grid produces the same wheel roughly four times per CUDA
line.

This is not hypothetical, and it was not noticed in review -- it was discovered
by the maintainer hitting it. Three configs already work around it by hand,
pinning one torch per CUDA in a `build_matrix` block written for that purpose.
`packages/llama_cpp_python.yml` states the reasoning in a comment:

> *this is a GGML/cuBLAS build, NOT a PyTorch extension -- it does not link
> libtorch, so it has no torch axis. The build_matrix below pins ONE torch per
> CUDA solely to satisfy the farm's job generator; the produced binary is
> identical across torch versions.*

A workaround repeated in three files, explained in prose, is a schema gap.

**Identifying them requires the build system, not metadata.** The obvious test
-- does the wheel declare `torch` in `Requires-Dist` -- is wrong in both
directions. Of 27 published packages, 14 do not declare torch, and most of
those (`pytorch3d`, `torch-scatter`, `torch-sparse`, `nvdiffrast`,
`sageattention`) are unambiguously torch extensions that simply assume torch is
present.

Two sound tests exist. `DT_NEEDED` for `libtorch` on the compiled extension
answers definitively but requires a built wheel. Reading upstream's own build
files at the pinned tag -- `setup.py`, `CMakeLists.txt`, `pyproject.toml` --
answers the same question for a few KB per package, *before* any build, and is
what the full survey used.

All 50 packages have now been surveyed this way. **46 link torch; 4 do not.**

| Package | Why it is torch-free |
|---|---|
| `cumm` | `pccm` / `PCCMExtension` build; `setup.py` deps are `["pccm"]`. |
| `spconv` | Same `pccm` build; deps are `["cumm"]`. Torch is imported by the Python layer only -- `core_cc*.so` never links it. |
| `llama_cpp_python` | scikit-build-core over GGML's CMake. No torch in build or link. |
| `flashinfer` | `flashinfer/jit/cpp_ext.py` forks torch's `cpp_extension`, but its ldflags are `-lcudart -lcuda` and nothing more. |

Of the 46, 44 were established by `CUDAExtension` / `BuildExtension` /
`cpp_extension` in upstream's `setup.py`, `pyg_lib` by `find_package(Torch)` in
its CMake, and `nvdiffrec_render` because upstream ships *no* `setup.py` -- it
is a research repo, and this farm's own patch script synthesises the
`CUDAExtension`. That last one is torch-linked by our doing, not upstream's.

**`vllm` is not torch-independent.** An earlier draft of this ADR listed it
alongside the other three because it carries the same hand-written
`build_matrix`. It carries it for the opposite reason: `setup.py` uses
`cpp_extension` and `CMakeLists.txt` calls `find_package(Torch)`, and vLLM
hard-pins exactly one torch per release. It is maximally torch-*dependent*,
merely narrow. A narrow `build_matrix` is therefore not evidence of
independence, and `links_torch` must not be inferred from one.

**`flashinfer` is a qualified yes.** It does not link libtorch, but it reads
`torch._C._GLIBCXX_USE_CXX11_ABI` and bakes the result into its *compile*
flags. PyTorch flipped that default during the manylinux_2_28 migration in the
2.x line. So a flashinfer wheel is torch-agnostic at link time while still
carrying one torch's C++ ABI setting, and wheels built either side of that flip
are not interchangeable. The exact torch version of the flip has not been
pinned down; it must be before flashinfer wheels are published under every
torch directory.

## Alternatives rejected

**Drop `torch<M.m>` from the local version tag.** Honest, and the tag would
stop asserting a binding that does not exist. Rejected for now on two counts:
comfy-env's resolver filters candidates on `+cu<short>torch<M.m>`
(`packages/cuda_wheels.py`), so an untagged wheel would match nothing; and
every URL already pinned in a lockfile or a generated pixi manifest contains
the current form. Making the tag honest is a coordinated change across two
repos, and it captures far less value than collapsing the build axis, which is
local to this one.

**A separate index root for torch-independent wheels.** Splitting discovery in
two so consumers must know which half a package lives in, to express a property
that is already a field on the package. The per-combo directories added in the
matrix work solve the presentation problem without fragmenting the index.

**Infer it instead of declaring it.** Tempting -- `readelf -d` on a built wheel
answers definitively. But it answers *after* the build, and the axis has to be
decided *before* one. A declared field with a post-build assertion is the right
order.

## Consequences

- `spconv` and `cumm` stop building roughly four redundant wheels per CUDA
  line. On today's grid that is the difference between ~180 wheels each and
  ~40.
- The two hand-written `build_matrix` blocks that exist for this reason
  (`flashinfer`, `llama_cpp_python`) become a single field, and stop being a
  pattern the next contributor copies without understanding. `vllm` keeps its
  block, which encodes a genuine upstream torch pin.
- **The saving is smaller than the package count suggests.** `flashinfer` and
  `llama_cpp_python` already pin one torch per CUDA by hand, so they are
  already narrow. The redundant builds are entirely `spconv` (145 wheels) and
  `cumm` (180 wheels), which drop to roughly 90 combined.
- **The declaration is unverified.** Nothing checks that a package claiming
  `links_torch: false` actually lacks the `DT_NEEDED`. It should: a post-repair
  assertion in the same place CW-ADR-0009 put the vendored-library check would
  catch a package that starts linking torch after being declared independent --
  which would otherwise ship one wheel that works against exactly one torch and
  claims to work against all of them. That is a worse failure than the
  duplication this ADR removes, so the assertion is not optional.
- No package remains unclassified. The 46 keep the default `links_torch: true`,
  which is also the safe direction for anything added later: the cost of a wrong
  default is redundant builds, not broken wheels.
