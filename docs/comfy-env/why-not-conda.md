# Why not just conda?

If you are thinking "this is just reinventing conda", you are absolutely
right. Combo detection, torch-family pinning, resolving wheel URLs by
scraping a PEP 503 page, `+cuXtorchY` local-version tags, `#sha256=`
fragment plumbing -- all of it exists because **the CUDA/torch variant axis
does not exist in PyPI's model**, and conda's model has it natively. A conda
solver does every one of those jobs for free, and comfy-env would gladly
delegate them:

```toml
[dependencies]          # not [cuda], not [pypi-dependencies]
flash-attn = "*"        # solver picks the variant for this env's torch+CUDA
```

The `[cuda]` section stops existing; comfy-env's packages/ layer shrinks to
a channel URL. The `conda-cuda-packages` repo already prototypes the recipes
(rattler-build, CUDA variant handling) for `cc_torch`, `flash_attn`,
`torch_generic_nms`.

## Why that path is closed today

**The PyTorch team does not publish conda packages**, which is quite
egregious, given torch is the poster child for the exact problems conda
exists to solve (bundled libomp copies,
import-numpy-before-torch-or-was-it-the-other-way-around native loading
order).

!!! note "A personal note about the PyTorch situation because I'm really not happy about it"
    PyTorch saying "we will shut down conda support because only 5% of our downloads come through there" is like a hospital saying:
    "Only 5% of our arrivals are by ambulance, so ambulances are clearly low ROI and we shouldn't support them anymore"

And conda-forge's community builds, healthy as they are, cannot cover the
need -- measured rather than suspected. The demand curve is the wheel farm's
own build grid: **29 (cuda, torch) cells**, torch 2.4.1 through 2.13.0,
cu124 through cu132. Against conda-forge's actual `pytorch` builds
(anaconda.org API, checked 2026-08-27, 4573 artifacts):

- **12 of 29 cells exist on linux-64 -- 41%.**
- **cu128 does not exist on conda-forge linux-64 for *any* torch version**
  -- and cu128/torch2.8 is the fleet's workhorse cell. (win-64 has cu128;
  linux never got it.)
- cu124 never existed there; cu132 does not yet.
- The pattern is structural, not backlog: conda-forge ships **one or two
  CUDA lines per torch version**, migrating with its global pinning
  (cu118 → cu120 → cu126 → cu129 → cu130). It is a moving pointer, not a
  matrix, and PyPI torch ships 3-4 CUDA lines per version. **The historical
  matrix will never appear upstream by policy.**

## What switching would actually take

1. **A torch channel of our own** -- repackaging PyPI's torch wheels as
   conda packages: a second farm, torch-shaped, though repackaging rather
   than compiling. 29 cells x their python axes x three platforms is
   roughly 350 artifacts of wrap-the-wheel work, automatable with the same
   machinery the wheel farm already runs.
2. **The dependent-package matrix** -- ~27 packages x the variant grid, as
   conda builds. The recipes exist; the matrix is the cost. The same work
   the wheel farm already does, in a different package format.
3. **Cross-build tensor-ABI verification** -- the ComfyUI host runs
   PyPI-built torch; a worker on conda-built torch of the same version
   crosses torch's *private* tensor-sharing ABI between two different
   builds. Probably compatible at equal versions, never verified. Until it
   is, the [serialization ladder](process-boundary.md) must treat the
   boundary as cross-build.

Until then: the custom index, combo detection and torch-family pinning stay
here.
