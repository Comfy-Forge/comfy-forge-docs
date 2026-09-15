# `install()`

```python
# install.py
from comfy_env import install; install()
```

*The build-time entry point, called once when a pack is installed or updated.*
{: .subtitle }

In the standard install path, ComfyUI-Manager pip-installs the pack's
`requirements.txt` and then executes its `install.py`. A user cloning by hand
should do the same.

Of the [three calls](index.md#the-three-call-contract), this is the **only** one
that does network and disk work. `install()` is the sole builder of isolated
envs: nothing materializes one at runtime. A missing env means
[`register_nodes()`](register-nodes.md) falls back to in-process import for that
pack, and stays that way until `install()` is run successfully.

If an install has already failed and you are here to find out why, skip to
[When it fails](#when-it-fails).

## What `install()` does

**Two things happen, in order:**

(1) peer packs named in `[node_packs]` are
installed, *if the config declares any*;

(2) every isolated env declared anywhere under
`custom_nodes/` is built or refreshed. **Only (2) is slow**, but if the isolated envs had already been built it exits without touching the network.

## 1. Peer packs from `[node_packs]`

*Runs only if the config declares `[node_packs]`; every accepted spelling is
tabulated in the [config reference](config.md#node_packs).*

Peer nodepacks are cloned from GitHub or downloaded from the Comfy Registry, then
their own `install.py` runs.

**Their `requirements.txt` is not installed.** A peer is required to be
comfy-envved ([ADR-0016](adr/0016-node-pack-dependencies.md)), so its
dependencies belong in its own isolated env, and its `requirements.txt` should
name nothing but `comfy-env`. comfy-env will not pip-install on another pack's
behalf into the environment it exists to keep clean. A peer that is not
comfy-envved is cloned but will not load, which is the visible failure rather
than the silent one.

The peer's `install.py` still runs, and nothing stops it pip-installing into
the host env from there. That route is open and [tracked as a
direction](../roadmap.md) to close.

## 2. The workspace build (`install_workspace()`)

*Runs only if the ComfyUI base directory can be located; if it cannot,
`install()` warns and skips the workspace entirely, leaving no envs built.*

### Where the envs land

The workspace is **machine-wide**, not per install, so two ComfyUI installs
using the same pack share one materialized env
([ADR-0007](adr/0007-machine-wide-workspace-with-per-env-manifests.md)).

| | Workspace root |
|---|---|
| **Windows** | `%LOCALAPPDATA%\Programs\comfy-env` |
| **Linux / macOS** | `~/.ce` |

`COMFY_ENV_ROOT` moves it. On Windows the root is deliberately *not* at a
drive root, as something like `C:\ce` would need admin to create.

One directory per env, under `envs/`:

```
<root>/envs/<env-name>_<abi-tag>/          the manifest (pixi.toml, pixi.lock)
<root>/envs/<env-name>_<abi-tag>/.pixi/envs/default/   the materialized env
```

**The seam is an underscore**, and it is the only one in the name
([ADR-0039](adr/0039-env-directory-naming.md)). Split on it and you have the
two halves; nothing else in either half can be an underscore.

* **`<env-name>`** is the pack directory, `ComfyUI-` / `ComfyUI_` prefix
  stripped and lowercased, plus `-<subdir>` when the config is not at the
  pack root. Anything outside `[a-z0-9-]` collapses to a single dash, because
  this half comes from a folder name on disk and pixi rejects the rest.
* **`<abi-tag>`** is `py<version>-torch<major>.<minor>-<backend>`, where
  backend is `cu128`, `rocm63`, `mps` or `cpu`. **Version dots are kept**,
  so torch 2.10 reads `torch2.10` and cannot be misread as torch 2 build 10.

!!! warning "Run `install.py` with the ComfyUI's python, or it refuses"
    The tag describes the **interpreter that runs the install**, and that
    interpreter is assumed to be the ComfyUI's. Every worker env gets the
    host's torch whether or not the pack declares one, because the worker
    has to `import comfy` to stand in for a node and `comfy` imports torch
    on line one; and it must be the *same* torch, because tensors cross the
    process boundary through torch's private sharing ABI and a mismatch
    corrupts them rather than failing.

    So a Python **without** torch is never a ComfyUI's -- ComfyUI imports
    torch unconditionally, and a CPU-only ComfyUI still has a CPU torch.
    Running `install.py` from one used to build an env keyed
    `py3XX-notorch`, stamped for a host with no torch, which no real host is:
    a gigabyte of the wrong torch that nothing could ever bind. `install()`
    now refuses before touching disk and names the interpreter it was run
    with. `comfy-env info` from such a python still reports the tag as
    `notorch`, which is the diagnosis.

The tag is what stops two ComfyUI installs on different stacks from sharing
a directory and rebuilding over each other. It also means **the same pack
can hold several copies at once**, one per stack it has been installed
under:

```
geometrypack-nodes_py310-torch2.10-cpu
geometrypack-nodes_py311-torch2.10-cpu
geometrypack-nodes_py313-torch2.8-cu128
```

[`comfy-env gc`](commands.md#comfy-env-gc) is a command that can be used to delete envs under `<root>/envs/` that no
installed pack references ([ADR-0028](adr/0028-workspace-disk-lifecycle.md)).
Full disk layout, including the pixi package cache that `COMFY_ENV_ROOT`
does **not** move, is in [Drives and volumes](drives-and-volumes.md).

### Bootstrap and discovery

- We run `ensure_pixi()` **first**
- Discovery then walks `custom_nodes/` for bindable configs (comfy-env.toml files).
- Three things are skipped silently, and one is fatal:
    - directories prefixed `.` or `_`, and those suffixed `.disabled` / `._disabled`
  (the quarantine convention) -- skipped;
    - configs outside `nodes/comfy-env.toml` or `nodes/<subdir>/comfy-env.toml` --
  invisible, deliberately, because the runtime binder can only bind those two
  shapes;
    - a config that does not parse -- skipped, warned inline as the scan hits it,
  then listed again as a batch as soon as discovery finishes (before the skip
  gate, not at the end of the install);
    - **two configs deriving the same env name -- `ValueError`**, because they would
  share one env directory and rebuild over each other forever
  (`workspace.py`).

### The skip gate

Two hashes decide whether any environments are rebuilt:

- A cheap **fast key** over inputs (does this machine have a GPU? Is the cpu aarch64 or x86? what is comfy-env.toml saying?)
- A precise **identity** over what those inputs derive to.
The full mechanism, including why a version bump rebuilds nothing, is
[The three seals](seals.md).

### Which combo the envs get

Usually the [cuda-wheels index](../cuda-wheels/index.md) has every needed wheel
for the **host combo** -- the (cuda × torch × python) triple ComfyUI itself
runs -- and we can match it perfectly.

When any of the cuda packages is not yet built for
the host combo (imagine we are using CUDA 13.0, have [cumesh, flash-attn, spconv] as cuda packages in comfy-env.toml and we only have cumesh and flash-attn for CUDA 13.0) the **requested combo** for the cuda wheels drops to a known-good fallback cell.

The fallback is **per CPU architecture**:

- `cu12.8 / torch 2.8` on x86_64
- `cu13.0 / torch 2.10` on linux aarch64.

ARM needs its own cell because `(12.8, 2.8)` has no aarch64 wheels at all; the
full argument is [Why ARM gets its own fallback
cell](../cuda-wheels/coverage.md#why-arm-gets-its-own-fallback-cell).

The CUDA wheels are **inside** the generated manifest, as direct-URL
pypi-dependencies: they land in `pixi.lock`.

!!! warning "No NVIDIA GPU means the CPU wheel index, whatever the host's torch says"
    Portable ComfyUI ships `torch+cu128` inside `python_embeded` even on
    machines with no NVIDIA driver. NVIDIA GPU presence therefore
    **overrides** the torch build (`workspace.py`): with none detected,
    envs resolve torch from the CPU index and `[cuda]` packages are not
    resolved or installed at all.

    **This is a Linux and Windows rule and does not apply to macOS.** Darwin
    never reaches the CUDA-index choice at all (`workspace.py`); macOS
    torch comes from ordinary PyPI, and **those wheels have MPS compiled in**
    (`detection/backend.py`). There is no separate MPS build to pick
    and nothing to opt into: a Mac with MPS available is detected as backend
    `mps`, so its envs are tagged `-mps` and never share a directory with a
    genuinely CPU-only machine's.

### Building each env

The work is **phase-major, not env-major**: every env goes through a phase
before any env goes through the next.

- Manifests are written for each env

- All installs run

- All stamps are produced

- All hash files are produced

That ordering is deliberate and produces three behaviours worth knowing:

- **One `pixi install` per manifest**, so a broken manifest cannot poison another
  env's scan or install.
- **`pixi` failures are collected and raised at the end** (`workspace.py`),
  so one run surfaces *every* broken env rather than stopping at the first.
- **Hash files are written last** (`workspace.py`), after that raise point.
  So if any env fails, the run leaves no hash bookkeeping for the envs that
  succeeded alongside it, and they are re-derived next time.

## When it fails

**Start here:** a workspace install that does any work tees its output to
**one `install.log` per env**, beside that env's `pixi.toml`:
`<workspace>/envs/<name>_<abi>/install.log`. Each holds the shared preamble
(the discovery list, the resolved combo) followed by that env's own
`pixi install` invocation with its output, stamp and identity.

!!! warning "The log is from the last run that did work"
    A run where every env is already current never opens any env's
    section, so no file is touched. After a clean run each env's file on
    disk is the transcript of the last install that rebuilt **that env**,
    and its header timestamp is the tell. To force
    a fresh one, delete an env's `install.hash` -- which is what the skip
    message itself tells you to do.

Because failures are batched and raised at the end, one log names *every*
broken env rather than stopping at the first. Re-run with:

```
comfy-env install --dir custom_nodes/<pack>
```

Two failures produce no envs and no `pixi` output at all: the ComfyUI base
directory could not be located (see section 2), or two configs derived the
same env name and raised a `ValueError` before any build started.
