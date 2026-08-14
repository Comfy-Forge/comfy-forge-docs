# ADR-0026: Trust inventory and supply chain

**Status:** accepted (2026-08-14). Unlike most hardening, several items
here are explicitly **not** parked on the [ADR-0017](0017-pre-1-0-no-backward-compatibility.md)
security clock: the wheel-consuming population already exists.

## What a user trusts today, enumerated

Installing a comfy-env pack currently means trusting:

1. **The pack itself** -- its `install.py` and node code execute with
   the user's privileges. Baseline ComfyUI-ecosystem trust, unchanged
   by comfy-env.
2. **The CUDA wheel farm** -- native binaries served from a personal
   GitHub Pages index (Releases API fallback), installed via a
   post-pixi side-channel **outside the lockfile, unhashed, from a
   mutable index** (`two-system-problem.md` says this honestly; this
   ADR makes it a decision surface). Blast radius: arbitrary native
   code on every GPU machine running these packs.
3. **`[node_reqs]` transitive installs** -- cloned repos' `install.py`
   files run ([ADR-0016](0016-node-pack-dependencies.md) bounds this
   to pinned refs; the registry path is rejected there and the
   dead code that still implements it is scheduled for deletion).
4. **The pinned pixi binary** -- the one link done right: version
   pinned, sha256-verified against the release's own sums, refused on
   mismatch. **This is the template for item 2.**
5. **The local IPC surface** -- was unauthenticated (any local process
   could connect and feed pickles to the parent); the
   authkey + peer-uid handshake is in flight on the `fixbugs` branch
   and its absence should block any multi-user-server recommendation
   until merged.

## Decisions

1. **Wheel integrity moves to now, not the rollout clock.** In order:
   (a) `Requires-Dist` curation on the farm so wheels are
   resolver-safe; (b) resolved wheel sha256 pinned into the generated
   per-env manifest, verified at install -- the pixi-bootstrap pattern
   applied to our own artifacts; (c) signing/attestation (sigstore) at
   the rollout tripwire, not before.
2. **Release qualification for the farm**: a wheel reaches the index
   only after a per-combo smoke test (import + one kernel launch) in
   farm CI; a staging index precedes the stable one. Rollback = index
   pointer flip over immutable per-release artifacts -- yanking a bad
   wheel must never require rewriting history.
3. **Bus factor, named**: every binary in this ecosystem currently
   flows through one personal GitHub account (index, releases, farm).
   There is no technical fix for a bus factor of one; the mitigation
   is (a) this sentence existing, (b) `Requires-Dist` curation making
   the artifacts usable by a successor without the farm, and (c)
   "migrate index + farm to an org account" sitting on the 0017
   rollout checklist with a date.
4. **The pickle rung** stays as-is until the sandbox milestone
   ([ADR-0011](0011-isolation-before-sandboxing.md)), per the
   instrument-then-flip ruling: rung-5 hits get counted (doctor
   surfaces them), and opt-in-per-pack pickle is a precondition
   written into the sandbox work, because a sandboxed worker that can
   still hand the parent a pickle owns the parent.

## Context

The 2026-08 reviews converged on one scoping error in 0017: "the
security clock starts at external rollout" conflates two populations.
External *pack authors* arrive at the rollout -- but external *users*
of the author's own packs exist today, and they execute unhashed
native binaries from a mutable single-maintainer index on every
install. The trust-boundary argument that defers compat work does not
defer artifact integrity. Hence this record, and hence its ordering:
integrity (hashes) before authenticity (signing), both before any
sandbox story.

## Consequences

- The generated manifest becomes the integrity anchor: everything an
  env installs is either pixi-locked or hash-pinned by us. The
  "load-bearing coincidence" (`--as-is` sparing the side-channel
  wheels) stops being load-bearing once wheels are in the manifest.
- Farm CI grows a qualification stage; wheel publishing slows down by
  one smoke run. Accepted.
- ADR-0011's "no regression vs vanilla ComfyUI" remains true for code
  execution but was never true for the wheel channel (vanilla has no
  wheel channel); this ADR stops using that argument for item 2.
