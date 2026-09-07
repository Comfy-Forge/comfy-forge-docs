# ADR-0004: Prebuilt CUDA wheel index

**Status:** accepted

## Decision

> **Prebuilt CUDA wheels from our own index, resolved at install time for
> the user's exact machine.** Not source builds (no end user has
> nvcc/MSVC); not upstream wheels alone (they cover a fraction of the
> ABI x torch x CUDA x OS x arch matrix); a companion wheel farm plus a
> two-tier resolver with a known-good fallback combo.

Maintain a companion wheel farm,
[cuda-wheels](https://github.com/PozzettiAndrea/cuda-wheels), and resolve
wheels automatically (`packages/cuda_wheels.py`):

- Packages listed under `[cuda]` in `comfy-env.toml` are resolved against the
  **GitHub Pages simple index** for the user's exact combination. The index
  base is `https://comfy-forge.github.io/cuda-wheels/`
  (`packages/cuda_wheels.py:CUDA_WHEELS_INDEX_DEFAULT`, overridable with
  `COMFY_ENV_CUDA_WHEELS_INDEX`); the resolver fetches one page per package
  directory under it, and the end-to-end fallback is the Releases API of
  `Comfy-Forge/cuda-wheels`. The older `pozzettiandrea.github.io/cuda-wheels/v2/`
  is the legacy farm and is not what comfy-env resolves against.
- **The matched wheel URLs are inlined into the generated `pixi.toml` as
  direct-URL `pypi-dependencies`.** They live inside `pixi.lock`, are
  hash-verified where the index anchor carries a `#sha256=` fragment, are
  cached by uv rather than re-downloaded, and survive a plain `pixi install`.

    !!! note "This replaced a post-pixi side channel"
        Wheels used to install in a `uv pip install --no-deps` pass *after*
        pixi, outside `pixi.toml` -- the **two-system problem**: two package
        managers writing one env, with the second one's work invisible to the
        lockfile and erased by any later plain `pixi install`. Inlining became
        possible when the farm blanked in-wheel `Requires-Dist` (a URL dep is
        then `--no-deps` by construction). The side-channel pass was deleted
        with it (`install/workspace.py` header); nothing about it is live, and
        the phrase "two-system problem" now describes a closed problem
        wherever it appears in these ADRs.
- The resolver derives **torch family pins** so the chosen wheels and the
  env's torch agree.
- Network resilience: transient TCP resets are retried with a real
  User-Agent (`comfy-env/<version>`) because corporate proxies and AV
  products RST `Python-urllib`; if Pages is unreachable end-to-end, the
  resolver **falls back to the GitHub Releases API**, which sits on a
  different routing edge.

## Context

Modern CV/ML node packs depend on CUDA-compiled packages -- flash-attn,
nvdiffrast, nunchaku, pytorch3d, gsplat. Every such wheel is compiled for a
specific combination of Python ABI (3.10-3.13) x torch version (2.4-2.11) x
CUDA version x OS x GPU architecture. Expecting end users to have a CUDA
toolkit and C++ compiler and to build from source is a support disaster;
upstream projects publish wheels for only a fraction of the matrix.

## Consequences

- Users never need nvcc or MSVC; `install.py` just works on a clean machine.
- The wheel farm is an external dependency the maintainer must keep building
  as new torch/CUDA versions ship.
- Combination resolution must happen at install time on the target machine
  (it depends on the host GPU and torch), which is why detection
  (`detection/`) feeds the install pipeline.
- When no prebuilt wheel exists for a combination, install fails with an
  explicit report rather than a silent source build.

## The boundary: what may still compile on a user's machine

The promise this decision serves is **one-click**: install a node pack and it
just runs -- no build tools, no CUDA toolkit, no PhD in dependency
management. Worth being precise about what that rules out, because it is
narrower than "everything must be a download". Compilation can still happen
on the user's machine:

- plenty of small C++ extensions build from source in seconds, and pip
  handles them perfectly well
- an isolated env can deliver its own compiler toolchain through conda and
  use it like any other dependency
- a few packages **JIT their CUDA kernels at runtime by design** (gsplat...)

What is forbidden is the **user** doing toolchain setup, anything touching
the host environment, and above all **CUDA kernel builds**.

**One click installs.**

## The wheel farm's own decisions

This ADR covers comfy-env's *consumer-side* choice. The farm itself has its
own decision record series --
[cuda-wheels ADRs](../../cuda-wheels/adr/index.md): package configs,
release storage, index generation, version encoding, the build grid, CI
strategies, phantom combos, and the proposed upstream watcher.

## Direction

- The design is accelerator-agnostic in principle; ROCm wheels are planned
  as a separate **rocm-wheels** repo with its own index, mirroring
  cuda-wheels (backend detection already recognizes torch's `+rocm` tag,
  and the consumer's `WHEEL_INDEX_REGISTRY` maps backend -> index, so a
  second repo is one dict entry) -- blocked on the maintainer having no
  ROCm hardware to test on. Today only the CUDA wheels are compiled
  end-to-end.
- The preferred long-term shape would remove this machinery from comfy-env
  entirely: delegate resolution to conda and publish the prebuilt wheels to
  a conda channel. That is closed off because the PyTorch team publishes no
  conda packages -- ironic, given torch is the poster child for the native
  library problems (bundled libomp copies, import-order-sensitive loading)
  that conda exists to solve. Until torch resolves through conda, the index
  and the torch-family pinning logic stay here.
