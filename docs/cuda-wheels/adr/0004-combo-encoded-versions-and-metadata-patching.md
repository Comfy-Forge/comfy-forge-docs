# CW-ADR-0004: Combo-encoded local versions + METADATA patching

**Status:** accepted

## Decision

> **The (CUDA x torch) combo lives in the version -- in the filename AND
> inside the wheel.** Not separate indexes per combo; not filename-only
> (uv hard-fails on filename/METADATA mismatch); local version tags with
> METADATA patched to match.

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

## Requires-Dist curation (2026-08-21)

The same METADATA pass also curates `Requires-Dist` -- upstream's list is
sometimes wrong for the artifact we ship (2026-08 audit of live wheels):
spconv leaks build-time tools (`pccm`, `ccimport`, `pybind11`) as runtime
deps and pins sibling `cumm<0.8.0` -- a spec our own farm cumm (0.8.2)
does not satisfy and that resolves to the WRONG artifact on PyPI; ovoxel's
sibling deps were bare names resolving against PyPI; detectron2 ships
`black==21.4b2` as a runtime dep. This mis-metadata is why comfy-env must
install these wheels with `--no-deps`, outside its lockfile (the
"two-system problem" --
[comfy-env docs](../../comfy-env/two-system-problem.md)).

Mechanism: an optional `requires_dist` list in the package's own
`package.yml` REPLACES the wheel's Requires-Dist (and Provides-Extra)
wholesale during this rewrite. `{LOCAL}` expands to the wheel's local tag
and `{VER:<folder>}` to a sibling's pinned `version`, yielding exact
local-version sibling pins (`cumm==0.8.2+cu128torch2.8`) -- PyPI forbids
local versions, so such a pin resolves from our index or fails loudly,
never to a stranger's package. The verify gate's C2 check asserts the
published wheel carries exactly the expanded list.

Curation is surgical, not blanket: most upstream lists are correct and
stay untouched, including genuine runtime-JIT deps (gsplat's `ninja` is
real; JIT-only nvdiffrast *gains* a ninja declaration). Torch policy:
leave upstream's bare `torch`/floor specifiers alone and never emit
`torch==X.Y.Z` -- consumer envs (comfy-env) pin torch at major.minor with
a per-package index on purpose, and an exact-patch pin in wheel metadata
would deadlock resolution the moment the index picks a different patch.
Result: resolver-safe wheels that consumers can inline as ordinary URL
dependencies, hashed, inside their lockfiles.

Propagation caveat: comfy-env's env identity hashes the wheel URLs, so a
metadata-only re-upload under the same filename is invisible to it --
curated metadata reaches consumers through actual rebuilds.
