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

## How a failure is detected

This is the only level that queues through the API rather than the browser, so
it listens for ComfyUI's execution events on a WebSocket directly.

That listening is conditional in a way worth knowing about. ComfyUI only sends
`execution_start`, `execution_error` and `execution_success` when the prompt
was submitted with a `client_id` -- all three are `broadcast=False`, and
`PromptExecutor.add_message` gates on
`client_id is not None or broadcast` (`execution.py:683`). A prompt queued
without one produces **no terminal event at all**, so a failing workflow and a
passing one look identical on the wire.

comfy-test sends the `client_id` of the socket it is listening on. As a second
line of defence it also reads the verdict ComfyUI files in
`/history/<prompt_id>`: `status.status_str` is `success` or `error`, and the
`execution_error` payload is replayed in `status.messages`. So a failure is
reported with the real exception type, message and node whether it was seen
live or recovered afterwards.

!!! note "This used to be a silent pass"
    Before that fix, a workflow that raised mid-graph produced no event, the
    completion fallback saw a history entry, concluded "done", and reported
    **passed**. Verified against ComfyUI 0.33.0.

## Choosing between them

Both are **terminal** levels: a run ends in one of them, not both. Which one
is decided by `[test] levels` in your `comfy-test.toml` -- list one or the
other. There is no command-line override
([ADR-0012](../adr/0012-level-flag-swaps-terminals.md)).

## Zero workflows is an error

Listing `execution_light` on a pack that ships no workflows now **fails the
run**. It used to log one line and return PASSED: the level that exists to
prove your nodes run had nothing to run, and the badge said pass.

Three ways out, depending on what you meant: add a workflow, drop the level
from `[test] levels`, or set `skip_workflow = true` under `[test.<lane>]` to
skip workflows on one lane. See the note on
[`execution`](execution.md#zero-workflows-is-an-error).

## Config

Opt-in -- it is not in the default set, so it must be listed in `levels`.
Same workflow keys as `execution`:
`[test.workflows] cpu` / `cuda` / `timeout`, and `[test] res` for the
screenshot.

## See also

- [The ladder](../levels.md) -- all 13 levels and the resource model
- [`execution`](execution.md) -- the full-capture version
- [Lanes](../lanes.md) -- which lanes are memory-constrained
