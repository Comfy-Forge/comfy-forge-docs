# `windows-desktop`

> The ComfyUI Desktop app on Windows -- the combination the largest number of
> non-technical ComfyUI users are running.

| | |
|---|---|
| **OS / accelerator** | Windows / CPU |
| **Install method** | `desktop` -- the Electron application |
| **Runner** | `windows-latest` (GitHub-hosted, 2x billing) |
| **Install path** | **desktop** -- the app's own bundled Python |
| **Config key** | `[test.windows_desktop]` |
| **Also accepts** | `windows-desktop`, `windows_desktop` |

## What a green cell proves

That your pack git-clones into the Desktop app on Windows, installs against its
bundled Python, and its nodes load and run in the real UI.

This is the lane that catches the intersection nothing else does: Windows path
and encoding constraints *and* the app's bundled environment *and* the
frontend. A pack can pass `windows-cpu` and fail here purely because the app's
Python has a different set of preinstalled packages.

## Gotchas

- **CPU only.** For the GPU version of this lane see
  [`windows-desktop-cuda`](windows-desktop-cuda.md), which needs a VM rather
  than a container.
- **Electron plus Windows is the flakiest combination in the matrix.** It is
  driving a GUI it does not own, over a debug protocol, on the OS with the most
  timing-sensitive file behaviour. Confirm a failure against `windows-cpu`
  before assuming your pack is at fault.

## See also

- [`macos-desktop`](macos-desktop.md) -- the same app, different OS
- [`windows-cpu`](windows-cpu.md) -- Windows without the app
