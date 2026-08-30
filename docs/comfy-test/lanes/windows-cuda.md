# `windows-cuda`

> Windows, GPU, fresh install. The lane that proves a CUDA pack installs on the
> platform most ComfyUI users are actually on.

| | |
|---|---|
| **OS / accelerator** | Windows / CUDA |
| **Install method** | `manual` -- venv + a ComfyUI checkout |
| **Runner** | `[self-hosted, windows, cuda]` |
| **Install path** | **fresh** |
| **Config key** | `[test.windows_cuda]` |
| **Dispatch only** | yes |

## What a green cell proves

The full fresh sequence on Windows: venv, [torch triple](../torch-triple.md)
from the CUDA index, ComfyUI clone, your requirements, your `install.py`,
server boot, and your `cuda` workflow list executed on a real GPU.

This is the combination most of your users run, and the one where a wheel that
exists on Linux but not Windows shows up.

## How it runs

In a Windows container with process isolation and GPU device mapping, via
`comfy-test docker run`. Windows containers cannot use `--device` under Hyper-V
isolation, so process isolation is what makes GPU passthrough possible here --
which in turn means the container shares the host kernel, so the host's driver
version and the image must agree.

## Gotchas

- **Driver/image coupling.** A host driver upgrade can break the image until it
  is rebuilt. This is the usual cause of a lane that was green last week.
- **Long paths.** Windows' 260-character limit bites deep dependency trees
  inside a container more often than on a developer machine.
- **Windows Defender.** Real-time scanning on the workspace slows installs
  significantly; comfy-test's docker path manages an exclusion for it.

## See also

- [Lanes](../lanes.md) -- the GPU lanes and their isolation model
- [`windows-desktop-cuda`](windows-desktop-cuda.md) -- why Electron needs a VM
  rather than a container
