# ADR-0017: Pre-1.0 -- no backward compatibility, by decision

**Status:** accepted (2026-08-13)

## Decision

> **Until the slow rollout begins, comfy-env promises nothing to
> yesterday's configs.** Config keys, Python APIs, and wire details may
> change in any release, with the old way removed in the same release.
> This is a deliberate policy with a named end condition -- not a habit
> that ends when someone notices it ended.

Concretely, for now:

- Breaking changes ship **without** shims, warnings, or transition
  windows. (Recent practice, now ratified: `[serializers]` was removed
  in the same release that introduced `[types]`; root `[env_vars]` was
  deleted outright.)
- The entire consumer base is the author's own packs, updated in
  lockstep. Releases therefore happen in **barrages**: comfy-env and
  every affected pack ship together, so at any moment the newest
  comfy-env works with the newest packs and no other combination is
  supported.
- Packs pin comfy-env **exactly** (`comfy-env==X.Y.Z`) during this era,
  because lockstep is the compatibility mechanism.

## The tripwire

This era ends at a **named moment: the start of the slow rollout** --
the first release in which third-party pack authors are publicly
encouraged to adopt comfy-env. Crossing it starts two clocks at once:

1. **The compat clock.** Old config keys keep parsing for N releases
   with deprecation warnings before removal; packs switch from exact
   pins to floors (`comfy-env>=X.Y`); a compat canary in CI installs
   the newest comfy-env against the oldest-supported pack configs. The
   `schema = 1` field already parsed by the config layer
   ([ADR-0013](0013-env-file-passthrough-contract.md)) is the
   pre-built hook for versioned migration windows.
2. **The security clock.** CUDA wheel hashes in generated manifests,
   the pickle rung flipped to opt-in per pack, and the sandbox
   timeline of [ADR-0011](0011-isolation-before-sandboxing.md) --
   third-party adoption is exactly the trust-boundary change 0011
   defers on.

Until the tripwire fires, work items behind those clocks are
deliberately parked, not forgotten.

## Context

Two pressures prompted writing this down:

- An adversarial review (2026-08) observed that the no-backcompat rule
  was cited as authority in other ADRs but argued nowhere, and that
  its real cost surfaces silently: the first external adopter converts
  the house rule into a public compat policy whether or not anyone
  notices. The single most important missing artifact was the
  tripwire.
- The author hit the cost from the inside: coupled releases mean one
  comfy-env change can force a barrage across ~33 isolated packs.
  The question "could two comfy-env versions coexist, so GeometryPack
  on 0.4.16 and CADabra on 0.3.17 both keep working?" was evaluated
  and **rejected**: all packs run in one ComfyUI process, one process
  imports one `comfy_env`, and vendored per-pack copies would
  double-apply the ComfyUI monkey-patches and contend for the
  machine-wide workspace
  ([ADR-0007](0007-machine-wide-workspace-with-per-env-manifests.md)).
  The achievable form of that dream is not multi-version -- it is the
  compat contract above: one installed version (the newest) that old
  packs still work with. That is what the compat clock buys, when its
  time comes.

Why not start the compat window now? Because its entire cost --
maintaining shims, warnings, and migration paths -- would today serve
zero users who are not the author. Pre-1.0, with one person holding
every consumer, lockstep is strictly cheaper and equally correct. The
mistake would be crossing the tripwire without noticing; hence this
document.

## Consequences

- comfy-env development stays fast: no shim code, no deprecation
  machinery, no compat matrix -- until the rollout.
- Barrage releases are the accepted price. Envs on disk are already
  insulated (per-env manifests are self-contained; an env built by an
  older comfy-env keeps running), so the barrage surface is config
  parsing and the host-side package only.
- Anyone adopting comfy-env before the rollout announcement is
  explicitly unsupported and should expect breakage without notice.
- The rollout release itself must ship the compat-clock items (floors,
  schema windows, canary) *in that release*, not after -- the promise
  starts when the invitation goes out.
