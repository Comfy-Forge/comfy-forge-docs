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

**Identifying them requires linkage, not metadata.** The obvious test -- does
the wheel declare `torch` in `Requires-Dist` -- is wrong in both directions. Of
27 published packages, 14 do not declare torch, and most of those
(`pytorch3d`, `torch-scatter`, `torch-sparse`, `nvdiffrast`, `sageattention`)
are unambiguously torch extensions that simply assume torch is present. The
sound test is a `DT_NEEDED` entry for `libtorch` on the compiled extension.

By that test, two packages currently built across the full grid are
torch-independent: **`spconv`** (`core_cc*.so` needs only `libcudart`) and
**`cumm`** (`libcudart` + `libnvrtc`). Both are pccm/pybind11 code generators
rather than torch extensions. With the three declared ones that is 5 of 50
known today; the remaining 45 have not been checked by linkage.

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
- The three hand-written `build_matrix` blocks in `flashinfer`,
  `llama_cpp_python` and `vllm` become a single field, and stop being a pattern
  the next contributor copies without understanding.
- **The declaration is unverified.** Nothing checks that a package claiming
  `links_torch: false` actually lacks the `DT_NEEDED`. It should: a post-repair
  assertion in the same place CW-ADR-0009 put the vendored-library check would
  catch a package that starts linking torch after being declared independent --
  which would otherwise ship one wheel that works against exactly one torch and
  claims to work against all of them. That is a worse failure than the
  duplication this ADR removes, so the assertion is not optional.
- 45 of 50 packages remain unclassified. They default to `links_torch: true`,
  which is the safe direction: the cost of a wrong default is redundant builds,
  not broken wheels.
