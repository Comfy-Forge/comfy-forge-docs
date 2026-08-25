# `execution_light`

> The same workflow execution as [`execution`](execution.md), with the video
> capture removed. One screenshot at the end.

| | |
|---|---|
| **Needs** | `server` (provided by [`registration`](registration.md)) |
| **Default** | no (opt-in) |
| **Fails the run** | yes |
| **Source** | `orchestration/levels/execution_light.py` |

The workflows really run and the results are just as real -- what you lose is
the recording, not the coverage.

## Why it exists

The 5 fps capture loop in `execution` pegs the browser process at 100% CPU. On
a weak runner -- macos-cpu on a 7 GB GitHub-hosted machine is the case that
motivated this -- the Playwright IPC pipe eventually dies, taking the run with
it.

Here the browser sits **idle** while the workflow is driven from Python over
WebSocket polling, and exactly one screenshot is taken at the end. Same
execution, a fraction of the load.

That trade is the whole level: pick it per lane, not per pack
([ADR-0011](../adr/0011-execution-light-is-a-level.md)).

## What you get, and what you lose

| | `execution` | `execution_light` |
|---|---|---|
| Workflows actually run | yes | yes |
| `results.json`, durations, RAM/VRAM peaks | yes | yes |
| Per-workflow logs and console | yes | yes |
| Final screenshot | yes | yes |
| **Canvas video (`driver.mp4`)** | yes | **no** |
| Browser CPU load during the run | high | idle |

The results gallery still gets a card per workflow; it just shows a still
where the video would be.

## Choosing between them

Both are **terminal** levels, so `--level` *replaces* whichever terminal your
config chose rather than truncating the ladder
([ADR-0012](../adr/0012-level-flag-swaps-terminals.md)). That is what lets one
`comfy-test.toml` serve every lane: list `execution` in the config, and have a
constrained lane pass `--level execution_light` on the command line.

## Zero workflows is a silent pass

As with `execution`, no configured workflows means the level logs and returns
PASSED with no `results.json`. See the warning on
[`execution`](execution.md#zero-workflows-is-a-silent-pass).

## Config

Opt-in -- it is not in the default set, so it must be listed in `levels` or
selected with `--level`. Same workflow keys as `execution`:
`[test.workflows] cpu` / `cuda` / `timeout`, and `[test] res` for the
screenshot.

## See also

- [The ladder](../levels.md) -- all 13 levels and the resource model
- [`execution`](execution.md) -- the full-capture version
- [Lanes](../lanes.md) -- which lanes are memory-constrained
