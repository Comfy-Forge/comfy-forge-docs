# comfy-test

[comfy-test](https://github.com/PozzettiAndrea/comfy-test) is **installation
and execution testing infrastructure for ComfyUI custom node packs**.

It does four things, the way a real user would:

1. **Installs the node pack on every OS ComfyUI runs on**: Windows, Linux, macOS.

2. **Across every officially supported ComfyUI installation type:**

    - **git-cloned ComfyUI** (all three) -- a fresh venv with a real server (`main.py`)
    - the **portable bundle** (Windows only) -- the official embedded-Python build
    - the **Desktop app** (Windows and macOS) -- the Electron app, driven over CDP

3. **Drives real workflows against it** -- queues actual workflow JSONs on the
   running server and checks it actually executes.

4. **Produces a results gallery** -- an HTML report with a per-lane video
   of each workflow running, plus memory and performance logs (RAM, VRAM, CPU,
   CUDA).

In this way, `example_workflows/` or `workflows/` folders become both
documentation and testing.

![The results gallery for GeometryPack: per-lane tabs, each workflow card
carrying a video of the run plus RAM and VRAM logs](img/test_gallery_example_geometrypack.png)

## Intended uses

The package has been created to help ComfyUI node pack creators test their nodes on different hardware/operating systems/installation types.

comfy-test can be pointed at a custom nodepack in **four different ways**.

| Intent | Who it's for | Who drives | Results land |
|---|---|---|---|
| **Local / offline** | a developer with **one node** | `comfy-test run <NODEPACK>` in a terminal | on the machine -- with a `--publish` workflow to share them to your GitHub repo |
| **Self-serve CI** | a developer with **one node** | automated GitHub Actions on a repo | your repo's gh-pages |
| **Central dispatcher ([comfy-ci](https://github.com/PozzettiAndrea/comfy-ci))** | a developer with **several nodes** who has local GitHub runners and doesn't want to open a GitHub org (GitHub won't let you register the same GPU machine to multiple repos) | automated GitHub Actions on a central repo with write access to the node repos | pushed back to **each node's own repo** |
| **Registry gate (comfy-forge)** | the **comfy-forge** registry | the registry, on ingest | kept by the registry as a **verdict / badge** |

Each is expanded, along with what you need to add to your pack, in
[Using comfy-test](using.md). For the anatomy of a single run --
what gets built, what lands on disk, and which checks run -- see
[What a run does](what-a-run-does.md).

## Accelerators

comfy-test aims to cover all the OSes ComfyUI users run, for all installation types and for all avaialble accelerators!

This means that we want to allow node creators to test their nodes on and across CUDA/ROCm/MPS/XPU accelerators eventually.
Currently we support only CUDA because the maintainer only has CUDA GPUs.

Therefore the following lanes are available as of 0.5.0:

| Install method | Linux | macOS | Windows |
|---|---|---|---|
| **Server** (git-cloned ComfyUI) | CPU · CUDA | CPU (MPS) | CPU · CUDA |
| **Portable** (embedded Python bundle) | X | X | CPU · CUDA |
| **Desktop** (Electron app) | X | CPU | CPU · CUDA |

Ten lanes in all.
See [Lanes](lanes.md) for the full per-lane breakdown.

## Commands besides `comfy-test run`

Besides the basic "comfy-test run" command, there's a few more commands like "publish" or "lint".
You can find the full reference here: [commands reference](commands.md).

## The lane matrix

The single source of truth is `lanes/registry.py`: a lane is an
**(os x accelerator x install method)** target.

| id | install method | runs on |
|----|------|---------|
| linux-cpu, windows-cpu, macos-cpu | server | GitHub-hosted runners |
| windows-portable-cpu | portable | GitHub-hosted |
| windows-desktop, macos-desktop | desktop | GitHub-hosted |
| linux-cuda, windows-cuda | server | self-hosted, docker |
| windows-portable-cuda | portable | self-hosted, docker |
| windows-desktop-cuda | desktop | self-hosted, Hyper-V VM / Sandbox |

(`rocm` is a valid backend token in the registry; no runner is wired yet. No
macos-cuda -- Apple Silicon has no CUDA.)

The three **kinds** differ substantially:

- **server** -- uv venv, cloned ComfyUI, `main.py --listen` (macOS omits
  `--cpu` so MPS gets picked).
- **portable** -- downloads and 7z-extracts the official Windows portable
  bundle and tests against its `python_embeded` interpreter.
- **desktop** -- installs the actual ComfyUI Desktop (Electron) app, launches
  it with a remote-debugging port, and drives the real UI over CDP with
  Playwright -- including installing the node through the in-app Manager.

## GPU lanes: docker, VM, and Sandbox

CUDA testing needs real GPUs, so those lanes are dispatch-only on
self-hosted runners:

- **linux-cuda / windows-cuda / windows-portable-cuda** run inside
  containers (`comfy-test docker run`): NVIDIA Container Toolkit on Linux,
  process-isolation with GPU device mapping on Windows.
- **windows-desktop-cuda** is the hard one: Electron needs an interactive
  desktop session *and* CUDA -- Windows containers can provide neither
  (Session 0 isolation; no `--device` under Hyper-V isolation). The answer
  is a **Hyper-V baseline VM** with the GPU DDA-attached: restore a clean
  snapshot, run the test via a GHA runner registered inside the VM, revert
  -- "same isolation contract as `docker run --rm`, ~60s overhead", the same
  pattern Comfy-Org's own desktop E2E tests use. The `comfy-test vm`
  subcommand formalizes the lifecycle: `build` (one-time host setup, optionally
  fully unattended Windows install), `snapshot`, `restore`, `gpu attach/detach`,
  `share` (SMB share that survives snapshot restores).
- **Windows Sandbox** (`comfy-test sandbox`) is the emerging successor for
  that lane: GPU-PV maps the host driver store into a pristine disposable
  guest -- no image build, no snapshots, no GPU dismount.

## CI architecture

The heavy lifting all lives in the Python package; the GitHub workflows are
thin:

- `test-matrix.yml` -- reusable workflow consumer repos call; fans out the
  hosted CPU lanes on push/PR.
- `dispatch-test.yml` -- one reusable workflow for *every* lane,
  including the self-hosted GPU lanes; test jobs just
  `pip install --upgrade comfy-test`, invoke `comfy-test run`, and upload
  the results artifact; a separate `publish` job pushes to gh-pages (so a
  flaky publish can be re-run without re-running the slow test).
- [comfy-ci](https://github.com/PozzettiAndrea/comfy-ci) -- a thin dispatcher
  repo whose only job is to be the entry point self-hosted GPU runners are
  enrolled against; its `test-cpu.yml` / `test-cuda.yml` are
  workflow_dispatch shims that call `dispatch-test.yml` by tag.

## Relationship to comfy-env

comfy-test knows about [comfy-env](../comfy-env/index.md) as an optional
peer, not a dependency: it reads `comfy-env.toml` for declared `[cuda]`
packages and `comfy-env-root.toml` for `[node_packs]`, sets
`COMFY_ENV_CUDA_VERSION` so wheel resolution works on CPU-only CI, probes
comfy-env's workspace to see which CUDA packages actually materialized (and
mocks the ones that didn't, since CUDA imports fail without a GPU), and logs
the installed comfy-env version alongside its own in every run.
