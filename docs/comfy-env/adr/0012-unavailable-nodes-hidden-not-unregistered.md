# ADR-0012: Unavailable accelerator nodes are menu-hidden, never unregistered

**Status:** accepted

## Decision

> **Register always; hide from the menu; explain twice.** Not
> unregistration (a missing node type breaks shared-workflow loading
> inscrutably); not a visible-but-broken picker entry (CPU users would
> browse nodes they can never run); registered with real inputs/outputs,
> menu-hidden via `DEPRECATED`, with a startup summary and a named-reason
> error on execution.

Three layers:

1. **Always register** the node type: proxies for unavailable nodes are
   built with their real inputs/outputs, so workflows load and validate
   and dispatcher ids resolve.
2. **Hide from the menu**: the unavailable stub sets `DEPRECATED = True`
   (plus the "(requires CUDA -- unavailable on this machine)" description
   badge for anyone who surfaces it, e.g. via a loaded workflow).
3. **Explain twice**: one summary warning at startup
   ("N node(s) require CUDA; no such accelerator on this machine --
   registered but hidden from the node menu"), and the named-reason error
   if the node is ever executed anyway (via a loaded workflow):
   `Node 'X' requires CUDA; this machine has backend 'cpu' ...`.

## Context

When a node declares an accelerator the machine lacks (ADR via the
[accelerator rule](../accelerators.md)), something must happen at
registration time. Two positions were argued:

- **Do not register at all**, plus a startup warning ("CUDA nodes found but
  no accelerator detected") -- keeps the node picker clean; a CPU user
  never sees nodes they cannot run.
- **Register visibly as unavailable** (the original v1 behavior) -- because
  an unregistered node type makes any *shared workflow* that references it
  fail to load with "node type not found", indistinguishable from a broken
  or missing pack. This is a documented support-load generator, and
  dispatcher nodes route to accelerator leaf nodes by node id, so those ids
  must resolve.

Both concerns are real; they concern different surfaces. "Visible"
conflates two things -- **menu presence** and **type registration** -- and
ComfyUI already separates them: a node with `DEPRECATED = True` is hidden
from the node picker and search but remains registered and executable.

## Consequences

- CPU users never encounter dead nodes while building workflows; users
  loading a GPU workflow get a loadable graph and an actionable error
  instead of "node type not found".
- The `DEPRECATED` flag is repurposed slightly (the node is not
  deprecated, it is unavailable) -- accepted: it is the only stock
  menu-hiding mechanism, degrades to plain visibility on frontends that
  ignore it, and the description badge disambiguates.
- comfy-test's registration level still sees the node on CPU lanes
  (registered), matching the honest-skip model for execution.
