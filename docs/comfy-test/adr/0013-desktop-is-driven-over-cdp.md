# ADR-0013: Desktop is driven over CDP and installed by git clone

**Status:** accepted (2026-08); the ComfyUI-Manager install route was
removed from the lanes in 2026-08 after it proved unable to install these
packs.

## Decision

> **The ComfyUI Desktop lane is tested as the Electron application it
> is:** download the real installer, launch the app, attach over the Chrome
> DevTools Protocol, and drive the first-run wizard, installation and
> workflow execution from inside it (`platforms/desktop/cdp_driver.py`).
> **The pack is installed by git clone + pip + `install.py`, executed inside
> the app's own environment -- not through ComfyUI-Manager.**

## Context

Desktop is not "ComfyUI in a venv with a window". It ships its own bundled
Python and torch, its own install location, a first-run wizard, and an
in-app Manager. A user's pack lands there through a different mechanism than
on a server install, so testing the server path proves nothing about
Desktop.

The obvious install route was the Manager's CLI (`cm_cli install <repo>`),
and the lanes were built that way first. It did not work for these packs:
`comfyui_manager` imports `from comfy.cli_args import args` at module load,
and `comfy` is not a site-packages module in Desktop -- it lives inside the
bundled app resources, on a `PYTHONPATH` the app sets at launch and a
standalone `cm_cli` process does not. Working around that meant
reconstructing the app's own environment from outside it, and the result
still failed to install the packs.

What does work is doing from *inside* the app what a developer does in their
own Desktop terminal: clone the repo into `custom_nodes`, pip install its
requirements, run its `install.py`, restart, execute the workflows. That is
what the CDP driver does, capturing the terminal output as video along the
way.

## Alternatives rejected

- **ComfyUI-Manager CLI.** Tried, shipped behind a flag, then deleted:
  cannot import ComfyUI from outside the bundled app. (The dead steps were
  removed from both desktop lanes in 2026-08 because "disabled while we
  iterate" wrongly implied pending work.)
- **Treating Desktop as another venv lane.** Would test a configuration
  no Desktop user has, and skip the wizard, the bundled interpreter and the
  app's own install path -- i.e. everything specific to Desktop.
- **Skipping Desktop.** It is a first-class distribution channel; packs
  break there specifically.

## Consequences

- **Desktop lanes install the pack in a different way than a Desktop user
  running Manager would.** They match the developer/git route. Manager
  installation remains untested, and a pack that installs by git but not by
  Manager would pass here.
- The driver is one large injected script running in the app's Python, so it
  cannot import comfy-test; shared helpers (e.g. run provenance) are
  mirrored there deliberately.
- `provenance.install_mode` is `desktop`, and `torch_version` is reported as
  *observed* rather than pinned -- the app brings its own
  ([ADR-0005](0005-pinned-torch-random-python.md) does not apply).
- The lanes emit a synthetic `system` entry in `results.json` so the report
  renders a card for the boot/terminal capture. It sits alongside real
  workflow results; anything aggregating `workflows[]` should be aware it is
  a display slot, not a test verdict.
- CDP is also what makes Desktop capture work
  ([ADR-0010](0010-capture-drives-a-real-browser.md)) without a separate
  browser install -- the app *is* the browser.
