# ADR-0010: Capture drives a real browser

**Status:** accepted (2026-08)

## Decision

> **Screenshots and video come from a real browser (Playwright / CDP)
> driving the real ComfyUI frontend against the live server** -- not from
> server-side rendering, not from API responses.

## Context

A node pack is not only Python. Roughly half of the interesting packs ship
frontend code: DOM widgets, iframe viewers, canvas overlays, custom node UI.
When those break, the server is perfectly healthy -- `/prompt` returns 200,
the workflow completes, `results.json` says pass -- and the node renders as
a blank rectangle.

Server-side assertions cannot see that. The only instrument that can is a
browser that loads the same JavaScript a user loads.

The same reasoning produced the JAVASCRIPT level
([ADR-0014](0014-javascript-isolation-is-static.md)): static analysis finds
collision hazards before they fire; browser capture shows what actually
rendered. They are complementary halves of the frontend story, one cheap and
predictive, one expensive and observational.

## Alternatives rejected

- **API-only assertions** (queue the prompt, check the outputs). Blind to
  every frontend failure, which is the failure class users report as "the
  node is broken".
- **Headless rendering without the real frontend** (screenshot the output
  images only). Tests the pipeline, not the pack's UI.
- **Author-supplied visual fixtures.** Shifts the work to the author and
  makes the verdict depend on fixture quality.

## Consequences

- Capture is the **most expensive and most fragile** stage. It needs a
  browser install, it is CPU-hungry, and on weak runners the Playwright IPC
  pipe dies -- which is exactly what forced
  [ADR-0011](0011-execution-light-is-a-level.md).
- The capture machinery is large and full of workarounds (animation
  freezing, per-iframe callback capture, frame sequencing). Those exist
  because real browsers are non-deterministic; they are documented as
  behaviour, not as internals.
- Screenshots are artifacts, not assertions: comfy-test records what
  rendered, it does not diff against a golden image. Visual regression is a
  deliberate non-goal -- goldens rot on every ComfyUI frontend release.
- Desktop uses the same principle through a different transport (CDP against
  Electron, [ADR-0013](0013-desktop-is-driven-over-cdp.md)).
