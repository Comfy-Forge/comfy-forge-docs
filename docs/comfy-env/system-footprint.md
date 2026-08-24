# What comfy-env leaves on your system

A common and reasonable question: if I delete the ComfyUI installation I
put comfy-env in, is everything gone? **Not quite** -- and the reason is a
deliberate design choice, not an oversight. This page states exactly what
persists, why, and how to remove it if you want to.

## What is never touched

Verified directly against the source (no registry APIs, service APIs, or
scheduled-task APIs appear anywhere in the codebase):

| Never touched | |
|---|---|
| Windows registry | comfy-env writes nothing to it. |
| Services / scheduled tasks | None are created. |
| System or user `PATH` | Never modified persistently -- only set inside the environment dict of subprocesses comfy-env launches itself. |
| Your own pixi install (`~/.pixi`) | comfy-env deliberately installs its own pixi to a separate, comfy-env-owned path so it can never collide with or upgrade a pixi you installed yourself (`pixi.py`). |

## What persists outside the ComfyUI folder, and why

| Location | What it is | Why it survives deleting a ComfyUI install |
|---|---|---|
| `%LOCALAPPDATA%\Programs\comfy-env` (Windows) / `~/.ce` (macOS/Linux) | The **workspace**: every materialized isolated environment (interpreters, conda packages, wheels). Can be multi-GB to tens of GB. | **Deliberately machine-wide**, not install-specific. If two ComfyUI installs on your machine use the same node pack, they share one materialized environment instead of each downloading and building their own copy ([ADR-0007](adr/0007-machine-wide-workspace-with-per-env-manifests.md)). Tying it to one install's lifecycle would break that sharing for every other install on the machine. |
| `~/.comfy-env/pixi/<version>/` | The pixi binary comfy-env uses. | Pinned and sha256-verified per version; shared by every isolated environment rather than duplicated per env. Tens of MB. |
| `~/.comfy-env/settings.env`, `~/.comfy-env/debug.env` | Your saved preferences from the `comfy-env settings` / `comfy-env debug` TUIs. | **Only exist if you explicitly ran those commands.** Nothing in the install or startup path writes them; a default install never creates these files. |
| `<workspace>/install.log` | The full transcript of every workspace install: the discovery list, the resolved (cuda x torch x python) combo, and each `pixi install` invocation with its output. Overwritten per run. | It is the first place to look when an env did not build. Kept beside the workspace rather than in the ComfyUI folder because the workspace is machine-wide and shared between installs (`workspace.py:680`). |
| *(Windows only)* one line inside `platform.py` in uv's own Python cache (`%APPDATA%\uv\python\cpython-*\Lib\platform.py` or `%LOCALAPPDATA%\rattler\cache\python\...`) | A compatibility patch, applied on every Windows workspace install. | conda-forge's Python build embeds an extra string in `sys.version` that breaks the standard library's own `platform.py` parser, crashing `setuptools`. comfy-env applies the same one-line regex fix conda-forge ships in their own builds, in place, to whichever interpreter uv/pixi already cached there. The file isn't created by or exclusive to comfy-env -- it's uv's shared cache, used by any uv-based tool on the machine -- and the patch only *adds* an optional match, so it cannot break anything that worked before. |

## Removing everything

1. Delete the ComfyUI installation as normal (removes comfy-env's own
   package files, which live inside that install's Python environment,
   like any other pip package).
2. Delete the workspace: `%LOCALAPPDATA%\Programs\comfy-env` (Windows) or
   `~/.ce` (macOS/Linux) -- or a custom path if you set `COMFY_ENV_ROOT`.
   **Check first if any other ComfyUI install on the machine still uses
   it** -- deleting it will force those installs to re-materialize their
   environments from scratch.
3. Delete `~/.comfy-env/` (the pixi binary and, if you used them, the
   settings/debug files).

The uv `platform.py` patch is safe to leave -- it is a one-line, purely
additive regex change to a shared cache file, not something scoped to any
one ComfyUI install.

There is currently no single `comfy-env uninstall` command that does steps
2-3 for you; this is a manual (but short and complete) recipe.
