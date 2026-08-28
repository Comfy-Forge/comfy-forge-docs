# Why not just conda?

If you are thinking our [cuda] to pypi dependencies pipeline is "just reinventing conda", you are absolutely
right.

Combo detection, torch-family pinning, resolving wheel URLs by
scraping a PEP 503 page, `+cuXtorchY` local-version tags, `#sha256=`
fragment plumbing... all of it exists because **the CUDA/torch variant axis
does not exist in PyPI's model**, and conda's model has it natively.

A conda solver does every one of those jobs for free, and comfy-env would gladly
delegate them, but **the PyTorch team does not publish conda packages**.

I feel like this is quite egregious, given torch is the poster child for the exact problems conda
exists to solve (bundled libomp copies,
import-numpy-before-torch-or-was-it-the-other-way-around native loading
order).

!!! note "A personal note about the PyTorch situation because I'm really not happy about it"
    PyTorch saying "we will shut down conda support because only 5% of our downloads come through there" is like a hospital saying:
    "Only 5% of our arrivals are by ambulance, so ambulances are clearly low ROI and we shouldn't support them anymore"

Conda-forge's community builds, healthy as they are, are ~30% of what is published on pypi.
