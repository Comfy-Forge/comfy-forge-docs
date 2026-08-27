# Why not just conda?

If you are thinking "this is just reinventing conda", you are absolutely
right. Wheel resolution, combo detection and torch-family pinning are all
things a conda solver does for free -- and comfy-env would gladly delegate
them.

## "Why this logic lives in comfy-env at all"

Ideally wheel/dependency resolution would be fully delegated to conda and the
prebuilt CUDA packages published to a conda channel.

That path is closed today because **the PyTorch team does not publish
conda packages**, which is quite egregious, given torch is the poster child for the
exact problems conda exists to solve (bundled libomp copies,
import-numpy-before-torch-or-was-it-the-other-way-around native loading order).

Until torch is resolvable through conda, the custom index, combo detection and torch-family pinning
stay here.

!!! note "A personal note about the PyTorch situation because I'm really not happy about it"
    PyTorch saying "we will shut down conda support because only 5% of our downloads come through there" is like a hospital saying:
    "Only 5% of our arrivals are by ambulance, so ambulances are clearly low ROI and we shouldn't support them anymore"

The conda-forge pytorch feedstock is actively maintained and closer to
viable than its reputation suggests; what "full coverage" still requires, and
the two upstream developments that would dissolve this whole layer, are
tracked in [One solver](one-solver.md#what-it-costs).
