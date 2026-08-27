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
[comfy-env docs](../../comfy-env/one-solver.md)).

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
real; JIT-only nvdiffrast *gains* a ninja declaration).

**Torch policy (revised 2026-08-24): the torch family is stripped from
every wheel, farm-wide.** The earlier policy -- leave bare `torch`/floor
specifiers alone, never emit `torch==X.Y.Z` -- was not enough. An audit of
the live index found 17 of 38 packages still declaring `torch` or
`torchvision`, and every one of them is unresolvable in the environment
comfy-env builds.

The reason a torch declaration differs from a numpy one: comfy-env pins the
torch family **workspace-wide**, by version *and* index, and writes it into
every generated feature, because tensors cross the process boundary over
torch's private multiprocessing ABI (`reduce_storage` /
`rebuild_cuda_tensor`), which carries no version handshake. comfy-env
enforces this so strictly that it *strips* torch from a node's own
declarations (`_strip_torch_family()`). A wheel's `Requires-Dist` is the
one channel that bypasses that strip, because it travels inside the
artifact rather than in the manifest comfy-env generates. So
`Requires-Dist: torch>=2.4.0` asks the solver to satisfy `torch` from its
default source (PyPI) while the manifest pins the same name from the
PyTorch CUDA index: best case redundant, realistic case a second CPU-only
torch, or outright failure. It can never be *useful* -- the only torch that
will ever be present was pinned before the wheel was selected, and the
wheel's local version (`+cu128torch2.8`) already records which torch it was
built against far more precisely than a floor does.

The metadata is therefore deliberately incomplete: the wheel does need
torch to import. That is the same trade `--no-deps` makes today, recorded
honestly in the artifact instead of hidden behind an install flag, and it
is safe *in this context* because a cuda-wheel is only ever installed into
an env whose torch was pinned first.

Implemented farm-wide rather than as a per-package list (`strip_torch_family`
in `patch_wheel_version.py`), so packages built later cannot regress. Three
guards keep it that way: the loader rejects a `requires_dist` naming the
family, the verify gate fails any wheel that still ships one, and lookalike
distributions (`torch-scatter`, `pytorch-lightning`) are matched by exact
distribution name, never by prefix.

This unblocks the downstream goal: with the family gone, comfy-env can feed
the wheel URLs into `build_env_toml()` as ordinary `pypi-dependencies` and
delete the post-pixi `uv pip install --no-deps` pass, putting the wheels
inside `pixi.lock`, hashed. (pixi has no `--no-deps` equivalent --
prefix-dev/pixi#1417 -- which is why `Requires-Dist` becomes load-bearing
the moment the wheels are inlined, and why this had to land first.)
Result: resolver-safe wheels that consumers can inline as ordinary URL
dependencies, hashed, inside their lockfiles.

Propagation caveat: comfy-env's env identity hashes the wheel URLs, so a
metadata-only re-upload under the same filename is invisible to it --
curated metadata reaches consumers through actual rebuilds.
