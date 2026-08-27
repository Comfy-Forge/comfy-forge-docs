# `install()`

```python
# install.py
from comfy_env import install; install()
```

The **build-time** entry point, called once when a pack is installed or updated.

**Three things happen, in order:**

(1) peer packs named in `[node_packs]` are
installed, *if the config declares any*;

(2) every sibling pack is scanned for
stale `comfy-env` pins, *always*;

(3) every isolated env declared anywhere under
`custom_nodes/` is built or refreshed. **Only (3) is slow**, but if the isolated envs had already been built it exits without touching the network.

## When it runs

In the standard install path, ComfyUI-Manager pip-installs the pack's
`requirements.txt` and then executes its `install.py`.
A user cloning by hand should do the same.

Of the [three calls](index.md#the-three-call-contract), this is the **only** one
that does network and disk work. `install()` is the sole builder of isolated
envs: nothing materializes one at runtime.

A missing env means [`register_nodes()`](register-nodes.md) falls back to in-process import for that
pack, and stays that way until `install()` is successfully ran.

## What `install()` does

```mermaid
flowchart TD
    entry["install()"]
    entry --> cfg["load the pack's config"]
    cfg --> nrq{"declares [node_packs]?"}
    nrq -->|"yes"| peers["install peer packs"]
    nrq -->|"no"| pins
    peers --> pins["scan sibling requirements.txt files for stale comfy-env pins, warn if problematic pins found"]
    pins --> found{"ComfyUI base dir found?"}
    found -->|"no"| warn["warn, skip the workspace"]
    found -->|"yes"| ws["build the workspace"]
```

### 1. Peer packs from `[node_packs]`

*Runs only if the config declares `[node_packs]`;
every accepted spelling for requirements is tabulated in the
[config reference](config.md#node_packs-every-spelling-the-code-accepts)).

Peer node packs are cloned from GitHub or downloaded from the Comfy Registry, then their own
`requirements.txt` and `install.py` run.

The pack's own `requirements.txt` is then re-run in the main env. just to ensure that the main pack's comfy-env is the right version.
This re-run exists because a peer pins its **own** comfy-env version and may have downgraded ours; reinstalling
reasserts this pack's pin ([ADR-0022](adr/0022-comfy-env-placement-in-host-env.md), the sibling-pin
hazard).

A peer that is not itself comfy-env'd installs its dependencies straight into
the shared host env. That is permitted today and [tracked as a
direction](../roadmap.md) to close.

### 2. Stale sibling pin check

*Always runs* (`install/__init__.py:84`).
Every sibling `requirements.txt` under `custom_nodes/` is scanned for `comfy-env` pins
that would downgrade the installed version:

| Pin form | Flagged? |
|---|---|
| `comfy-env==0.3.9` | yes |
| `comfy-env<=0.4.0`, `<0.4` | yes |
| `comfy-env>=…`, `~=…`, unpinned | no |

```
[comfy-env] WARNING: ComfyUI-OldPack/requirements.txt pins 'comfy-env==0.3.9'
but comfy-env 0.4.12 is installed. If that pack reinstalls its requirements,
comfy-env will be DOWNGRADED for every pack -- update ComfyUI-OldPack (or
relax its pin).
```
Warn-only; never fails an install.

### 3. The workspace build (`install_workspace()`)

### Bootstrap and discovery

- We run `ensure_pixi()` **first**
- Discovery then walks `custom_nodes/` for bindable configs (comfy-env.toml files).
- Three things are skipped silently, and one is fatal:
    - directories prefixed `.` or `_`, and those suffixed `.disabled` / `._disabled`
  (the quarantine convention) -- skipped;
    - configs outside `nodes/comfy-env.toml` or `nodes/<subdir>/comfy-env.toml` --
  invisible, deliberately, because the runtime binder can only bind those two
  shapes;
    - a config that does not parse -- skipped, and reported in a batch at the end;
    - **two configs deriving the same env name -- `ValueError`**, because they would
  share one env directory and rebuild over each other forever
  (`workspace.py:226`).

### The skip gate

Two hashes decide whether any environments are rebuilt:

- A cheap **fast key** over inputs (does this machine have a GPU? Is the cpu aarch64 or x86? what is comfy-env.toml saying?)
- A precise **identity** over what those inputs derive to.
The full mechanism, including why a version bump rebuilds nothing, is
[The three seals](seals.md).

### Torch pin vs wheel combo

In this following paragraph, **pin** is used to refer to the (cuda × torch × python) **combo** that *ComfyUI itself runs*.

Usually the [cuda-wheels index](../cuda-wheels/index.md) has every needed wheel
for the host's own *pin* and we can match it perfectly.

When any of the cuda packages is not yet built for
the host combo (imagine we are using CUDA 13.0, have [cumesh, flash-attn, spconv] as cuda packages in comfy-env.toml and we only have cumesh and flash-attn for CUDA 13.0) the **requested combo** for the cuda wheels drops to a known-good fallback cell.

The fallback is **per CPU architecture**:
- `cu12.8 / torch 2.8` on x86_64
- `cu13.0 / torch 2.10` on linux aarch64.

The reasoning is a bit long but can be summarised as follows: very few people use torch aarch64, but if they have a CUDA GPU it is likely to be a DGX Spark or some other late model, while if someone is on x86_64 there's a higher chance they might want Volta/Turing compatibility if they have an old GPU.

!!! warning "No GPU means CPU torch, whatever the host's torch says"
    Portable ComfyUI ships `torch+cu128` inside `python_embeded` even on
    machines with no NVIDIA driver. GPU presence therefore **overrides** the
    torch build (`workspace.py:83-88`): with no GPU detected, envs pin **CPU
    torch** and `[cuda]` packages are not resolved or installed at all.

### Building each env

The work is **phase-major, not env-major**: every env goes through a phase
before any env goes through the next.

- Manifests are written for each env

- All installs runs

- All stamps are produced

- All hash files are produced

That ordering is deliberate and produces three behaviours worth knowing:

- **One `pixi install` per manifest**, so a broken manifest cannot poison another
  env's scan or install.
- **`pixi` failures are collected and raised at the end** (`workspace.py:856`),
  so one run surfaces *every* broken env rather than stopping at the first.
- **Hash files are written last** (`workspace.py:966`), after that raise point.
  So if any env fails, the run leaves no hash bookkeeping for the envs that
  succeeded alongside it, and they are re-derived next time.

The CUDA wheels are **inside** the generated manifest, as direct-URL
pypi-dependencies (0.4.31): they land in `pixi.lock`.

## When it fails

| What | Result |
|---|---|
| ComfyUI base dir not found | workspace skipped, warning; steps 1-2 stand |
| A config does not parse | that pack skipped, batch-reported, install continues |
| Two configs derive one env name | **`ValueError`** -- rename a directory |
| No cuda-wheels combo covers a package | **`RuntimeError`** naming package + index URL |
| `pixi install` fails for an env | collected; **`RuntimeError`** at the end listing every failure |
| An env ends up unbuilt | marked `[MISSING]` at next launch by [`setup_env()`](setup-env.md); ComfyUI still boots and those nodes fall back to in-process import at [`register_nodes()`](register-nodes.md) time ([ADR-0008](adr/0008-graceful-degradation-everywhere.md)) |

**Start here when debugging:** every workspace install tees its full output to
`<workspace>/install.log` (`workspace.py:680`), including the discovery list,
the resolved combo, and each `pixi install` invocation.