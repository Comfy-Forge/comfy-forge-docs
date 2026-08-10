# `install()`

```python
# install.py
from comfy_env import install; install()
```

The **build-time** entry point. It runs when the pack is installed or
updated -- ComfyUI-Manager executes `install.py` after pip-installing
`requirements.txt`, or the user runs `python install.py` by hand. It is the
only one of the three calls that touches the network or writes to disk
outside the pack.

Source: `src/comfy_env/install/__init__.py:39`.

## Signature

```python
def install(
    config: str | Path | None = None,     # explicit config path; default: discover
    node_dir: Path | None = None,         # default: the CALLER's directory
    log_callback: Callable | None = None, # default: print
    dry_run: bool = False,
) -> bool
```

With no arguments it figures out everything itself: `node_dir` is inferred
from the caller's file via `inspect.stack()` -- which is how the one-liner in
`install.py` works -- and the config is discovered by looking for
`comfy-env-root.toml` / `comfy-env.toml` in that directory. No config file
at all raises `FileNotFoundError`.

Importing the module also sets `PYTHONUNBUFFERED=1` and switches
stdout/stderr to line buffering, so install output streams live even when
ComfyUI-Manager pipes it.

## What it does

Two halves, in order:

### 1. Plugin half (main environment)

Only runs if the config declares `[node_reqs]`:

- **Install node dependencies** (`install/plugin.py`) -- other ComfyUI packs
  this one depends on, cloned from GitHub (git, zip fallback) or downloaded
  from the Comfy Registry into `custom_nodes/`, then their
  `requirements.txt` and `install.py` are run.
- **Re-run this pack's own `requirements.txt`** in the main env --
  installing a peer pack may have downgraded or clobbered shared deps.

### Stale comfy-env pin check (always runs)

Independent of `[node_reqs]`, every install scans the *sibling* packs'
`requirements.txt` files under `custom_nodes/` for comfy-env pins that would
downgrade the installed version -- an exact pin like `comfy-env==0.3.9`, or
an upper bound below it (`<=0.4.0`, `<0.4`). Each hit produces a warning
(`check_sibling_comfy_env_pins` in `install/plugin.py`):

```
[comfy-env] WARNING: ComfyUI-OldPack/requirements.txt pins 'comfy-env==0.3.9'
but comfy-env 0.4.12 is installed. If that pack reinstalls its requirements,
comfy-env will be DOWNGRADED for every pack -- update ComfyUI-OldPack (or
relax its pin).
```

Why: the shared main env has exactly one comfy-env, and whichever pack
reinstalls its requirements last wins -- a stale pin in *any* pack silently
downgrades comfy-env for every pack on that pack's next update. The check is
warn-only (`>=`, unpinned, and `~=` pins are fine and not flagged) and never
fails an install.

### 2. Workspace half (isolated environments)

Gated on the `COMFY_ENV_INSTALL_ISOLATED` flag (default **on**;
overridable per-node via `[settings]`). Locates the ComfyUI base dir from
the node's position, then `install_workspace()` (`install/workspace.py`):

1. **Discover** every `comfy-env.toml` under `custom_nodes` -- all packs,
   not just the calling one; the workspace is shared.
2. **Resolve the torch pin** from the host env (CPU-only build when no GPU
   is present) and pick a CUDA wheel combo the
   [cuda-wheels index](../cuda-wheels/index.md) can satisfy -- exact host
   combo first, known-good fallback second.
3. **Generate one `pixi.toml` per env** (`packages/toml_generator.py`) with
   the torch pin replicated verbatim and CUDA wheels inlined as direct URLs.
4. **Hash each env's config** -- unchanged envs are skipped entirely.
5. **`pixi install --manifest-path envs/<name>/pixi.toml`**, one invocation
   per env, so a broken manifest cannot poison the others
   ([ADR-0007](adr/0007-machine-wide-workspace-with-per-env-manifests.md)).
6. **Stamp the env** (Python ABI + comfy-env version + torch pin) so later
   launches can detect staleness, then dedupe macOS libomp copies.

The pixi binary itself is bootstrapped to `~/.pixi/bin/` on first use --
`pip install comfy-env` is the only prerequisite
([ADR-0002](adr/0002-pixi-as-environment-manager.md)).

## Failure behavior

If the ComfyUI base directory cannot be located, the workspace half is
skipped with a warning -- the plugin half's work stands. A failed env
install leaves that env `[MISSING]` (reported at every startup by
[`setup_env()`](setup-env.md)); ComfyUI still boots and the affected nodes
fall back to in-process import at
[`register_nodes()`](register-nodes.md) time
([ADR-0008](adr/0008-graceful-degradation-everywhere.md)).
