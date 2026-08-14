# ADR-0022: comfy-env's placement -- inside the env it manages

**Status:** accepted as-is for the pre-1.0 era (2026-08-13), with the
hazard named and the relocation alternative recorded; revisit at the
[ADR-0017](0017-pre-1-0-no-backward-compatibility.md) tripwire.

## Decision

> **comfy-env is a normal pip package in the shared ComfyUI
> environment -- the one deliberate exception to its own host-env
> principle.** The env manager lives inside the env it manages, ships
> through the only channel a pack's `requirements.txt` can use, and
> accepts the failure mode that placement creates: any pack's stale
> exact pin can downgrade comfy-env for the whole machine.

What this buys, and why it stands for now:

- **Distribution reality.** Packs declare `comfy-env==X.Y.Z` in
  `requirements.txt`, and ComfyUI/Manager runs `pip install -r` --
  that is the entire delivery mechanism available to a pack on a user
  machine. A pip package in the host env is the only placement that
  installs itself with zero extra user action.
- **One interpreter, one import.** `register_nodes()` must run inside
  ComfyUI's process; the runtime half is unavoidably host-resident
  Python whatever else moves.
- **Iteration speed for a solo maintainer**: one package, one version,
  `pip install -e` for development.

The named hazard: pip is last-write-wins. Installing a pack whose
`requirements.txt` pins an older comfy-env silently downgrades every
other pack's comfy-env on that machine. This is not hypothetical
machinery -- `check_sibling_comfy_env_pins` (`install/plugin.py`)
exists precisely to *warn* about it, which is a symptom-patch for a
placement decision this ADR now writes down. During the pre-1.0 era
the barrage discipline (ADR-0017) keeps all pins aligned because one
person owns all of them.

## The recorded alternative: split-and-relocate

The 2026-08 review's proposal, preserved for the revisit:

- **Split the package in two.** The *runtime* half (proxies,
  `register_nodes`, transport parent side) must stay a host-env import
  -- no placement can change that. The *installer* half (manifest
  compiler, wheel resolver, workspace/env materialization -- the bulk
  of the code and of the churn) has no such constraint.
- **Relocate the installer half** to a versioned, comfy-env-owned
  artifact under `~/.comfy-env/` (a zipapp is sufficient; a static
  binary is possible but pointless -- pixi already is the Rust binary
  in this stack, and the same directory already hosts the pinned pixi
  per its bootstrap design). The in-host pip package shrinks to a thin
  shim that execs the right installer version.
- Effect: a pack's stale pin can then downgrade only the thin shim --
  small, stable, rarely-changing -- while the machinery that actually
  churns is version-keyed per env and cannot be clobbered by pip at
  all. The downgrade hazard class shrinks from "everything" to "a
  shim designed to tolerate it."

Why not now: the hazard is warn-detected today and structurally
impossible to *hit* while one person aligns all pins in lockstep; the
relocation is real engineering with zero user-visible payoff until the
day external packs pin independently -- which is exactly the ADR-0017
tripwire. Doing it early would be insurance paid on a risk the current
era cannot realize.

## Context

The review put it sharply: the project's founding principle is "the
host env stays clean because shared mutable site-packages is where
conflicts live" -- and comfy-env itself is 12k lines in that exact
shared mutable site-packages, defended by a warning function. That
tension deserved a decision record rather than an unexamined
tradition, even though (as with
[ADR-0021](0021-three-call-contract.md)) examination largely vindicates
the status quo *for the current era*: every alternative placement
either cannot run the runtime half (binaries), cannot self-deliver
(anything outside pip), or only pays off after external adoption
(the split).

## Consequences

- The sibling-pin warning stays load-bearing until the split; treat
  its firing as a barrage-discipline bug, not noise.
- The pre-rollout checklist (ADR-0017) gains an item: decide the
  split-and-relocate before inviting external pins, because after
  external adoption every new pack is a new source of stale pins
  against a package that can no longer rely on lockstep.
- Anything added to the installer half should keep the future seam in
  mind: no new imports from installer code into runtime code (the
  split's cut line is the package's existing `install/` + `packages/`
  vs `isolation/` boundary, and it should stay clean).
