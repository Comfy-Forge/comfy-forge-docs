# ADR-0004: Prebuilt CUDA wheel index

**Status:** accepted

## Context

Modern CV/ML node packs depend on CUDA-compiled packages -- flash-attn,
nvdiffrast, nunchaku, pytorch3d, gsplat. Every such wheel is compiled for a
specific combination of Python ABI (3.10-3.13) x torch version (2.4-2.11) x
CUDA version x OS x GPU architecture. Expecting end users to have a CUDA
toolkit and C++ compiler and to build from source is a support disaster;
upstream projects publish wheels for only a fraction of the matrix.

## Decision

Maintain a companion wheel farm,
[cuda-wheels](https://github.com/PozzettiAndrea/cuda-wheels), and resolve
wheels automatically (`packages/cuda_wheels.py`):

- Packages listed under `[cuda]` in `comfy-env.toml` are resolved against the
  **GitHub Pages simple index** (`pozzettiandrea.github.io/cuda-wheels/v2/`)
  for the user's exact combination; matching wheel URLs are inlined into the
  generated `pixi.toml` as URL pypi-dependencies.
- The resolver derives **torch family pins** so the chosen wheels and the
  env's torch agree.
- Network resilience: transient TCP resets are retried with a real
  User-Agent (`comfy-env/<version>`) because corporate proxies and AV
  products RST `Python-urllib`; if Pages is unreachable end-to-end, the
  resolver **falls back to the GitHub Releases API**, which sits on a
  different routing edge.

## Consequences

- Users never need nvcc or MSVC; `install.py` just works on a clean machine.
- The wheel farm is an external dependency the maintainer must keep building
  as new torch/CUDA versions ship.
- Combination resolution must happen at install time on the target machine
  (it depends on the host GPU and torch), which is why detection
  (`detection/`) feeds the install pipeline.
- When no prebuilt wheel exists for a combination, install fails with an
  explicit report rather than a silent source build.
