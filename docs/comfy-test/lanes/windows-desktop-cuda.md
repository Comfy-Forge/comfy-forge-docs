# `windows-desktop-cuda`

> Electron plus CUDA on Windows -- the hardest lane to isolate, and the reason
> comfy-test grew a VM subcommand.

| | |
|---|---|
| **OS / accelerator** | Windows / CUDA |
| **Install method** | `desktop` -- the Electron application |
| **Runner** | `[self-hosted, windows, cuda, vm]` |
| **Install path** | **desktop** -- the app's own bundled Python |
| **Config key** | `[test.windows_desktop_cuda]` |
| **Dispatch only** | yes |

## Why it needs a virtual machine

Electron needs an interactive desktop session *and* CUDA, and a Windows
container can provide neither:

- **Process isolation** has no Session 0 desktop, so Chromium cannot open a
  window.
- **Hyper-V isolation** does not accept `--device`, so the GPU cannot be
  passed through.
- **Container GPU passthrough** on Windows is DirectX/DirectML only -- no CUDA.

So the isolation primitive is a **Hyper-V baseline VM** with the GPU attached
via DDA: restore a clean snapshot, run the test through a GHA runner registered
inside the VM, revert. Roughly a minute of overhead for the same contract as
`docker run --rm`, and the same pattern Comfy-Org's own desktop E2E tests use.

`comfy-test vm` formalises the lifecycle -- `build` (one-time host setup,
optionally a fully unattended Windows install), `snapshot`, `restore`,
`gpu attach` / `detach`, and `share` (an SMB share that survives snapshot
restores).

**Windows Sandbox** (`comfy-test sandbox`) is the emerging successor: GPU-PV
maps the host driver store into a pristine disposable guest, with no image
build, no snapshots and no GPU dismount.

## Gotchas

- **This lane has no reusable workflow of its own.** `dispatch-test.yml`
  carries an inline `desktop_cuda` job instead;
  `_test-windows-desktop-cuda.yml` is its superseded pre-VM ancestor, kept for
  reference and called by nothing.
- **Snapshot discipline is the whole guarantee.** A run that does not revert
  leaves the next one testing a dirty machine.

## See also

- [Lanes](../lanes.md) -- the GPU lanes and their isolation model
- [Commands](../commands.md) -- the `vm` and `sandbox` subcommands
