# `linux-cpu`

> The cheapest lane and the one most runs start from. A ComfyUI checkout in a
> venv on a GitHub-hosted Ubuntu runner -- and an **attach** lane, so it proves
> your pack *runs*, not that it *installs*.

| | |
|---|---|
| **OS / accelerator** | Linux / CPU |
| **Install method** | `manual` -- venv + a ComfyUI checkout |
| **Runner** | `ubuntu-latest` (GitHub-hosted, 1x billing) |
| **Install path** | **attach** |
| **Config key** | `[test.linux]` |
| **Also accepts** | `linux`, `linux_cpu` |

## What a green cell proves

That your pack imports, registers its nodes, and executes its CPU workflows
against a real ComfyUI server on Linux. For most packs this is the single most
informative lane per minute spent.

## What it does not prove

**That your pack installs.** The workflow builds the venv, clones ComfyUI and
installs your requirements in YAML behind a cache, then hands comfy-test a
running server via `--server-url`. The [`install`](../levels/install.md) level
does almost nothing, and `provenance.install_mode` records `attach`.

The cache key is only (lane, Python version), so ComfyUI and the torch family
stay frozen at whatever HEAD first populated it until GitHub evicts. This lane
therefore does **not** exercise the pin from
[ADR-0005](../adr/0005-pinned-torch-random-python.md) -- see
[torch, torchvision and torchaudio](../torch-triple.md).

For proof of installability, run `linux-cuda`, or run comfy-test locally: both
take the fresh path.

## Gotchas

- **Case sensitivity.** Linux is the lane that catches `import MyNodes` when
  the file is `mynodes.py`. Windows and macOS will not.
- **No GPU.** Anything gated on `torch.cuda.is_available()` silently does not
  run here. That is what [`syntax`](../levels/syntax.md) is for -- it fails
  hardcoded `.cuda()` before a server is ever started.

## See also

- [Lanes](../lanes.md) -- all ten, and what attach costs you
- [`linux-cuda`](linux-cuda.md) -- the same lane, fresh, with a GPU
