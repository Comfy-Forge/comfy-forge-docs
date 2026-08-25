# `comfy-test.toml` reference

The config file lives at the root of your node pack (custom_nodes/ComfyUI-MyPack/comfy-test.toml)

**Unknown keys are a hard error**.

## Minimal file

```toml
[test]
levels = ["syntax", "install", "registration", "execution"]

[test.lanes]
lanes = ["linux-cpu", "windows-cpu"]

[test.workflows]
cpu = "all"
```

## `[test]`

| Key | Type | Default | Meaning |
|-----|------|---------|---------|
| `levels` | list | see below | Which levels to run; a set, not a sequence ([levels](levels.md)). **The only thing that decides what a run does** -- no lane and no flag overrides it. Listing `execution` or `execution_light` on a pack with no workflows is an error. |
| `comfyui_version` | string | `"latest"` | ComfyUI ref to test against: `latest` (the default branch's HEAD), a tag, a branch, or a **full 40-character commit SHA**. The resolved version and commit are both recorded in results -- see [pinning to a commit](#pinning-comfyui-to-a-commit). |
| `python_version` | string *or* list | `"3.13"` | A single version pins it; **a list draws one at random per run**. `COMFY_TEST_PYTHON_VERSION` overrides both. Supported: 3.10, 3.11, 3.12, 3.13 -- anything else is a hard error ([ADR-0005](adr/0005-pinned-torch-random-python.md)). |
| `torch_version` | string | newest complete triple | Pins `torch` + the matching `torchvision`/`torchaudio`. A version, `"latest"` to opt out, or an explicit `"t/tv/ta"` triple. An unavailable triple aborts at config parse -- see [torch, torchvision and torchaudio](torch-triple.md). |
| `extra_pip_indices` | list | `[]` | Extra pip indexes for the **whole test venv**, added as `--extra-index-url` alongside the PyTorch index and pypi.org. For private mirrors and Artifactory proxies -- see [below](#where-to-declare-a-package-index). |
| `res` | int | `1080` | Capture resolution (viewport height) for screenshots and video. |
| `custom` | string | none | Path to a `run(ctx)` hook, **relative to your pack**, for the [`custom`](levels/custom.md) level. Setting it enables that level automatically. |

Default `levels`: `syntax`, `install`, `registration`, `instantiation`,
`static_capture`, `validation`, `execution`. The opt-in levels --
`coverage`, `warnings`, `hazards`, `javascript`, `execution_light` and
`custom` -- must be listed explicitly (except `custom`, above).

### Pinning ComfyUI to a commit

```toml
[test]
comfyui_version = "latest"                                     # default branch HEAD
comfyui_version = "v0.3.60"                                    # a tag
comfyui_version = "master"                                     # a branch
comfyui_version = "37ac9ff44ffd1e4cc4b481cee550ced67608ec3a"   # a commit
```

All four are fetched the same way and cost the same -- one commit over the
wire. A SHA is the only one of them that cannot move: ComfyUI's version string
bumps only on releases, so many different HEADs report the same
`0.33.0`, and `latest` is a different commit every day.

!!! warning "An abbreviated SHA is rejected"
    `comfyui_version = "37ac9ff"` fails at config parse. This is a git
    limitation, not a comfy-test one: abbreviations are expanded against
    objects you already have, and a fresh clone has none -- so the remote is
    asked for a ref literally named `37ac9ff`, which does not exist. Paste the
    full 40 characters.

    Six hex characters or fewer are treated as a branch name, since that is
    below the length git itself would accept as an abbreviation.

!!! note "Pinning ComfyUI does not pin its frontend"
    `comfyui_version` fixes the ComfyUI source tree. It does not by itself fix
    everything that tree installs at runtime -- see
    [ComfyUI versioning](comfyui-versioning.md).

### Where to declare a package index

Three files can point pip at an index, and they cover **different
environments**. Picking the wrong one produces a test that passes while real
installs fail.

| Declare it in | Applies to | Reaches your users? |
|---|---|---|
| `requirements.txt` (`--extra-index-url` line) | ComfyUI's main venv | **yes** -- Manager installs this file on a real install |
| `comfy-env.toml` `[pypi-options] extra-index-urls` | comfy-env's isolated pixi envs | yes, on a real install |
| `comfy-test.toml` `extra_pip_indices` | the test venv only | **no** |

**Your pack's own dependencies belong in `requirements.txt`.** A
`--extra-index-url` line there is honoured by uv and by every real user
installing through ComfyUI-Manager. Putting that index only in
`comfy-test.toml` means comfy-test resolves the dependency and your users
cannot -- a green run that proves the opposite of what it looks like.

`extra_pip_indices` exists for the installs that **cannot read a
`requirements.txt`**, and it is the only lever for them:

- the **pinned torch triple**, installed before any requirements file exists
- **ComfyUI's own `requirements.txt`**, which is not yours to edit
- **peer packs** pulled in via comfy-env's `[node_packs]`, which are other
  repositories' files

That is why it is described as infrastructure: a mirror for the whole
environment, not a place to declare where your package comes from.

!!! note "macOS resolves pack requirements differently"
    The macOS lane overrides the index-routed install path with plain uv, so
    extra indexes may not reach your pack's own requirements there. Verify on
    that lane before depending on it.

### Choosing interpreters

```toml
[test]
python_version = "3.12"                    # pin one
python_version = ["3.10", "3.13"]          # draw one at random per run
```

The default is a **fixed 3.13**, not a draw. An unpinned random interpreter
meant a re-run could go green with no fix -- the single most confusing
behaviour the tool had. Widening is now a deliberate act: give a list and you
opt into the variance, and `provenance.python_version` records which one ran.

A list is the right choice for a nightly or dispatch lane, where sampling the
matrix over many runs is the point. Pin a single version for pre-merge CI,
where a reproducible red is worth more than coverage.

!!! note "There is no `name` key"
    Your pack is always identified by its **directory name** as installed
    under `custom_nodes/` -- `config_file.py` sets it from the directory
    unconditionally and never reads a `name` from the TOML.

    This is deliberate: ComfyUI itself identifies a pack by that directory, so
    a config-supplied name could only ever disagree with reality. Older
    examples showed `name = "..."`; it does nothing, and can be deleted.

!!! note "`[test] timeout` is not read"
    Per-workflow timeout is `[test.workflows] timeout`; the `[test]` key of the
    same name is a setup timeout that the parser does not consult.

## `[test.lanes]`

```toml
[test.lanes]
lanes = ["linux-cpu", "windows-cuda", "macos-desktop"]
```

An allowlist of ids from the [lane table](lanes.md); unknown tokens are
an error, and there are no per-lane booleans
([ADR-0008](adr/0008-lanes-are-opt-in.md)).

Per-lane overrides use the lane's config key:

```toml
[test.windows_portable]
comfyui_portable_version = "v0.3.60"
skip_workflow = true
```

| Key | Type | Default | Meaning |
|-----|------|---------|---------|
| `enabled` | bool | `true` | Off switch for a listed lane. |
| `skip_workflow` | bool | `false` | Run the pipeline but not the workflows. The supported way to keep `execution` in `levels` while a particular lane runs none. |
| `comfyui_portable_version` | string | none | Pin the portable bundle (portable kinds only). |

## `[test.workflows]`

```toml
[test.workflows]
cpu = { exclude = ["heavy"] }   # selection is always per accelerator
cuda = "all"
timeout = 3600
```

| Key | Type | Default | Meaning |
|-----|------|---------|---------|
| `cpu` / `cuda` / `rocm` | `"all"`, list, or `{ exclude = [...] }` | `[]` | Which workflows run on that accelerator. |
| `timeout` | int | `3600` | Per-workflow timeout, seconds. |

!!! warning "There is no `workflows` key"
    Selection is **always** per accelerator. Workflows themselves are
    auto-discovered from the folders ComfyUI recognises -- there is no key that
    lists them. A `workflows = [...]` entry is an unknown key and aborts the
    run before anything is built.

    The `gpu` key does not exist either, and is specifically diagnosed in the
    error message: it is the typo behind
    [ADR-0006](adr/0006-config-is-a-hard-fail-allowlist.md).

### Selecting workflows

Three forms, per accelerator:

```toml
[test.workflows]
cuda = "all"                              # everything discovered
cpu  = ["basic", "upscale"]               # exactly these two
cpu  = { exclude = ["heavy_sdxl"] }       # everything except these
```

The `.json` suffix is optional -- `"basic"` and `"basic.json"` are the same
thing.

The usual shape is a CUDA lane running the lot and a CPU lane skipping what
needs a GPU:

```toml
[test.workflows]
cuda = "all"
cpu  = { exclude = ["flux_full", "video_interpolation", "sdxl_refiner"] }
timeout = 3600
```

!!! warning "`!name` was removed -- there is one way to exclude"
    The older per-item spelling is now a hard error:

    ```toml
    cpu = ["basic", "!heavy"]   # rejected
    ```

    It looked like "run basic, skip heavy" and never meant that: a single `!`
    entry switched the whole list to *everything except*, the include was
    dropped on the floor, and you got **every** workflow on a CPU lane.

    A table cannot express that mistake -- a selection names either what to run
    or what to skip, never both. The error prints the replacement for you.

An **empty list means nothing runs** on that accelerator -- it is not a synonym
for `"all"`. But omitting *both* `cpu` and `cuda` is different again: with
neither configured the skip filter is disabled entirely and **every discovered
workflow runs**.

## `[test.coverage]`

```toml
[test.coverage]
inputs = { GeomPackLoadMesh = { file_path = "3d/cube.glb" } }
```

Declares inputs so the `coverage` level can account for nodes that need
values to be exercised.

!!! note "There is no `[test.javascript]` section"
    The [`javascript`](levels/javascript.md) level takes no configuration. Your
    pack gets **one** namespace, derived from its published identity in
    `pyproject.toml` -- `[tool.comfy] DisplayName` if present, otherwise
    `[project] name` with any `comfyui-` prefix stripped. Nothing is declared in
    `comfy-test.toml`.

    A pack that ships several namespaces (usually vendored JS that kept its old
    prefix) must rename that JS. There is no longer an escape hatch for it
    ([ADR-0014](adr/0014-javascript-isolation-is-static.md)).
