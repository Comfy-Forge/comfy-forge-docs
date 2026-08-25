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

| id | OS | Backend | Kind | Runner | Install path |
|----|----|---------|------|--------|--------------|
| `linux-cpu` | linux | cpu | server | hosted | **attach** |
| `linux-cuda` | linux | cuda | server | self-hosted | fresh |
| `windows-cpu` | windows | cpu | server | hosted | **attach** |
| `windows-cuda` | windows | cuda | server | self-hosted | fresh |
| `windows-portable-cpu` | windows | cpu | portable | hosted | **attach** |
| `windows-portable-cuda` | windows | cuda | portable | self-hosted | fresh |
| `macos-cpu` | macos | cpu | server | hosted | **attach** |
| `macos-desktop` | macos | cpu | desktop | hosted | desktop |
| `windows-desktop` | windows | cpu | desktop | hosted | desktop |
| `windows-desktop-cuda` | windows | cuda | desktop | self-hosted (VM) | desktop |

Select them explicitly -- listing is opt-in and an unknown token is a hard
error ([ADR-0008](adr/0008-lanes-are-opt-in.md)):

```toml
[test.lanes]
lanes = ["linux-cpu", "windows-cuda", "macos-desktop"]
```

## Kinds

- **`server`** -- a normal ComfyUI checkout in a virtual environment. The
  reference configuration.
- **`portable`** -- the Windows portable bundle: an embedded Python with no
  `.git`, unpacked rather than installed. Catches packs that assume a
  writable site-packages or a git checkout.
- **`desktop`** -- the Electron application, driven over CDP and installed by
  git clone rather than through Manager
  ([ADR-0013](adr/0013-desktop-is-driven-over-cdp.md)).

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

## GPU lanes

CUDA lanes are dispatch-only and run on self-hosted hardware. Three host
mechanisms exist, chosen by what the lane needs:

- **docker** -- Linux CUDA. Cheapest and most reproducible.
- **VM (Hyper-V)** -- `windows-desktop-cuda`. A container cannot run an
  interactive Electron app with a GPU attached, so the lane provisions a VM
  with device assignment.
- **Windows Sandbox** -- lightweight disposable Windows runs where a full VM
  is unnecessary.

These are operator concerns; the `comfy-test docker|vm|sandbox` subcommands
manage their lifecycles.

## Runtime level per lane

Lanes pick their terminal level with `--level`: macOS passes
`execution_light` because the capture loop kills the Playwright pipe on a
7 GB runner; Linux and Windows pass `execution`
([ADR-0011](adr/0011-execution-light-is-a-level.md)). One config file
serves all of them because `--level` *replaces* the terminal rather than
truncating ([ADR-0012](adr/0012-level-flag-swaps-terminals.md)).
