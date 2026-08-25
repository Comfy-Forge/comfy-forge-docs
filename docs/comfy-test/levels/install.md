# `install`

> Builds the environment a real user would have: a ComfyUI, a Python, your
> pack inside `custom_nodes/`, and everything they depend on.

| | |
|---|---|
| **Needs** | nothing -- it is the **provider** of the `env` resource |
| **Default** | yes |
| **Fails the run** | yes |
| **Source** | `orchestration/levels/install.py` |

Every level above `syntax`, `coverage`, `warnings` and `hazards` depends on
this one, directly or transitively -- it is what `env` means in the
[resource model](../levels.md).

## Two paths

The level takes one of two completely different routes, and **which one ran
changes what a green result proves**:

- **fresh** (`ctx.server_url` unset) -- `_setup_full()` clones ComfyUI, builds
  the venv or unpacks the portable bundle, installs your pack, then installs
  its dependencies.
- **attach** (`--server-url` given) -- CI already built everything and runs
  comfy-test from inside `custom_nodes/<pack>/`. Paths are *derived* from that
  layout rather than built: `node_dir.parent` is `custom_nodes/`, its parent is
  ComfyUI. Nothing is installed.

In attach mode this level does almost nothing, so a passing run says *"your
pack works in a prebuilt environment"*, not *"your pack installs cleanly."*
The mode is recorded in `provenance.install_mode`. See
[ADR-0003](../adr/0003-two-install-paths-attach-and-fresh.md).

## What the fresh path does

1. **`setup_comfyui()`** -- per-lane: git-cloned lanes make a venv and
   clone ComfyUI; portable extracts the official bundle and uses its
   `python_embeded`. See [Lanes](../lanes.md).
2. **`install_node()`** -- copies your pack into `custom_nodes/`, then
   `requirements.txt`, then `install.py`.
3. **`_install_node_dependencies()`** -- clones any peer packs declared in
   comfy-env's `[node_packs]`.
4. **The validation helper** -- clones
   `PozzettiAndrea/ComfyUI-validate-endpoint` into every environment, because
   [`validation`](validation.md) needs it. This is a supply-chain fact
   ([ADR-0009](../adr/0009-a-helper-pack-is-injected.md)); note the clone is
   unpinned.

## CUDA packages and mocking

If your `comfy-env.toml` declares `[cuda] packages`, the level decides **per
package** whether to mock it -- based on whether the wheel is actually present
in the materialized pixi env, not on whether `--cuda` was passed. comfy-env
inlines cuda-wheel URLs when a GPU is detected and a combo resolves; on a
no-GPU host that resolution is skipped, so the wheels are absent and must be
mocked or node code crashes on `import flash_attn`.

!!! warning "Absence of an environment is not evidence of absence"
    If *no* materialized comfy-env environment is found at all, that is a
    resolution failure, not proof the wheels are missing -- and it is exactly
    how a stale `.ce` path once silently mocked everything for weeks. The level
    says so loudly rather than returning a wrong verdict. If you see that
    warning, the mocking decision below it is untrustworthy.

Mocking is earned by probing, never assumed:
[ADR-0004](../adr/0004-mocking-is-earned-by-probing.md).

## What it records

The level reads ComfyUI's version and commit from the clone for the provenance
block. The portable bundle has no `.git`, so `comfyui_commit` is `None` there.
[`registration`](registration.md) later overrides the version with the running
server's own report, which is authoritative.

## Config

| Key | Effect |
|---|---|
| `comfyui_version` | ref to clone. **Tags and branches only** -- `--depth 1 --branch` cannot take a SHA |
| `python_version` | interpreter; drawn at random from 3.10-3.13 if unset |
| `extra_pip_indices` | appended as `--extra-index-url` |

## See also

- [The ladder](../levels.md) -- all 13 levels and the resource model
- [Lanes](../lanes.md) -- what each lane builds
- [Reproducibility](../reproducibility.md) -- what is pinned and what floats
