# One solver

*comfy-env should not contain a package resolver. It contains one anyway --
smaller than it was, and this page is the plan for deleting the rest.*

## The layers, and where they stand

| # | Resolution system | Status |
|---|---|---|
| 1 | **pixi's solver** -- conda + PyPI, one lockfile | the one that should do everything |
| 2 | **the post-pixi `uv pip install --no-deps` pass** | **deleted in 0.4.31** -- see below |
| 3 | **comfy-env's hand-rolled `[cuda]` resolver** | still here, still load-bearing |

Layer 3 is what is left, and it is a real resolver in everything but name:

- **combo detection** -- probe the host's (CUDA, torch, python), walk a
  fallback ladder when the index lacks the cell;
- **torch-family pinning** -- strip `torch`/`torchvision`/`torchaudio` from
  every manifest and re-inject one workspace-wide pin with an explicit
  index, because nothing else guarantees parent and worker share one ABI;
- **URL resolution by HTML-scraping** -- `get_wheel_url` regex-parses a
  PEP 503 page and matches on anchor *text*, a private contract with the
  farm's torch-free alias scheme;
- **`+cuXtorchY` local-version tags** -- ABI encoded in a version string,
  because PyPI metadata has no variant axis to put it in;
- **hash plumbing** -- `#sha256=` fragments hand-carried from index to lock.

Every line of that exists because **the CUDA/torch variant axis does not
exist in PyPI's model**. Conda's model has it natively.

## The end state

Publish the ~27 CUDA packages as **conda packages** in a channel, variants
encoded in build strings, and let pixi's solver do all of it:

```toml
[dependencies]          # not [cuda], not [pypi-dependencies]
flash-attn = "*"        # solver picks the variant for this env's torch+CUDA
```

No combo detection, no torch stripping, no scraping, no local-version tags,
no fragment plumbing -- conda hashes and locks natively. The `[cuda]`
section stops existing; comfy-env's packages/ layer shrinks to a channel
URL. The `conda-cuda-packages` repo already prototypes the recipes
(rattler-build, CUDA variant handling) for `cc_torch`, `flash_attn`,
`torch_generic_nms`.

## What it costs

**1. torch itself must resolve from conda -- for every cell packs pin.**
This is the hard one. Packs pin torch families back to ~2.4; conda-forge
ships the current version, not the historical matrix
([checked 2026-08](why-not-conda.md): the feedstock is at 2.13, actively
maintained, CUDA builds on Linux and Windows -- much healthier than its
reputation, but it is a rolling head, not an archive). Covering every
(torch, cuda, python) cell the packs need means either conda-forge growing
the historical matrix or **our own channel repackaging PyPI's torch wheels
as conda packages** -- a second farm, torch-shaped.

**2. The dependent-package matrix is on us regardless.** ~27 packages x the
variant grid, as conda builds. The recipes exist; the matrix is the cost.
This is the same work the wheel farm already does, in a different package
format.

**3. Build lineage must be verified before zero-copy trusts it.** The
ComfyUI host runs PyPI-built torch. A worker on conda-built torch of the
same version crosses torch's *private* tensor-sharing ABI between two
different builds -- probably compatible at equal versions, never verified.
Until it is, the [serialization ladder](process-boundary.md) must treat the
boundary as cross-build.

Why the obvious shortcut is closed -- just take torch from conda today --
is [Why not just conda?](why-not-conda.md).

## History: the layer that already fell

Until 0.4.31 there was a third system: pixi could not express a no-deps
install, and the wheels' upstream `Requires-Dist` was wrong for our
artifacts, so after `pixi install` the CUDA wheels were installed
out-of-band with `uv pip install --no-deps`. The costs were real:
`pixi.lock` described an env that was not the env that ran; the
highest-risk binaries lived outside the lock, unhashed, from a mutable
index; a plain `pixi install` would strip them (worker launch survived on
`pixi run --as-is`, a correct but load-bearing coincidence); and
`--no-cache` re-downloaded the largest wheels on every reinstall.

Two upstream exits were tracked -- pixi PR #5464 (pip-style no-deps for URL
dependencies) and conda-forge pytorch coverage -- and the split ended by a
**third path**: the farm blanked in-wheel `Requires-Dist` across every
package, which makes a plain direct-URL dependency `--no-deps` *by
construction*. The wheels moved inside the manifests and `pixi.lock`,
hash-verified via `#sha256=` fragments, and the uv pass was deleted.

That fix is also the measure of this page: it removed a *pass*, not the
resolver. Layer 3 chose those URLs before pixi ever ran. **The end state is
that nothing chooses but the solver.**

## Watch items

In leverage order: conda-forge historical torch coverage (or the decision
to repackage it ourselves) &gt; the dependent-package matrix build-out &gt;
the cross-build tensor-ABI verification.
