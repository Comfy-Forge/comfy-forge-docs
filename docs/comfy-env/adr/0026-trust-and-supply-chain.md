# ADR-0026: Trust inventory and supply chain

**Status:** accepted (2026-08-14). Unlike most hardening, several items
here are explicitly **not** parked on the [ADR-0017](0017-pre-1-0-no-backward-compatibility.md)
security clock: the wheel-consuming population already exists.

## What a user trusts today, enumerated

Installing a comfy-env pack currently means trusting:

1. **The pack itself** -- its `install.py` and node code execute with
   the user's privileges. Baseline ComfyUI-ecosystem trust, unchanged
   by comfy-env.
2. **The CUDA wheel farm** -- native binaries served from a GitHub Pages
   index (Releases API fallback) run by this project, executing at import
   time inside the isolated env. Blast radius: arbitrary native code on
   every GPU machine running these packs.

    *Amended 2026-09:* two of the three qualifiers below have since been
    fixed. The wheels are **inside the lockfile** -- inlined as direct-URL
    `pypi-dependencies` -- and **hash-verified wherever the index anchor
    carries a `#sha256=` fragment**, which the default index does. The
    post-pixi `uv pip install --no-deps` side channel that put them outside
    the lock is deleted. What remains true, and is what decision 1 below
    is about: the index is **mutable**, a mirror set via
    `COMFY_ENV_CUDA_WHEELS_INDEX` need not attach fragments at all, and
    nothing is signed.
3. **`[node_packs]` transitive installs** -- cloned repos' `install.py`
   files run, and so does the `install.py` of anything *they* declare,
   recursively. [ADR-0016](0016-node-pack-dependencies.md) rules that
   entries must be pinned to a git ref and that `registry`/`version`
   entries are refused until the Comfy Registry can be verified.
   **Neither rule is enforced in code yet** -- ADR-0016's status line
   says so, and this item used to read as though it were done. Today
   `install_node_packs` accepts a bare `owner/repo` (tracking HEAD) and
   dispatches `registry = "..."` straight to `install_from_registry`,
   which downloads whatever `api.comfy.org` currently serves for that
   name and runs its install script (`packages/node_packs.py:172`,
   `:69`). Blast radius is the same as item 1, at a source the pack
   author did not pin and the user never named.
4. **The pinned pixi binary** -- the one link done right: version
   pinned, sha256-verified against the release's own sums, refused on
   mismatch. **This is the template for item 2.**
5. **The local IPC surface** -- was unauthenticated (any local process
   could connect and feed pickles to the parent). **Fixed in 0.4.18:**
   a per-spawn authkey is verified as the worker's first frame, with an
   `SO_PEERCRED` same-uid check on Linux AF_UNIX, and the address +
   authkey travel via the worker's environment, never argv. Residual
   gap to note for multi-user servers: on the Windows TCP-loopback
   fallback there is no peer-uid check, so the authkey (which lives in
   the child's environment block, readable by same-user processes) is
   the only gate there.

## Decisions

1. **Wheel integrity: curation now, hashing/signing at the rollout
   clock (revised 2026-08-15).** The earlier ruling was "hash now"; on
   reflection it is deferred, because pre-rollout the only attacker is
   the maintainer's own compromised GitHub account -- the wheel
   consumers are the maintainer's own packs, and sha256-pinning buys
   tamper-defense against a threat the current era barely has while
   imposing a permanent farm↔manifest hash-sync burden (a rebuilt
   wheel whose hash was not re-synced fails a *correct* install).
   Ordering:
   (a) `Requires-Dist` curation on the farm -- **do now**, but on its
   own merits (resolver-safe wheels, lockfile-visible inlining,
   successor-usable artifacts per the bus-factor point), not as
   security;
   (b) resolved wheel sha256 pinned into the generated manifest --
   **deferred to the rollout tripwire** with the rest of the trust
   work, when strangers' machines make tamper-defense a real threat
   model;
   (c) signing/attestation (sigstore) -- rollout tripwire, after (b).
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
   instrument-then-flip ruling: rung-5 hits get counted (the
   `SERIALIZE` debug category logs them), and opt-in-per-pack pickle is a precondition
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
  env installs is either pixi-locked or hash-pinned by us. **Landed**
  (2026-09): the wheels are in the manifest, so the "load-bearing
  coincidence" (`--as-is` sparing the side-channel wheels) is no longer
  load-bearing, and the side channel it protected is gone.
- Farm CI grows a qualification stage; wheel publishing slows down by
  one smoke run. Accepted.
- ADR-0011's "no regression vs vanilla ComfyUI" remains true for code
  execution but was never true for the wheel channel (vanilla has no
  wheel channel); this ADR stops using that argument for item 2.
