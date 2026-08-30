# `linux-cuda`

> The reference GPU lane. Self-hosted, containerised, and a **fresh** install --
> the one that actually proves your pack installs and runs on a GPU.

| | |
|---|---|
| **OS / accelerator** | Linux / CUDA |
| **Install method** | `manual` -- venv + a ComfyUI checkout |
| **Runner** | `[self-hosted, linux, cuda]` |
| **Install path** | **fresh** |
| **Config key** | `[test.linux_cuda]` |
| **Dispatch only** | yes -- not in the hosted matrix |

## What a green cell proves

Everything `linux-cpu` proves, plus: the environment was built from nothing on
this run. comfy-test created the venv, resolved and installed the
[torch triple](../torch-triple.md) against the CUDA wheel index, cloned
ComfyUI, installed your `requirements.txt` and ran your `install.py`, then
booted the server. `provenance.install_mode` records `fresh`.

It is also the only Linux lane where your `cuda` workflow list runs at all.

## How it runs

Inside a container via `comfy-test docker run`, with the NVIDIA Container
Toolkit providing GPU access. The container is disposable, so a pack that
corrupts its environment cannot poison the next run.

Because it is dispatch-only, it is not part of `test-matrix.yml`. It is
triggered through `dispatch-test.yml` -- pass the lane, not the deprecated
`platform` alias:

```yaml
with:
  lane: linux-cuda
```

## Gotchas

- **Wheels must exist for the resolved torch.** A CUDA package pinned to a
  torch the index does not carry aborts at config parse with a message naming
  the missing package -- deliberately, before a venv is built.
- **Self-hosted means shared.** Runs are serialised on the GPU; a queue here is
  normal, not a hang.

## See also

- [Lanes](../lanes.md) -- the GPU lanes and their isolation model
- [torch, torchvision and torchaudio](../torch-triple.md) -- what gets pinned
