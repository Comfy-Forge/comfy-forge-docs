# CW-ADR-0005: The shared (cuda x torch) grid and arch-list policy

**Status:** accepted

## Decision

> **One shared grid; arch lists mirror PyTorch's own build scripts, with
> +PTX always on the highest arch.** Not per-package matrices (drift);
> not taste-curated arch lists (copied from upstream CI, with one
> deliberate forward-compat deviation); stable CPython 3.10-3.14 only.

- **One grid** in `packages/_defaults.yml` (21 combos at the time of
  writing: cu124 x torch 2.4-2.6 through cu130 x torch 2.9-2.11), each row
  carrying its Python list and GPU arch list. Packages inherit it wholesale
  unless they declare their own matrix (CW-ADR-0001).
- **Arch lists mirror PyTorch's own `build_cuda.sh`** for the matching
  release -- with ONE deliberate deviation: **`+PTX` is always forced onto
  the highest base architecture**, so wheels stay JIT-forward-compatible
  with GPUs newer than any SASS in the wheel, even after PyTorch rotates
  `+PTX` off a maturing toolchain. `_ensure_ptx_on_highest_base` re-applies
  the rule at matrix-resolve time, so a hand-added row without `+PTX` still
  gets one.
- Resolution order for a package's arch list: per-combo override ->
  `arch_list_by_cuda[cuda]` -> package `arch_list` -> grid row -> **live
  fetch of PyTorch's `build_cuda.sh`** for the tag
  (`scripts/fetch_pytorch_arch_lists.py`, 30-day disk cache). The live
  fallback means a new grid row may legitimately omit `arch_list` entirely.
- **Python axis policy:** stable CPython only, currently 3.10-3.14.
  Free-threaded variants (`cp313t`/`cp314t`/`cp315t`) and `cp315` are
  deliberately excluded: ComfyUI's distributions run standard builds, most
  farm packages have never been validated against no-GIL runtimes, and
  tracking them would roughly double the matrix for zero current users.
  Revisit when a ComfyUI distribution ships a free-threaded interpreter.
  Dev/rc builds upstream are never targeted.

## Context

Which (CUDA, torch, Python, platform) cells to build is the farm's central
policy question. Per-package answers drift; a single grid keeps ~38
packages aligned and makes coverage auditable.

## Consequences

- One edit propagates a torch release to the whole farm; per-cuda arch
  differences (Blackwell on cu128+, Maxwell dropped) stay factual because
  they are copied from PyTorch's own CI, not curated by taste.
- The +PTX policy trades slightly larger wheels and a possible slow
  first-launch JIT on future GPUs for not bricking them.
- The grid is hand-edited today, which is how it drifted 2 torch minors
  behind upstream (verified 2026-08: 2.12.1/2.13.0 on cu126/cu129/cu130
  missing). CW-ADR-0008 exists to close exactly this gap.
