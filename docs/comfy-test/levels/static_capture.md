# `static_capture`

> Loads each workflow in a real browser and screenshots it **without
> executing**. Proves the graph renders and no node comes up red.

| | |
|---|---|
| **Needs** | `server` (provided by [`registration`](registration.md)) |
| **Default** | yes |
| **Fails the run** | yes -- but skips gracefully if Playwright is unavailable |
| **Source** | `orchestration/levels/static_capture.py`, `reporting/screenshot.py` |

The cheapest level that exercises the **frontend**. Everything below it works
on Python objects and HTTP; this is the first one that finds out whether a
human opening your workflow sees a usable graph.

## How it works

For each configured workflow: drive a headless browser to the running server,
load the workflow JSON into `window.app`, wait for the graph to render, tidy
the view, and screenshot. No prompt is queued, so nothing executes and no
model is loaded.

The capture pipeline is shared with [`execution`](execution.md) and does the
same framing work before shooting: fit the graph to view, close open panels
and alerts, hide the unsaved-changes dot. Screenshots land in
`screenshots/<workflow>.png`.

## What it catches

- **A node that renders red** -- registered but the frontend cannot build its
  widget set, usually an `INPUT_TYPES` the UI cannot express.
- **A workflow that references a node your pack no longer ships**, after a
  rename or removal.
- **Frontend JS that breaks the canvas** on load.
- **Widgets that render wrong** -- a combo with no options, a missing default.

None of this is visible to [`validation`](validation.md), which checks the
graph as data rather than as a rendered page.

## What it does not catch

Nothing runs. A workflow that renders perfectly and fails on the first node is
a pass here -- that is [`execution`](execution.md)'s job.

## Graceful skip

If Playwright is not installed the level logs and skips rather than failing.
A pack with no workflows configured also logs and returns without error.

!!! warning "No workflows means a silent pass"
    This level reports success when there is nothing to capture. Unlike
    [`execution`](execution.md) and [`execution_light`](execution_light.md),
    which now fail on a pack with no workflows, `static_capture` still passes
    -- it is a screenshot of the graph, so an empty run is not obviously
    wrong. Check that your workflows are actually discovered -- see
    [what a pack looks like](../using.md#what-a-pack-looks-like) for the folder names
    ComfyUI and comfy-test recognise.

## Config

| Key | Effect |
|---|---|
| `[test] res` | capture resolution (viewport height), default 1080 |
| `[test.workflows] cpu` / `cuda` | which workflows are captured on this backend |

In the default set. It needs `server`, so listing it pulls in `install` and
`registration`.

It is also one of the four **terminal** levels: a run ends in exactly one of
them, chosen by what `[test] levels` lists
([ADR-0012](../adr/0012-level-flag-swaps-terminals.md)).

## See also

- [The ladder](../levels.md) -- all 13 levels and the resource model
- [`validation`](validation.md) -- the same workflows checked as data
- [ADR-0010](../adr/0010-capture-drives-a-real-browser.md) -- why a real
  browser rather than a headless graph parse
