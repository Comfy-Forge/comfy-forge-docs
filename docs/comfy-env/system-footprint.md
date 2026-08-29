# What comfy-env leaves on your system

FAQ: if I delete the ComfyUI installation I put comfy-env in, is everything gone?

The answer is: **Not quite**, and the reason is a
deliberate design choice, not an oversight.

This page states exactly what persists, why, and how to remove it if you want to.

## What is never touched

Verified directly against the source. No OS-integration API appears anywhere
in the codebase -- comfy-env installs no persistent hooks on any platform:

| Never touched | |
|---|---|
| Windows registry | Nothing is written to it. |
| Shell startup files | `.bashrc`, `.zshrc`, `.profile` and friends are never edited. |
| Services, daemons, scheduled tasks | None are created -- no Windows services or scheduled tasks, no systemd units (user or system), no launchd agents, no cron entries. |
| Anything outside your user account | Nothing is written to `/etc`, `/usr`, `Program Files`, or any location needing root/admin. comfy-env never asks for elevation. |
| Desktop / autostart integration | No XDG autostart entries, `.desktop` files, dconf/gsettings keys, or login items. |
| Your shell's environment, incl. `PATH` | No OS-level environment store is written -- not the registry, not `/etc/environment`, not a shell profile. `PATH` is only ever set on the environment dict of a subprocess comfy-env launches itself (`subenv.py`), which dies with it. See the note below for the settings comfy-env *does* persist. |
| Your own pixi install (`~/.pixi`) | comfy-env deliberately installs its own pixi to a separate, comfy-env-owned path so it can never collide with or upgrade a pixi you installed yourself (`pixi.py`). |

!!! note "One caveat on environment variables"
    comfy-env keeps its own settings in `~/.comfy-env/settings.env` and
    `~/.comfy-env/debug.env`, written by `comfy-env settings`. On **every**
    import it reads those files and pushes their keys into `os.environ`
    (`settings.py`, `debug.py`), so a setting you saved months ago is applied
    on the next launch and inherited by every subprocess started afterwards.

    Nothing about your shell changes -- open a terminal and `env` looks exactly
    as it did. But the effect is persistent, and it is env-var shaped, so it is
    worth knowing about rather than filing under "never touched". Deleting
    `~/.comfy-env/` (step 3 of every removal recipe above) removes it.

## What persists outside the ComfyUI folder, and why

| Location | What it is | Why it survives deleting a ComfyUI install |
|---|---|---|
| `%LOCALAPPDATA%\Programs\comfy-env` (Windows) / `~/.ce` (macOS/Linux) | The **workspace**: every materialized isolated environment (interpreters, conda packages, wheels). Can be multi-GB to tens of GB. | **Deliberately machine-wide**, not install-specific. If two ComfyUI installs on your machine use the same node pack, they share one materialized environment instead of each downloading and building their own copy ([ADR-0007](adr/0007-machine-wide-workspace-with-per-env-manifests.md)). Tying it to one install's lifecycle would break that sharing for every other install on the machine. |
| `~/.comfy-env/pixi/<version>/` | The pixi binary comfy-env uses. | Pinned and sha256-verified per version; shared by every isolated environment rather than duplicated per env. Tens of MB. |
| `~/.comfy-env/settings.env`, `~/.comfy-env/debug.env` | Your saved preferences from the `comfy-env settings` TUI (one tab per file). | **Only exist if you explicitly saved from it.** Nothing in the install or startup path writes them; a default install never creates these files. |
| `<workspace>/install.log` | The full transcript of every workspace install: the discovery list, the resolved (cuda x torch x python) combo, and each `pixi install` invocation with its output. Overwritten per run. | It is the first place to look when an env did not build. Kept beside the workspace rather than in the ComfyUI folder because the workspace is machine-wide and shared between installs (`workspace.py:680`). |
| *(Windows only)* one line inside `platform.py` in uv's own Python cache (`%APPDATA%\uv\python\cpython-*\Lib\platform.py` or `%LOCALAPPDATA%\rattler\cache\python\...`) | A compatibility patch, applied on every Windows workspace install. | conda-forge's Python build embeds an extra string in `sys.version` that breaks the standard library's own `platform.py` parser, crashing `setuptools`. comfy-env applies the same one-line regex fix conda-forge ships in their own builds, in place, to whichever interpreter uv/pixi already cached there. The file isn't created by or exclusive to comfy-env -- it's uv's shared cache, used by any uv-based tool on the machine -- and the patch only *adds* an optional match, so it cannot break anything that worked before. |

## Removing everything

Two things live outside the ComfyUI install, and only one of them is large.

**Step 1 is the same everywhere: delete the ComfyUI installation as normal.**
That removes comfy-env's own package files, which live inside that install's
Python environment like any other pip package.

!!! warning "The workspace is shared machine-wide"
    Step 2 below deletes the workspace, which is deliberately shared across
    every ComfyUI install on the machine so two installs on the same stack
    reuse one materialized env. **Check no other install still needs it** --
    deleting it forces those installs to re-materialize from scratch.

In all three recipes, step 2 is the multi-GB one: every materialized env, its
`pixi.toml` / `pixi.lock`, and each env's `.metadata_cache.pkl`. Step 3 is a
few tens of MB. If you set `COMFY_ENV_ROOT`, that path replaces the step-2
location.

### Removing everything (Windows)

2. **`%LOCALAPPDATA%\Programs\comfy-env`** -- the workspace.
3. **`%USERPROFILE%\.comfy-env\`** -- the pinned pixi binary, plus
   `settings.env` and `debug.env` if you ever opened `comfy-env settings`.

Optional leftovers:

- `%TEMP%\comfyui_pvenv_*`, `%TEMP%\comfy_worker_*.sock` and the
  `comfy_worker_*.log` files. Reaped automatically at the next launch, so they
  only matter if there will not be one.
- `C:\ce` -- the pre-0.4 workspace location, present only if you upgraded from
  a version that used it. comfy-env prints a notice when it finds one.
- The **uv `platform.py` patch** is safe to leave: a one-line, purely additive
  regex change under `%APPDATA%\uv\python\cpython-*\Lib\platform.py` (and
  the rattler cache equivalent) that teaches a shared interpreter to parse
  conda-forge version strings. It belongs to no single ComfyUI install -- and
  it is **Windows-only**, so it has no equivalent in the two recipes below.

### Removing everything (macOS)

2. **`~/.ce`** -- the workspace.
3. **`~/.comfy-env/`** -- the pinned pixi binary, plus `settings.env` and
   `debug.env` if you ever opened `comfy-env settings`.

Optional leftovers:

- `$TMPDIR/comfyui_pvenv_*`, `$TMPDIR/comfy_worker_*.sock` and the
  `comfy_worker_*.log` files. Reaped automatically at the next launch.
- **`libomp.dylib.bak` files.** macOS is the only platform where comfy-env
  rewrites files inside an already-installed environment: the
  [libomp dedupe](setup-env.md) symlinks redundant copies to torch's and
  renames the original aside. Copies inside the workspace go with step 2, but
  the prestartup pass also runs against **ComfyUI's own site-packages**, so
  those go with step 1.

### Removing everything (Linux)

2. **`~/.ce`** -- the workspace.
3. **`~/.comfy-env/`** -- the pinned pixi binary, plus `settings.env` and
   `debug.env` if you ever opened `comfy-env settings`.

Optional leftovers:

- `$TMPDIR/comfyui_pvenv_*` and the `comfy_worker_*.log` files. Reaped
  automatically at the next launch.
- **No socket files to clean.** Linux binds worker sockets in the abstract
  namespace, which is kernel-only and leaves nothing on disk. The `/dev/shm`
  blocks used for tensor transport are unlinked per call and do not survive the
  process.

There is no `comfy-env uninstall` command that does steps 2-3 for you; this is
a manual, but short and complete, recipe.
