# Using comfy-test

To use comfy-test, the **only** file you need to add to your node pack is **`comfy-test.toml`** (see [`comfy-test.toml` reference](config.md)). With just that, `comfy-test run` works.

Everything else is optional and depends on how you use it:

- **`comfy-test.toml`** *(required)* -- the config
  ([full reference](config.md)).

- **An example workflow** *(optional, only to test execution)*: a minimal
  ComfyUI workflow using your nodes, exported from ComfyUI.

  comfy-test looks for json workflows in the same canonical folders (workflows/, example_workflows/...) that ComfyUI does.

## What a pack looks like

Only `comfy-test.toml` is required. Everything else here you probably already
have:

```text
ComfyUI-YourPack/
├── comfy-test.toml            <- the only file comfy-test requires
├── pyproject.toml                [project] name -- also the JS namespace
├── requirements.txt              your deps (and any --extra-index-url)
├── install.py                    optional; run at install time
├── __init__.py                   NODE_CLASS_MAPPINGS
├── nodes/                        your node classes
├── web/                          frontend JS, if any (the `javascript` level)
├── example_workflows/         <- workflows: docs for users, tests for you
│   ├── basic.json
│   ├── upscale.json
│   └── tests/                    dev-only workflows, not shown to users
│       └── regression.json
└── .github/
    └── workflows/
        └── test-install.yml   <- one line; only for the CI paths
```

`example_workflows/` is the canonical name, and four aliases are accepted --
see [below](#what-you-need-to-add). A `tests/` subfolder inside it holds
workflows you want exercised but not advertised.

## The four ways

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
comfy-test run                          # the pack in the current directory
comfy-test run ../ComfyUI-MyPack        # any local directory, used as-is
comfy-test run owner/repo               # GitHub shorthand -- shallow-cloned
comfy-test run https://github.com/…     # any git URL -- shallow-cloned
comfy-test run owner/repo --branch dev  # remote forms take --branch
```

An existing local directory is used in place and never cloned, so `--branch`
is **rejected** for it -- the branch is detected from the checkout instead.
(Passing it would have filed the results under a branch whose code was not
what ran.) The directory must contain an `__init__.py`, which is checked
before any environment is built. Private repos work: the clone URL picks up `NODE_PAT` /
`GH_TOKEN` / `GITHUB_TOKEN` when set, and the un-tokenised URL is what gets
logged, so the PAT never reaches CI output.

Everything is built on your machine and results land under your logs directory
([`comfy-test paths`](commands.md#comfy-test-paths) shows where). This is the
**fresh** install path, so a green run genuinely means "this installs."

You can share results from a local run with
[`comfy-test publish`](commands.md#comfy-test-publish), which pushes them to a
repo's `gh-pages` exactly as CI would.

### 2. Self-serve CI

Add one workflow file to your pack, then turn on a `gh-pages` branch to serve
the results as a website.

**1. Add the workflow file.** That is the whole thing -- it calls comfy-test's
reusable workflow, so there is nothing to keep in sync:

```yaml title=".github/workflows/test-install.yml"
name: Workflow Tests
on: [push, pull_request]

jobs:
  test:
    uses: PozzettiAndrea/comfy-test/.github/workflows/test-matrix.yml@main
```

That is the only addition to [the layout above](#what-a-pack-looks-like):

```text
ComfyUI-YourPack/
├── comfy-test.toml
├── example_workflows/
└── .github/
    └── workflows/
        └── test-install.yml   <- new
```

**2. Let Actions write to your repo.** Settings -> Actions -> General ->
Workflow permissions -> **Read and write permissions**. Without this the
publish step fails with a 403 when it tries to push.

**3. Push, and let it run once.** You do **not** create the `gh-pages` branch
by hand -- the first publish creates it if it is missing.

**4. Point Pages at the branch.** Settings -> Pages -> Source:
**Deploy from a branch**, branch `gh-pages`, folder `/ (root)`. Your dashboard
is then live at `https://<owner>.github.io/<repo>/`.

!!! warning "Choose 'Deploy from a branch', not 'GitHub Actions'"
    comfy-test pushes finished HTML to the branch, so Pages should serve that
    branch directly. The "GitHub Actions" source expects a Pages *build*
    workflow, which comfy-test does not provide -- pick it and your dashboard
    silently never appears.

That reusable workflow fans out the **GitHub CPU lanes** on push or PR, reads
your `comfy-test.toml` to decide which lanes to run, and publishes to your
repo's `gh-pages`.

You do not configure it further -- which lanes run comes from your
`comfy-test.toml`, not from the workflow call. The reusable workflow does take
a few inputs, but they exist for the dispatcher case below (calling it on
behalf of *another* repo) and for packs keeping their config off the default
path; a consumer repo should not need any of them.

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

## How the workflows fit together

The heavy lifting all lives in the Python package; the GitHub workflows are
thin wrappers around it.

| Workflow | Role |
|---|---|
| `test-matrix.yml` | The reusable workflow consumer repos call. Fans out the hosted CPU lanes on push/PR. |
| `dispatch-test.yml` | One reusable workflow for *every* lane, including the self-hosted GPU ones. Test jobs `pip install --upgrade comfy-test`, invoke `comfy-test run`, and upload the results artifact. |
| [comfy-ci](https://github.com/PozzettiAndrea/comfy-ci) | A thin dispatcher repo whose only job is to be the entry point self-hosted GPU runners are enrolled against. Its `test-cpu.yml` / `test-cuda.yml` are `workflow_dispatch` shims calling `dispatch-test.yml` by tag. |

In `dispatch-test.yml`, publishing is a **separate job** from testing, so a
flaky push to gh-pages can be re-run without repeating the slow test
([ADR-0015](adr/0015-publish-is-a-separate-job.md)).

Each one's inputs, and the internal `_test-*.yml` files behind them, are in
the [GitHub workflows reference](workflows.md).

## Which lanes exist

Every mode draws from the same lane table -- ten lanes across three
ComfyUI installation types and three operating systems. See
[Lanes](lanes.md).
