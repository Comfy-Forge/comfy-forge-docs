# The two-system problem and its exit paths

comfy-env currently runs **two package systems in one environment**: pixi
resolves the conda + ordinary PyPI layer into `pixi.lock`, and a separate
post-pixi `uv pip install --no-deps` pass installs the CUDA wheels --
because pixi cannot express "install this wheel but ignore its declared
dependencies" ([prefix-dev/pixi#1417](https://github.com/prefix-dev/pixi/issues/1417)).

The costs of the split:

- `pixi.lock` describes an env that is not the env that runs -- the
  highest-risk binaries (flash-attn, cumesh, ...) live outside the lock,
  unhashed, from a mutable index.
- Any plain `pixi run`/`pixi install` against the env would re-sync to the
  lockfile and strip the uv-installed wheels; the runtime survives because
  worker launch uses `pixi run --as-is` -- a correct but load-bearing
  coincidence.
- The uv pass runs `--no-cache`, so the largest wheels re-download on every
  reinstall with no cross-env dedup.

Interim mitigations: the pixi binary is now version-pinned and
checksum-verified, and wheel `Requires-Dist` curation in the cuda-wheels
farm (strip build-tool leakage, pin sibling packages, keep true runtime
deps) is planned -- that alone makes the wheels inlineable on today's pixi.

Beyond that, **two upstream developments would dissolve the split
entirely**. We would love either one.

## Exit path A: pixi learns `no-deps`

[PR #5464](https://github.com/prefix-dev/pixi/pull/5464) ("feat: support
pip style no-deps") implements exactly what comfy-env needs: per-dependency
`no-deps` for PyPI packages, where URL dependencies "lock metadata directly
without resolving dependencies" -- i.e. our CUDA wheels, inlined in the
generated manifest, dependency-blind, **and still recorded deterministically
in `pixi.lock`**.

Status: open since 2026-02, non-draft, zero reviews -- stalled in the queue.
The related design avenue is
[#4392](https://github.com/prefix-dev/pixi/issues/4392) (manifest-supplied
dependency metadata, `needs-design`). Either landing lets the generated
manifests carry the wheels natively and retires the uv side-channel with
zero farm changes.

**Our position: we would love to see #5464 merged**, and we are a concrete
downstream test case (a manifest compiler + a wheel farm) happy to validate
it against real workloads.

## Exit path B: conda-forge pytorch reaches full coverage

If torch resolves natively from conda-forge for every combo our users have,
the endgame is simpler than fixing wheels: **publish the CUDA packages as
conda packages** and let one solver handle everything.

- The [conda-cuda-packages](https://github.com/PozzettiAndrea/conda-cuda-packages)
  repo already prototypes this: rattler-build recipes for `cc_torch`,
  `flash_attn`, `torch_generic_nms` with CUDA variant handling.
- Conda encodes cuda/torch variants natively in **build strings** -- no
  `+cu128torch2.9` local-version hacks, no METADATA patching, no
  combo-resolution logic in comfy-env at all: declare `flash-attn` in
  `[dependencies]`, and the solver picks the right variant for the env's
  torch and CUDA, inside the lockfile, automatically.

Current state (checked 2026-08): the conda-forge
[pytorch feedstock](https://github.com/conda-forge/pytorch-cpu-feedstock)
is at **2.13.0** -- current, actively maintained (with prefix-dev
contributors involved), with CUDA builds on Linux **and Windows** and
CUDA 13 variants underway. This is much healthier than its reputation.
What "full coverage" still requires before we could switch:

1. ~~**Version breadth**~~ **-- solved** (re-checked 2026-08): the feedstock
   carries the historical matrix after all -- 2.4.1, 2.5.1, 2.6.0, 2.7.1,
   2.8.0, 2.9.1, 2.10.0 through 2.13.0, each at py3.10-3.13, on linux-64,
   win-64 *and* linux-aarch64 (whose CUDA coverage is better than PyPI's).
   What replaces it: **CUDA-variant alignment**. conda-forge builds one or
   two variants per torch line and they are not PyPI's cells -- torch 2.8.0
   on linux is **cuda129 only** (no cuda128), while win-64 is cuda128. Our
   x86 fallback combo (12.8, 2.8) has no exact linux cell; the aarch64
   fallback (13.0, 2.10) exists exactly. Either the combo table learns
   conda-forge's cells, or we accept nearest-minor and verify CUDA minor
   compatibility holds for the farm's extensions.
2. **Dependent-package matrix**: the real work is our side -- building the
   ~27 CUDA packages as conda packages across the variant grid (the
   rattler-build recipes exist; the matrix is the cost).
3. **Build-lineage caveat**: the ComfyUI *host* runs PyPI-built torch.
   Workers on conda-forge-built torch of the same version would cross
   torch's private tensor-sharing ABI between two different builds --
   probably compatible at equal versions, but it must be verified before
   zero-copy transport trusts it (see the tensor serialization ladder).

## How the paths compose

The farm metadata curation is worth doing regardless: it fixes standalone
`pip install` consumers today and unblocks inlining on the pinned pixi.
Path A removes the need for curation-as-resolver-safety (it stays useful as
honest metadata). Path B eventually removes the wheel farm's raison d'etre
for the conda-capable population -- while the wheel index remains the
answer for plain-venv users outside comfy-env.

Watch items, in preference order: **#5464 merged** > #4392 designed >
conda-forge dependent-package matrix built out.
