# `macos-desktop`

> The ComfyUI Desktop app on macOS, driven over the Chrome DevTools Protocol.
> It tests the install path a non-technical user actually takes.

| | |
|---|---|
| **OS / accelerator** | macOS / CPU |
| **Install method** | `desktop` -- the Electron application |
| **Runner** | `macos-latest` (GitHub-hosted, 10x billing) |
| **Install path** | **desktop** -- the app's own bundled Python |
| **Config key** | `[test.macos_desktop]` |
| **Also accepts** | `macos-desktop`, `macos_desktop` |

## What makes this lane different

Everything. The other lanes start a server comfy-test controls; here an Electron
app spawns its own Python on a port it chooses, and comfy-test drives the real
UI over CDP ([ADR-0013](../adr/0013-desktop-is-driven-over-cdp.md)).

That means the run covers things no other lane touches: the first-run wizard,
the app's own install flow, and the frontend as a user sees it. Your pack is
installed by `git clone` into the app's `custom_nodes`, then its
`requirements.txt` and `install.py` run against the app's bundled interpreter --
not through ComfyUI-Manager.

## What a green cell proves

That your pack installs into the Desktop app's environment and its nodes load
and execute in the real UI on macOS. It says nothing about a venv install; that
is [`macos-cpu`](macos-cpu.md)'s job.

## Gotchas

- **No CUDA, by construction.** Same as `macos-cpu`.
- **The app moves under you.** Desktop ships on its own cadence, and a wizard
  layout change can break the automation before it breaks your pack. A failure
  here is worth confirming against `macos-cpu` before assuming it is yours.
- **`provenance` is thinner.** The desktop path runs outside the orchestrator,
  so some fields other lanes record are absent rather than null-by-choice.

## See also

- [`windows-desktop`](windows-desktop.md) -- the same app on Windows
- [ADR-0013](../adr/0013-desktop-is-driven-over-cdp.md) -- why CDP
