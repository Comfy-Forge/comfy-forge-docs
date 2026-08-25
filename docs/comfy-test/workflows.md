# GitHub workflows

comfy-test ships **reusable workflows** you call from your own repo with a
one-line `uses:`. You do not copy them, and you do not vendor their contents --
you reference them, and they run comfy-test's own tested logic.

Three are meant for you to call. The rest are internal plumbing, named with a
leading underscore, and exist only because one reusable workflow cannot fan out
to per-lane jobs without them.

## The ones you call

### `test-matrix.yml` -- run the tests

```yaml
# .github/workflows/test-install.yml
name: Workflow Tests
on: [push, pull_request]

jobs:
  test:
    uses: PozzettiAndrea/comfy-test/.github/workflows/test-matrix.yml@main
```

That is the whole file. It fans out the **hosted CPU lanes** on push and PR,
reads your `comfy-test.toml` to decide which lanes to run, and publishes
results to your repo's `gh-pages`.

**Which lanes run comes from your config, not from the workflow call.** The
reusable workflow does take inputs, but a consumer repo should not need any of
them:

| Input | |
|---|---|
| `config-file` | path to `comfy-test.toml` if it is not at the repo root |
| `lane` | run only this lane. Debugging aid |
| `timeout-minutes` | job timeout, default 1440 |
| `node_repo` / `node_branch` | **dispatcher use only** -- test a *different* repo than the caller. Leave unset |

!!! warning "`node_repo` is not for consumers"
    It exists so a central dispatcher can run tests on behalf of another
    repository ([mode 3](using.md#3-central-dispatcher)). Setting it in your
    own repo points the config read, the skip check and the publish target at
    someone else's repo.

### `pr-gate.yml` -- block merges on a red run, promote on merge

```yaml
name: PR Gate
on:
  pull_request:
  push:
    branches: [main]

jobs:
  gate:
    uses: PozzettiAndrea/comfy-test/.github/workflows/pr-gate.yml@main
```

Two behaviours in one workflow:

- **on `pull_request`** -- checks `gh-pages` for a passing result matching the
  PR's HEAD commit. This is what lets a GPU run on self-hosted hardware gate a
  PR: the result is looked up, not re-run.
- **on `push` to the default branch** -- promotes the `dev` results to `main`
  and regenerates the index, so the badge and gallery reflect what shipped.

| Input | |
|---|---|
| `test-workflow-name` | name of the test workflow to verify, default `"Workflow Tests"` -- must match the `name:` in your test workflow |

### `dispatch-test.yml` -- one lane, on demand

The workflow every lane goes through, including the self-hosted GPU ones.
You call this directly only if you are running a **dispatcher**
([mode 3](using.md#3-central-dispatcher)); otherwise `test-matrix.yml` calls
it for you.

```yaml
jobs:
  cuda:
    uses: PozzettiAndrea/comfy-test/.github/workflows/dispatch-test.yml@v0.4.5
    with:
      node_repo: PozzettiAndrea/ComfyUI-SAM3
      branch: dev
      lane: linux-cuda
    secrets:
      NODE_PAT: ${{ secrets.NODE_PAT }}
```

| Input | |
|---|---|
| `node_repo` | **required** -- owner/repo to test |
| `lane` | **required** -- which lane. `platform` is accepted as a deprecated alias |
| `branch` | node branch, default `dev` |
| `NODE_PAT` *(secret)* | write access to push results back to the node's repo |

!!! tip "Pin dispatch-test to a tag, never `@main`"
    The comment at the top of the file says so, and the reason is that its
    inputs are a cross-repo API. `comfy-gpu-ci` pins `@v0.4.5`.

## The internal ones

You never call these, but knowing what they are makes a red CI log readable.

| File | |
|---|---|
| `_test-linux.yml`, `_test-macos.yml`, `_test-windows.yml`, `_test-windows-portable.yml` | one hosted lane each: build the env in YAML, hand comfy-test a live server (**attach** mode) |
| `_test-macos-desktop.yml`, `_test-windows-desktop.yml`, `_test-windows-desktop-cuda.yml` | the Electron lanes, driven over CDP |
| `_publish-results.yml` | downloads the results artifacts and pushes them to `gh-pages`. A separate job so a flaky push can be re-run without repeating the slow test ([ADR-0015](adr/0015-publish-is-a-separate-job.md)) |

`publish.yml` is comfy-test's **own** CI -- it builds and publishes the
package to PyPI. It is not something a consumer repo calls.

!!! note "Hosted lanes attach; they do not install"
    The `_test-*.yml` files build the venv, ComfyUI and your pack in YAML
    behind a cache, then hand comfy-test a running server. So on those lanes
    the `install` level is a no-op and install errors are suppressed. A green
    cell means *"works in a prebuilt environment"*, not *"installs cleanly"* --
    see [what a green cell means](lanes.md#what-a-green-cell-means-read-this-one).

## See also

- [Using comfy-test](using.md) -- the four ways to run it, and what to add to your pack
- [Commands](commands.md) -- the CLI these workflows invoke
- [Lanes](lanes.md) -- what each lane builds and what it proves
