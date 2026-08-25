# `execution`

> Runs your workflows for real and **records the ComfyUI canvas as video**
> while they run. The level that produces the results gallery.

| | |
|---|---|
| **Needs** | `server` (provided by [`registration`](registration.md)) |
| **Default** | yes |
| **Fails the run** | yes |
| **Source** | `orchestration/levels/execution.py`, `reporting/screenshot.py` |

This is the level the whole tool exists for: not a green check, but a video of
your nodes working on somebody else's machine.

## How the video is made

`capture_execution_frames()` drives a real browser at the live server, loads
the workflow, queues it, and captures at a **fixed cadence of 5 fps** while it
executes. Frames are written as `frame_%06d.png`, then encoded by
`encode_mp4_timeline()` into `driver.mp4` -- *timeline-accurate*, meaning the
mp4's playback timing matches real elapsed capture time, with the pre-run frame
held across the validate/queue window. The loose frames are then dropped; the
mp4 is the artifact the report plays, through a native `<video>` element with a
real seek bar.

Before capture starts the view is prepared the same way
[`static_capture`](static_capture.md) does it: fit the graph, close panels and
alerts, hide the unsaved dot. A high-quality PNG is taken after execution
completes, once previews have rendered.

## What lands on disk

Per workflow, under the run's output directory:

```text
videos/<workflow>/driver.mp4          the canvas recording
videos/<workflow>/metadata.json       frame timings + log offsets
screenshots/<workflow>_executed.png   final frame, full quality
logs/<workflow>.log                   that workflow's log slice
logs/<workflow>_console.log           browser console
logs/<workflow>_resources.csv         RAM/VRAM/CPU samples
```

Per-workflow status, duration and RAM/VRAM peaks go into `results.json`. See
[what a run does](../what-a-run-does.md) for the full tree.

## What it catches

Everything the earlier levels structurally cannot: shape and dtype errors,
OOM under real allocation, models that fail to load, nodes that produce
black images or empty outputs, and the actual wall-clock cost of your graph on
each lane.

## Zero workflows is an error

A pack that ships **no workflows at all** fails the run. It used to log one
line and return **PASSED** with no `results.json` written -- a green badge for
a level that executed nothing.

This is the discovery bug it was hiding: a pack whose example workflows live
in a folder comfy-test does not scan contributes nothing and went green.
Confirm the folder is one of the names ComfyUI itself recognises -- see
[what a pack looks like](../using.md#what-a-pack-looks-like). `execution` now behaves like
[`coverage`](coverage.md), which has always refused to pass a vacuous 0/0.

Say it deliberately when you mean it:

- `skip_workflow = true` under `[test.<lane>]` -- run the pipeline but not the
  workflows, on that lane only
- drop `execution` from `[test] levels` -- stop asking for it at all

An **empty per-accelerator selection** (`cpu = []`) is not affected: this
checks that workflows were discovered, not which ones a given lane selected.

## Config

| Key | Effect |
|---|---|
| `[test.workflows] cpu` / `cuda` / `rocm` | which workflows run on this backend |
| `[test.workflows] timeout` | per-workflow timeout, default 3600s |
| `[test] res` | capture resolution (viewport height), default 1080 |
| `[test.<lane>] skip_workflow` | run the pipeline but not the workflows |

In the default set, and a **terminal** level: a run ends in exactly one of
`static_capture`, `validation`, `execution_light` and `execution`, chosen by
what you list in `[test] levels`
([ADR-0012](../adr/0012-level-flag-swaps-terminals.md)).


## See also

- [The ladder](../levels.md) -- all 13 levels and the resource model
- [`execution_light`](execution_light.md) -- same execution, one still instead
  of video, for memory-constrained lanes
- [ADR-0010](../adr/0010-capture-drives-a-real-browser.md) -- why a real browser
