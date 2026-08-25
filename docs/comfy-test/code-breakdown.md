# Code breakdown -- named and shamed

**28,646 lines of Python across 104 files** under `src/comfy_test/`, plus 3,441
lines of shipped non-Python (raw `wc -l`). Snapshot at **v0.4.15 (2026-08-25),
commit `ab8fba0`**.

The thesis, and the organising principle of this page:

> **Four bugs were found here, documented in a code comment naming the run that
> went wrong, and fixed in exactly one of the two-to-four places that had them.**

Nearly everything below is an instance of that, a count that sizes it, or an
honest exception to it.

| | Measured | |
|---|--:|---|
| Fixes applied to one of several copies | **4** | [below](#the-fix-that-landed-in-one-copy) |
| Lines in one unnamed `with` block | **1,337** | [4.7% of the project](#the-script-that-is-not-a-program) |
| Verified-unreachable lines | **801** | [27% of `screenshot.py`](#what-is-actually-dead) |
| CI jobs running a test or a linter | **0** | [in 12 workflow files](#nothing-gates-a-push) |
| Functions over 150 lines | **18** | 4,871 lines, 17% of the tree |
| Lines that exist because of Windows | **4,238** | 14.8% |

## The fix that landed in one copy

**Each row is a fix whose own comment describes a real run that went wrong.
None is a style preference.**

| The fix | Lives in | Missing from |
|---|---|---|
| CPU/CUDA routing guard | `levels/execution.py:131` | `execution_light.py` -- the variable it tests does not exist there |
| `success` requires `passed > 0` | `desktop/cdp_driver.py:3956` | `execution.py:370`, `execution_light.py:272` |
| Do not log the token | `cli/_git_auth.py:31` | `cli/docker/run.py:316`, `:429` |
| The node-pack gate | `cli/_nodelink.py:41` | `cli/docker/run.py:140` (private fork) |

!!! danger "The 59-workflow bug is still live in `execution_light`"
    `execution.py:120-130` explains itself: *"Previously this case fell through
    to run-everything, which is how a typo'd key ('gpu' instead of 'cuda')
    executed all 59 workflows on a runner configured to run 3, presented as a
    plausible 48/59 result."* The guard is the next line, turning on a local
    named `other_list`.

    `execution_light.py:82-95` never computes `other_list` -- the name is
    absent from the file. A pack with `cpu = [...]` and no `cuda` key, running
    `execution_light` on a CUDA runner, falls through and runs everything. The
    identical failure, reported as a plausible result.

The two `run` functions are 349 and 275 lines and share **216 identical lines**,
62% of the smaller one. The difference is not the video capture the module
docstring advertises. It is the guard, and its absence.

Both of them then compute the verdict as
`all(r["status"] == "pass" for r in results if r["status"] != "skipped")`.
**`all()` over an empty generator is `True`**, so a run whose every workflow was
skipped writes `success: true, passed: 0`. The desktop driver got this right --
`cdp_driver.py:3956` is `_failed == 0 and _passed > 0` -- and the fix never
crossed back.

!!! warning "`comfy-test docker` prints your PAT into the CI log"
    `cli/_git_auth.py:31` defines `tokens_to_redact()`, reading exactly
    `GH_TOKEN`, `GITHUB_TOKEN` and `NODE_PAT`, with a docstring naming the
    hazard. Two call sites use it (`venv_server.py:360`,
    `windows_portable/platform.py:449`).

    `cli/docker/run.py:311-314` appends those same three variables *and their
    values* to `docker_cmd`, and `:316` prints the joined command. Same code at
    `:420-429` for Linux. GitHub masks secrets it issued to that job; a token
    forwarded from a self-hosted runner's environment is not one.

That same file privately re-forks four of `_nodelink.py`'s six public functions
(`:109-163`, 55 lines against 44). The only textual difference is an added
`print()`. What the fork drops is `check_is_node_pack` -- the cheap up-front
gate that stops comfy-test building a venv and cloning ComfyUI for a directory
that is not a node pack. The original `copy_local_node` (`_nodelink.py:107`) now
has zero callers.

## The script that is not a program

**`platforms/desktop/cdp_driver.py` is 4,089 lines, and 1,675 of them (41%) are
top-level statements.** 1,337 of those are a single unnamed
`with sync_playwright() as p:` block spanning `:2538-3874` -- 4.7% of the entire
project, in one statement. Above it sit 55 module-level `def`s that exist to
serve it. There are no classes and no `if __name__ == "__main__"` guard; the
string `__main__` does not occur in the file.

Nothing else is shaped like this. `reporting/screenshot.py` is 2,129 lines with
**15** top-level statement lines; `comfyui/workflow_converter.py` is 1,312 with
**10**.

It is normally executed as a script, in a separate interpreter
(`_desktop_runner.py:70`, `:1602`). Exactly one place imports it as a module --
`_desktop_runner.py:1071`, reaching for one helper -- and **that import runs
the whole desktop test**. Its `sys.exit(0)` at `cdp_driver.py:2545` raises
`SystemExit`, which the `except Exception` below the import does not catch. The
comment above it says *"Ask installations.json for the authoritative slot
rather than guessing."* The guess always wins.

It is also why this file's dead helpers are dead: nothing can call into the
module without running it.

## Where the lines go

| Subsystem | Lines | % |
|---|--:|--:|
| Desktop lane -- `platforms/desktop/` + `cli/_desktop_runner.py` | 5,945 | 21% |
| Sandbox infra -- `cli/docker/` + `cli/vm/` + `cli/sandbox/` | 4,340 | 15% |
| Capture and reporting -- `reporting/`, `debug/` | 3,819 | 13% |
| ComfyUI interface -- `comfyui/` | 3,665 | 13% |
| The level ladder -- `orchestration/levels/` | 3,460 | 12% |
| Config and shared machinery -- `common/`, `backends/`, `lanes/` | 2,856 | 10% |
| CLI -- everything else under `cli/` | 2,065 | 7% |
| Other lanes + platform base -- `platforms/` minus desktop | 1,683 | 6% |
| Orchestration core -- `orchestration/*.py` | 813 | 3% |
| **Total** | **28,646** | 100% |

**One lane is 21% of the codebase; the other nine share 6%.** `platforms/linux/`
is 55 lines across four files. Driving an Electron first-run wizard over CDP
genuinely is harder than `Popen(main.py)`, so the ratio is not the scandal --
the shape of the 5,945 is.

## What is actually dead

**801 verified-unreachable lines, and 575 of them were invisible to a scan that
only walked module-level `def`s.**

| Where | Lines |
|---|--:|
| `reporting/screenshot.py` -- 8 functions and methods | 575 |
| 7 module-level functions across 4 files | 226 |

**27% of `screenshot.py` cannot be reached**: `capture_execution_gif` (238),
`capture_after_execution` (159), `validate_workflow` (59), `capture_workflows`
(46), `_unfreeze_animations` (29), `_create_gif` (26), `_dedupe_frames` (12),
`_disable_first_run_tutorial` (6). Exactly one capture function is live --
`capture_execution_frames` (590), called from `execution.py`.

That reframes the duplication: the 133 lines `capture_execution_frames` shares
with `capture_execution_gif`, and the 93 `gif` shares with `after_execution`,
are not three implementations drifting apart. They are one live function and
two corpses of it.

## Nothing gates a push

**Twelve workflow files. Eleven are `workflow_call` -- reusable jobs waiting on
an outer dispatch. Not one of the twelve invokes `pytest` or `ruff`.**

The twelfth is `publish.yml`. It is named `CI`, and all seven of its steps after
checkout carry `if: github.event_name == 'push'` (`:23, :29, :36, :57, :61,
:70`). **On a pull request it checks out the repo, skips everything, and reports
success.** On a push it tags, builds and publishes to PyPI.

`pyproject.toml` sets `[tool.ruff]` to a line length and a target version and no
`lint.select`, so ruff's defaults (`E4,E7,E9,F`) apply -- which do include
`F401`. Unlike comfy-env, **the linter here is not blind. It is simply never
run.** Under exactly that config it reports **143 errors**, among them 24 unused
imports and 4 unused locals. And the one thing the project did configure
enforces nothing: `E501` is in no default rule set, so **242 lines exceed the
declared 100-character limit and ruff will never say so.**

`.pre-commit-config.yaml` has two hooks. The second runs
`bash scripts/auto-bump.sh` with `always_run: true`, and **there is no
`scripts/` directory in the repository** -- which is how we know pre-commit is
not installed where commits are actually made.

The test suite itself is real, fast and green: **103 passed, 4 skipped in
0.15s**, 1,516 lines across 14 files. That is 5.3% of source, and it imports
about a fifth of the modules. Not among them: `cdp_driver.py`,
`screenshot.py`, `_desktop_runner.py`, `workflow_converter.py`, `execution.py`,
or anything under `cli/docker/`, `cli/vm/` or `cli/sandbox/`.

## Truthiness, three times

**`COMFY_TEST_CUDA` is set to the string `"0"` on every hosted lane, and three
places test it for truth rather than value.**

`orchestration/results.py:113` is `if os.environ.get("COMFY_TEST_CUDA"):` and
returns 86,400 seconds. Executed with `COMFY_TEST_CUDA=0`,
`get_workflow_timeout(120)` returns **86400** -- so a pack's configured
per-workflow timeout is dead on all six GitHub-hosted lanes, and 86,400 s is
4.8x the job ceiling, so it cannot fire on a real CUDA lane either.

`common/config.py:45` has the same shape, and the docstring four lines above it
names the exact failure it now causes: the run must not *"silently fall through
to plain PyPI wheels on a CUDA lane."* With `COMFY_TEST_CUDA=0`,
`_index_variant()` returns **`cu128`** -- a CPU lane resolving its torch triple
against the CUDA wheel index.

Every other read in the tree is `== "1"` or `not in ("0","","false","no")`.

!!! danger "A `linux-cuda` dispatch runs as a Windows CPU job"
    `dispatch-test.yml:38-45` declares `lane`, and calls `platform` a
    *"DEPRECATED alias for `lane`"*. But `platform` is what every routing
    expression reads -- the job filter at `:55` and `runs-on` at `:58-60` --
    while `lane` appears only in cosmetic positions like `name:`. The file has
    25 reads of `inputs.platform` against 15 of `inputs.lane`.

    Dispatch `lane: linux-cuda` with no `platform`: `contains('', 'cuda')` is
    false, so the **CPU** job matches; both `startsWith` tests fail, so
    `runs-on` falls through to **`windows-latest`**. The job is *labelled*
    `linux-cuda`, because `name:` is one of the cosmetic reads. Green results
    publish under a lane that never executed.

    `test-matrix.yml:89` uses `${{ inputs.lane || inputs.platform }}` and is
    fine. Only the dispatch path is inverted.

## Documentation that outranks the code

Two pages here contradict each other and the code sides with one:
`reproducibility.md:71` prescribes `comfy-test run --lane linux-cpu`, which
`argparse` rejects with exit 2. `commands.md:92` says, correctly, *"There is no
`--lane` flag."*

**ADR-0005 is false in both halves of its decision box.** It names
`TORCH_TRIPLES` as the pinning mechanism -- deleted in `b701e1f`, and
`tests/test_torch_triple.py:108` now **bans the string from reappearing**. It
says Python is *"drawn at random per run"*; `config.py:19` pins 3.13. The ADR
even lists un-reproducible runs as an accepted consequence, *"the single most
confusing behaviour in the tool"* -- the same phrase the code uses to explain
why that behaviour was removed.

## What is correctly right

A page that only shames gets discounted whole, including the true parts. These
are load-bearing, and three of them are fixes for exactly the class of bug this
page is about -- found and killed inside the level ladder.

- **`lanes/registry.py` (162 lines) is the best file in the tree.** Five stored
  facts per lane, everything else a computed property, validated at import. Its
  docstring lists the five consumers that derive from it.
- **`orchestration/context.py:98`** kills the vacuous pass in as many words:
  *"the level that is supposed to prove your nodes run had nothing to run, and
  the badge said pass ... if it cannot execute, that is a red."*
- **`levels/registration.py:116`** reports *"Imported cleanly but registered 0
  nodes"* -- a blind spot inherited straight from upstream, closed here.
- **`levels/coverage.py:35`** refuses a 0/0: *"this almost always means the
  static scan couldn't recognize this pack's registration pattern ... Failing
  loudly instead of vacuously passing."* Telling the author the tool is probably
  wrong is rare and correct.
- **`common/base_platform.py` and the venv lane.** Seven abstract methods, one
  implementation, and OS subclasses of 9, 34 and 99 lines -- every line a real
  OS delta. The abstraction is load-bearing where it is used.
- **`common/resource_monitor.py:143-153`** omits metrics it could not measure
  instead of reporting zero.
- **There is no unbounded retry loop in this codebase.** Every retry has a
  ceiling. That is worth stating, because it makes the counts above believable.

The pattern is consistent: **comfy-test is good at catching vacuous passes in
the layer it looks at, and has never looked at its own gate.**

## Regenerate this page

```
find src/comfy_test -name '*.py' | xargs wc -l | sort -rn
uvx ruff@0.16.4 check src/ --statistics
PYTHONPATH=src pytest tests -q
```

This page is a photograph and it *will* drift. Every number above carries a
`file:line` so the next person can re-derive it rather than trust it.
