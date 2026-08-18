# CW-ADR-0010: No free-threaded builds -- the GIL is not going anywhere yet

**Status:** accepted (2026-08-18) -- revisit when a real pack asks for it

## Decision

> **The farm builds against standard CPython only.** Not a parallel
> free-threaded grid (it doubles every axis for a population that does not
> exist yet); not a best-effort subset (a half-covered ABI is worse than an
> absent one, because it fails at import instead of at resolve).

- `cp3XXt` wheels are treated as belonging to their standard Python version for
  reporting, and no `+cu` extension is compiled against the free-threaded ABI.
- The matrix page says so plainly rather than implying the tag does not exist.
- Reversal is cheap and is a config change, not a redesign: the axis already
  exists in the wheel tags, and `_defaults.yml` would gain `t` variants.

## Context

Python 3.13 shipped an experimental build with the GIL removed (PEP 703). It is
a **separate ABI**: `torch-2.10.0+cu128-cp313-cp313t-...` and
`...-cp313-cp313-...` are the same Python version and incompatible binaries. A
C++ extension compiled against one will not load in the other.

Upstream publishes both. The farm's grid is already
`architectures x CUDA x Python x torch x CPU x OS`; adding a free-threaded axis
does not add a column, it **doubles the Python axis** -- roughly 190 more jobs
per package on today's grid, against an allowance that is already absorbing
~95% of the compute bill.

What would justify that spend is demand, and there is none to point at. No
`comfy-env.toml` in the fleet requests a free-threaded interpreter; ComfyUI
itself does not ship one; and the ecosystem these packs depend on -- torch
extensions with pybind11 and CUDA kernels -- is where free-threading support is
thinnest, because every C extension has to be audited for thread-safety before
it can honestly claim `Py_MOD_GIL_NOT_USED`.

The other half of the reasoning is that the GIL is not on a clock. PEP 703 was
accepted with an explicit staged rollout and an explicit escape hatch: the
free-threaded build is optional, distributors are not obliged to ship it, and
the steering council reserved the right to revert if adoption or performance
disappointed. Nothing about that timeline requires a wheel farm to move first.

## Consequences

- The grid stays the size it is. No new axis, no doubled Python coverage.
- A user on a free-threaded interpreter gets no wheel from this farm and finds
  out at resolve time (pip reports no matching distribution), not at import
  time with an undefined symbol. That is the failure mode to prefer.
- The matrix page reports `cp3XXt` under its standard Python version. That is a
  reporting convenience and slightly overstates coverage for a free-threaded
  reader; it is honest only because upstream currently ships **no**
  free-threaded-only target -- every `cp3XXt` wheel has a `cp3XX` sibling for
  the same (torch, python, platform), so nothing is claimed that does not exist.
  **If that ever stops being true, this decision has to be revisited before the
  page can stay accurate**, because a check mark would then stand for a wheel
  no standard interpreter can install.
- Revisit triggers, any one of which is enough: ComfyUI ships or supports a
  free-threaded interpreter; a pack in the fleet declares one; torch publishes
  a free-threaded-only combo; or the free-threaded build stops being labelled
  experimental upstream.
