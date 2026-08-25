# Using comfy-test

What you need to adopt it, and the four different ways people run it.

## What you need to add

The **only** file you need to add to your node pack is **`comfy-test.toml`** --
every key it accepts is in the [`comfy-test.toml` reference](config.md). With
just that, `comfy-test run` works.

Everything else is optional and depends on how you use it:

- **`comfy-test.toml`** *(required)* -- the config
  ([full reference](config.md)).

- **An example workflow** *(optional -- only to test execution)* -- a minimal
  ComfyUI workflow using your nodes, exported from ComfyUI. Skip it if you only
  test install and registration.

    comfy-test looks in the same folders ComfyUI itself does, so a pack that
    already ships example workflows needs no new directory:

    | Folder | |
    |---|---|
    | `example_workflows/` | **canonical** -- the name ComfyUI recommends |
    | `example/`, `examples/`, `workflow/`, `workflows/` | tolerated aliases |

    That list is copied verbatim from core's `example_workflow_folder_names`
    (`app/custom_node_manager.py`) -- the same glob ComfyUI uses to build
    `/workflow_templates`, so the workflows your users can load from the
    node-library menu are exactly the ones comfy-test runs. A `tests/`
    subfolder inside any of them holds dev-only workflows.

- **A CI workflow file** *(optional -- only for the CI paths)* -- a one-line
  `uses:` pointing at the reusable workflow. Not needed to run locally. Which
  one depends on which of the four ways below you are using.

## The four ways

The accelerator (CPU / CUDA / ...) is orthogonal to all of this. What actually
separates the four is **who drives the run, where the results land, and who
they are for**.

| | Who it's for | Who drives | Results land |
|---|---|---|---|
| **1. Local** | a developer with **one node** | you, in a terminal | on your machine |
| **2. Self-serve CI** | a developer with **one node** | GitHub Actions on your repo | your repo's `gh-pages` |
| **3. Central dispatcher** | a developer with **several nodes** and a GPU fleet | Actions on a central repo | pushed back to **each node's own repo** |
| **4. Registry gate** | the **comfy-forge** registry | the registry, on ingest | kept as a **verdict / badge** |

The first two are the same developer, offline versus in CI. The third is that
developer once they outgrow one repo. The fourth is a different thing entirely.

### 1. Local

```bash
comfy-test run                 # the pack in the current directory
comfy-test run owner/repo      # or any GitHub pack
```

Everything is built on your machine and results land under your logs directory
([`comfy-test paths`](commands.md#comfy-test-paths) shows where). This is the
**fresh** install path, so a green run genuinely means "this installs."

You can share results from a local run with
[`comfy-test publish`](commands.md#comfy-test-publish), which pushes them to a
repo's `gh-pages` exactly as CI would.

### 2. Self-serve CI

One line in `.github/workflows/test-install.yml`:

```yaml
uses: PozzettiAndrea/comfy-test/.github/workflows/test-matrix.yml@main
```

That reusable workflow fans out the **hosted CPU lanes** on push or PR, reads
your `comfy-test.toml` to decide which lanes to run, and publishes to your
repo's `gh-pages`.

It accepts `config-file`, `lane` (run just one), and `node_repo` /
`node_branch` for targeting a repo other than the caller.

!!! warning "Hosted CPU lanes attach; they do not install"
    These lanes prebuild the environment in YAML behind a cache and hand
    comfy-test a live server, so `install` is effectively a no-op and install
    errors are suppressed. A green cell means *"your pack works in a prebuilt
    environment"*, not *"your pack installs cleanly"* -- check
    `provenance.install_mode`. See
    [ADR-0003](adr/0003-two-install-paths-attach-and-fresh.md) and
    [Reproducibility](reproducibility.md).

### 3. Central dispatcher

Once you have several nodes and want one shared GPU fleet, per-repo runners
stop working: **GitHub will not register the same self-hosted machine to
multiple repositories**, and you may not want to open an org just for this.

[comfy-ci](https://github.com/PozzettiAndrea/comfy-ci) is a thin dispatcher
repo that exists to be the single place those runners are enrolled against. It
holds nothing but `test-cpu.yml` and `test-cuda.yml`, which are
`workflow_dispatch` shims calling comfy-test's `dispatch-test.yml` with a
`node_repo`, `branch` and `lane`.

The split it enables:

- **CPU tests** stay per-repo on GitHub-hosted runners -- free.
- **CUDA tests** run on the fleet, inside Docker containers for isolation, and
  the results are **pushed back to each node's own `gh-pages`** using a token
  with write access.

The node repo's PR gate then checks `gh-pages` for a passing result matching
the PR's HEAD commit ([`pr-gate.yml`](https://github.com/PozzettiAndrea/comfy-test)),
and promotes results from `dev` to `main` on merge. So a GPU test run on
someone else's hardware still gates your PR.

Unlike the hosted lanes, dispatch lanes take the **fresh** install path, so
they do prove installability.

### 4. Registry gate

A different thing entirely: here comfy-test is not a tool a developer runs but
a **gate the registry applies**. The comfy-forge registry runs it on ingest and
keeps the outcome as a verdict or badge attached to the pack, rather than
publishing a dashboard to anyone's repository.

The developer is not in the loop; the artifact is the verdict.

## Which lanes exist

Every mode draws from the same lane table -- ten lanes across three
ComfyUI installation types and three operating systems. See
[Lanes](lanes.md).
