# Lanes

A **lane** is one combination of **operating system x accelerator x ComfyUI
install method** -- `linux-cuda`, `windows-portable-cpu`, `macos-desktop`. Ten
of them. One CI job runs one lane.

The taxonomy lives in `lanes/registry.py` and everything else derives from it
([ADR-0007](adr/0007-lane-registry-is-the-source-of-truth.md)).

!!! note "Why not `platform`"
    `platform` is reserved for what `sys.platform` and wheel tags
    (`win_amd64`, `manylinux_2_35_x86_64`) mean -- which is only *one
    component* of a lane id.

## The lanes

| id | OS | Accel | Install method | Runner | Install path |
|----|----|-------|----------------|--------|--------------|
| [`linux-cpu`](lanes/linux-cpu.md) | linux | cpu | manual | hosted | **attach** |
| [`linux-cuda`](lanes/linux-cuda.md) | linux | cuda | manual | self-hosted | fresh |
| [`windows-cpu`](lanes/windows-cpu.md) | windows | cpu | manual | hosted | **attach** |
| [`windows-cuda`](lanes/windows-cuda.md) | windows | cuda | manual | self-hosted | fresh |
| [`windows-portable-cpu`](lanes/windows-portable-cpu.md) | windows | cpu | portable | hosted | **attach** |
| [`windows-portable-cuda`](lanes/windows-portable-cuda.md) | windows | cuda | portable | self-hosted | fresh |
| [`macos-cpu`](lanes/macos-cpu.md) | macos | cpu | manual | hosted | **attach** |
| [`macos-desktop`](lanes/macos-desktop.md) | macos | cpu | desktop | hosted | desktop |
| [`windows-desktop`](lanes/windows-desktop.md) | windows | cpu | desktop | hosted | desktop |
| [`windows-desktop-cuda`](lanes/windows-desktop-cuda.md) | windows | cuda | desktop | self-hosted (VM) | desktop |

Select them explicitly -- listing is opt-in and an unknown token is a hard
error ([ADR-0008](adr/0008-lanes-are-opt-in.md)):

```toml
[test.lanes]
lanes = ["linux-cpu", "windows-cuda", "macos-desktop"]
```

## Install methods

These are ComfyUI's own three, under ComfyUI's own names -- the
[install docs](https://docs.comfy.org/installation/manual_install) list
Desktop, Windows Portable and Manual Install, and comfy-test tests one lane
family per option.

- **`manual`** -- what upstream calls a *manual install*: a virtual environment
  plus a ComfyUI checkout, which is its published four-step definition (venv,
  clone, dependencies, start). The reference configuration.
- **`portable`** -- the Windows portable bundle: an embedded Python with no
  `.git`, unpacked rather than installed. Catches packs that assume a
  writable site-packages or a git checkout.
- **`desktop`** -- the Electron application, driven over CDP and installed by
  git clone rather than through Manager
  ([ADR-0013](adr/0013-desktop-is-driven-over-cdp.md)).

!!! note "`manual` describes ComfyUI, not your pack"
    Two different things get installed in a run and both have a "manual"
    option in the wider ecosystem. This axis is **how ComfyUI itself got
    there**. How *your pack* gets installed is separate -- and today every
    lane installs it the same way, by git clone rather than through
    ComfyUI-Manager, including the Desktop one.

    Note also that `manual` says nothing about who typed the commands: CI does
    every step unattended, exactly as no runner carries the `portable` bundle
    on a USB stick. All three are inherited distribution names.

## What a green cell means -- read this one

Lanes differ in **how the environment was built**, and that changes the
claim a passing result makes.

- **attach** -- the lane prebuilt the venv, ComfyUI and your pack in YAML,
  behind a cache, and handed comfy-test a live server. The `install` level
  did essentially nothing. A green cell means *"your pack works in a
  prebuilt environment"*, **not** *"your pack installs cleanly."*
- **fresh** -- comfy-test built the venv, installed the torch triple, cloned
  ComfyUI, installed your pack, booted the server. A green cell here does
  mean it installs.
- **desktop** -- the app's own bundled Python; the pack is git-cloned and
  installed inside the app.

The mode is recorded per run in `results.json` as
`provenance.install_mode`. The reasoning is in
[ADR-0003](adr/0003-two-install-paths-attach-and-fresh.md).

Two consequences worth internalising:

1. The hosted cache key is only (lane, Python version). ComfyUI and the
   torch family stay frozen at whatever HEAD first populated it, until
   GitHub evicts. Attach lanes therefore do **not** exercise the torch pin
   from [ADR-0005](adr/0005-pinned-torch-random-python.md).
2. If you want proof of installability, run a CUDA/dispatch lane, or run
   comfy-test locally -- both take the fresh path.

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

These are operator concerns; the flags and subcommands are in the
[commands reference](commands.md#gpu-lane-operations).

## Runtime level per lane

The terminal level is the pack's choice, not the lane's: every lane runs what
`[test] levels` lists, and no lane overrides it
([ADR-0012](adr/0012-level-flag-swaps-terminals.md)).

`execution_light` exists for constrained runners -- the full capture loop can
kill the Playwright pipe on a 7 GB macOS runner
([ADR-0011](adr/0011-execution-light-is-a-level.md)). A pack that hits this
lists `execution_light` instead of `execution`; per-lane variation is
`skip_workflow` under `[test.<lane>]`.
