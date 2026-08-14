# ADR-0028: Workspace disk lifecycle

**Status:** accepted (2026-08-14) -- policy decided; automation is the
implementation step. Must land **before** the content-addressed store
([ADR-0020](0020-concurrency-and-env-granularity.md)) does.

## Decision

> **Envs are caches, and caches get evicted -- with consent.** The
> workspace may grow without the user's involvement, but it never
> shrinks without it: reclamation is always visible, attributable, and
> dry-run-first.

1. **Accumulation is the shipped reality, now stated**: every host
   torch/CUDA upgrade strands the previous ABI-tagged variant
   (`<pack>-py313-torch2-9-...` next to `-torch2-10-`) by
   [ADR-0007](0007-machine-wide-workspace-with-per-env-manifests.md)'s
   own design; pack renames orphan directories; multi-GB envs are
   never reclaimed automatically. A user discovers `comfy-env gc` or
   they discover a full disk.
2. **`comfy-env gc` is the one reclamation door**: dry-run by default
   (already the case -- keep it), categorizing candidates as
   *stale-ABI variant* (superseded by a newer tag for the same env),
   *orphan* (no installed pack references it), and *cold* (stamp's
   last-use beyond a threshold). Deletion requires the explicit flag.
3. **Visibility before automation**: the startup banner gains a
   one-line nudge when reclaimable space crosses a threshold
   ("comfy-env: ~18 GB reclaimable, run `comfy-env gc`") -- the
   cheapest fix for the discovery problem, shipped before any
   auto-reclaim is even considered. Auto-reclaim of *stale-ABI*
   variants (the unambiguous category) may follow; orphans and cold
   envs stay manual.
4. **Content-addressing raises the stakes, so GC precedes it**: in a
   content-addressed store an env may be shared by several packs, so
   deletion needs reference counting from the alias layer -- retrofit
   is how you delete a user's 40 GB or none of anyone's. The 0020
   implementation is therefore **blocked on** this ADR's refcount
   design: aliases are the references; an env with zero aliases is an
   orphan; the gc categories above apply unchanged.
5. **Last-use stamps**: workers already stamp envs at spawn;
   "cold" is computed from that. No new bookkeeping.

## Context

Dismissed in the first 2026-08 review round as polish; re-ranked when
0020 accepted content-addressed identity, whose sharing semantics make
naive deletion dangerous. The disk numbers make it user-facing: a
typical multi-pack install carries tens of GB of envs, pixi/uv
hardlinking dedupes within a store but not across strandings, and the
audience (hobbyist Windows machines) is disk-constrained.

## Consequences

- The workspace has a stated lifecycle instead of a ratchet; support
  answers become "run gc" instead of "go delete folders whose names
  look old".
- The idle reaper ([ADR-0019](0019-worker-lifecycle.md)) and gc are
  siblings, not the same thing: the reaper reclaims *memory* from warm
  processes, gc reclaims *disk* from dead envs. Neither triggers the
  other.
- Refcounted deletion is a hard prerequisite the 0020 implementer
  inherits; this ADR is the reason a quick content-addressing PR
  without it gets bounced.
