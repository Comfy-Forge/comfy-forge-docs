# ADR-0011: `execution_light` is a level, not a fallback

**Status:** accepted (2026-08)

## Decision

> **Reduced-capture execution is its own named level, selected explicitly by
> the lane -- never an automatic degradation inside EXECUTION.**

`EXECUTION` runs the workflow while capturing per-frame video.
`EXECUTION_LIGHT` runs the same workflow via Python-side WebSocket polling
with the browser idle, then takes exactly one screenshot at the end.

## Context

The per-frame capture loop pegs the browser process at 100% CPU. On weak
hosted runners -- macOS at 7 GB -- the Playwright IPC pipe eventually dies
mid-run, and the failure looks like a workflow failure rather than a
capture failure (`levels/execution_light.py`).

The tempting fix is to catch that and quietly fall back to a single
screenshot. It is the wrong fix, because it destroys the meaning of the
result: two green cells on the dashboard would then represent different
amounts of evidence, decided at runtime by whichever runner happened to be
under load. "Did this pack's workflow render correctly frame by frame?" and
"did it not raise?" are different questions, and a badge that silently
switches between them is not reporting anything.

Making it a level moves the choice to configuration time, where it is
visible in `provenance.levels` and in the lane definition.

## Alternatives rejected

- **Auto-degrade inside EXECUTION on capture failure.** Silent, run-dependent
  evidence level; a flaky runner would change what green means.
- **Retry the capture.** Does not address the cause (the runner cannot
  sustain the loop) and doubles the slowest stage.
- **Drop video capture entirely** and always take one screenshot. Cheaper,
  but loses the per-frame record on the platforms that *can* afford it --
  and that record is what catches progressive rendering bugs.
- **A `capture = "light" | "full"` option inside EXECUTION.** Equivalent in
  power, worse in reporting: the level list is what the dashboard and
  provenance already surface, so making it a level means it is recorded
  everywhere for free.

## Consequences

- Lanes must choose. macOS lanes pass `execution_light`; Linux and Windows
  pass `execution` ([ADR-0012](0012-level-flag-swaps-terminals.md) is what
  makes that a one-flag change).
- A macOS green cell carries less evidence than a Linux green cell, and the
  provenance block says so explicitly.
- Both levels write the same `results.json` shape, so the dashboard does not
  branch -- only the artifacts differ (one screenshot vs a frame sequence).
- If a future runner becomes capable, the lane changes one word; no code
  path changes.
