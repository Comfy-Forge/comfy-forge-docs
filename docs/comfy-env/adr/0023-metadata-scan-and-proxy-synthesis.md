# ADR-0023: Metadata scan and proxy synthesis

**Status:** accepted (2026-08-13) -- records the mechanism as shipped
(`isolation/metadata.py`, ~1100 lines; the subsystem most exposed to
ComfyUI schema churn).

## Decision

> **The main process never imports a pack's node code -- it interviews
> it.** A short-lived subprocess in the pack's own env imports the node
> modules, extracts everything ComfyUI needs to *describe* the nodes,
> and sends it back serialized; the parent then synthesizes proxy
> classes from the description alone. Nodes exist twice: as real
> classes in the worker env, and as generated stand-ins in ComfyUI.

Mechanics:

- **The scan**: `fetch_metadata()` runs a scan script in the env's
  interpreter, which imports the node modules and captures per-class
  schema -- `INPUT_TYPES` (evaluated, not parsed: they are classmethods
  and can be dynamic), `RETURN_TYPES`/names, category, display names,
  V3 `io.Schema` where present, `ROUTES` declarations, plus the
  accelerator-rule lint (`accel_import_violations`: top-level imports
  of declared `[cuda]` packages, forwarded via `COMFY_ENV_ACCEL_PKGS`).
  The payload crosses back via pickle -- acceptable here because the
  scanned code is the same pack the user already chose to execute
  ([ADR-0011](0011-isolation-before-sandboxing.md) trust posture).
- **The cache**: keyed `v{_CACHE_VERSION}:{pkg_hash}` -- a content hash
  of the pack, versioned by the scan format (`_CACHE_VERSION`, bumped
  whenever the script or payload shape changes). Warm boots skip the
  subprocess entirely; envs scan in parallel at `register_nodes()`.
- **Proxy synthesis**: `build_proxy_class()` fabricates a V1-shaped
  class per node whose `FUNCTION` forwards kwargs to the worker
  ([ADR-0019](0019-worker-lifecycle.md) pool) and whose schema is the
  scanned one -- including the impedance mismatches this layer exists
  to absorb: V3 hidden-value tuples unwrapped for V1 execution,
  `DynamicCombo` parents tracked so dropdowns expand, `NodeOutput`/
  expand-graph results handled on the output stage, unavailable
  backends registered-but-menu-hidden per
  [ADR-0012](0012-unavailable-nodes-hidden-not-unregistered.md).

Rejected alternatives:

- **Import nodes in the main process**: is precisely the thing
  isolation exists to prevent -- the imports drag the pack's native
  dependencies into the host.
- **Static analysis (AST) instead of a scan subprocess**: cannot
  evaluate dynamic `INPUT_TYPES` (dropdowns built from disk contents,
  torch-dependent dtype lists), which real packs use routinely. The
  scan pays one subprocess spawn per cold boot for full fidelity.
- **Hand-written proxy stubs per node**: 161 nodes in the flagship
  pack alone; generation from scanned truth is the only shape that
  survives node authors editing their own schemas.

## Context

This is the machinery that makes isolation *invisible* -- ComfyUI sees
ordinary node classes and never learns the real ones live elsewhere.
It is also, as the 2026-08 review noted, the subsystem with the most
surface against ComfyUI internals (V1/V3 schema duality, hidden-input
conventions, DynamicCombo expansion, the executor's expand protocol),
which is why its facts should follow ADR-0001's pattern of
version-stamped upstream observations, and why cache invalidation is
format-versioned rather than best-effort.

## Consequences

- Cold boot pays one scan subprocess per env (seconds, dominated by
  the env's own imports); warm boots are cache hits. A stale cache is
  impossible by construction only if `_CACHE_VERSION` discipline holds
  -- bumping it belongs in review checklists for any scan change.
- Schema fidelity is bounded by what the scan captures: a ComfyUI
  schema feature the scan doesn't model (the next V3 addition) shows
  up as proxies missing that feature, not as an error. Compat canary
  runs against a pinned ComfyUI checkout catch this class.
- The pickle channel from the scan subprocess is inside the existing
  trust boundary but is one more thing the eventual sandbox
  (ADR-0011) must replace with a declared format.
- Proxy classes are V1-shaped by deliberate choice: V1 is the schema
  ComfyUI's executor treats most conservatively, and the scan flattens
  V3 constructs into it rather than tracking both shapes end-to-end.
