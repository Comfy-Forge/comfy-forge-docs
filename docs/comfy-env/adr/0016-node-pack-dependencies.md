# ADR-0016: Node pack dependencies (`[node_packs]`)

**Status:** accepted (2026-08-14; enforcement lands in comfy-env after the
existing packs migrate to pinned entries)

**Amended 2026-08-24 (comfy-env 0.4.27):** the section is `[node_packs]`.
It shipped as `[node_reqs]`, which read as *"the requirements of my node"* --
pip packages -- when every entry is a whole pack. Renamed to match this ADR's
own title and to sit parallel to `[cuda_packages]`. No compatibility shim: the
root file has a closed schema, so a stale `[node_reqs]` is rejected by name at
parse time. The `[settings] auto_install_node_reqs` opt-out below was never
built -- per-pack `[settings]` was removed in 0.4.25.

## Decision

> **A pack may depend on other packs -- automatically installed, but only
> pinned and only comfy-envved.** `[node_packs]` stays an install-time
> mechanism (headless testing needs it); in exchange every entry must
> name an exact git ref and point at a pack that honors the host-env
> principle. Anything else is a test-workflow convenience and belongs in
> the test config, not in runtime dependencies.

- `[node_packs]` lives in `comfy-env-root.toml`
  ([ADR-0003](0003-two-config-files-with-two-roles.md) root role) and is
  auto-installed at `install()` time, recursively, with a cycle guard.
- **Pinning is mandatory.** Every entry carries `tag = "..."` or
  `commit = "..."`. Bare `owner/repo` (tracking HEAD) is refused: an
  unpinned dependency makes every downstream install and every CI run
  nondeterministic. **Registry versions are NOT accepted as pins** --
  the registry is not currently trusted for integrity (mutable,
  unsigned); `registry`/`version` entries are rejected until that
  changes. Git refs are the only pin vocabulary for now; revisit when
  the registry can be verified.
- **Compliance is mandatory and mechanically checked.** At clone time
  the dependency must (a) carry a comfy-env config and (b) have a
  host-clean `requirements.txt` -- comfy-env and nothing else. A dep
  that fails the check is a named install error, not a warning: one
  non-compliant dependency pip-installs its requirements into the host
  env and silently defeats the host-env principle for the whole
  machine ("violation by proxy").
- **Runtime vs test dependencies split.** Third-party utility packs
  that example workflows use (video IO, UI helpers) are not runtime
  dependencies of the pack -- they are test/demo dependencies, declared
  in the comfy-test config and installed only into the disposable test
  ComfyUI, where any pack is acceptable because the whole tree is
  thrown away.
- **Conflicts are errors.** The same dependency required at two
  different pins by two installed packs fails the install naming both
  requirers -- never first-installed-silently-wins (the current
  behavior).
- **Opt-out setting:** `[settings] auto_install_node_packs`
  (default `true`) through the existing settings machinery, for users
  who want to audit before anything is cloned.

## Context

A census of the author's 53 packs (2026-08) found `[node_packs]` in 24 of
them, with a real dependency graph: GeometryPack has 11 dependents;
chains run three deep (Cadderizer -> CADabra -> GeometryPack;
WorldStereo -> WorldNav -> MoGe2/PanoPack/Multiband). The declared needs
fall into three kinds: producer packs (the only source of a socket the
depending pack consumes -- e.g. HyMotion's SMPL_PARAMS for
MotionCapture), suite stacks (CAD packs), and third-party workflow
utilities (VideoHelperSuite x4, KJNodes x2, RMBG, cg-use-everywhere).

Every entry today tracks HEAD, unpinned -- while the environment layer
below it pins pixi by version and sha256. And the third-party entries
auto-install native packs whose requirements pip straight into the host
env, violating the host-env principle by proxy.

Two developments reshaped the question. First,
[ADR-0015](0015-declared-wire-types.md) dissolved the *type*-dependency
motivation: consuming another pack's socket type needs nothing
installed (type-identity tags; the transport holds unknown values as
materialized receipts). What remains is producer nodes and suite
composition. Second, comfy-test needs headless, deterministic
dependency setup -- install the pack under test, get its producers,
run the example workflows -- which ComfyUI-Manager (interactive,
workflow-driven, GUI-consent) cannot provide. That testing requirement
is why auto-install survives at all; determinism is why it must be
pinned.

## Rejected alternatives

- **Delete `[node_packs]`; delegate to ComfyUI-Manager.** Manager
  resolves missing nodes from a loaded workflow with user consent --
  the right UX for humans, no story for headless CI. Testing decided
  this.
- **Advisory-only ("pairs with X -- install via Manager").** Same CI
  gap; keeps the documentation value but serves no machine.
- **Registry-version pinning.** The natural long-term answer, rejected
  for now: the registry is mutable and unsigned, so a "pin" there
  pins nothing. Explicitly revisit when registry integrity
  (immutability/signing) exists.
- **Unpinned status quo.** Nondeterministic installs and CI; a
  third-party force-push changes what users get. Incompatible with the
  pinned-everything discipline the env layer already follows.
- **Growing a full pack manager** (dependency resolution, version
  ranges, lockfiles). Out of scope: comfy-env's identity is
  environments and transport. Pins + refuse-on-conflict is the entire
  resolution algorithm, on purpose.

## Consequences

- Installing a compliant pack cascades only through compliant packs:
  the host env gains `comfy-env` and nothing else, no matter how deep
  the graph.
- The 24 existing packs must migrate before enforcement ships: add
  `tag`/`commit` pins to every entry; move VideoHelperSuite / KJNodes /
  RMBG / cg-use-everywhere entries into comfy-test config as test
  dependencies. Until migration, enforcement stays off (shipping the
  check today would fail every install in the suite).
- Suite releases carry pin-bump churn: updating a producer pack means
  bumping pins in its dependents. Accepted cost of determinism; the
  registry, once trustworthy, is the designated relief.
- Named plainly, this is the **exact-pin diamond**: with GeometryPack
  at 11 dependents, every GeometryPack release forces a lockstep
  pin-bump across the suite, and two packs pinning GeometryPack at
  *different* commits are uninstallable together -- refuse-on-conflict
  fires by design. Livable while one author owns all 24 consumers and
  releases in barrages ([ADR-0017](0017-pre-1-0-no-backward-compatibility.md));
  the first *external* pack pinning GeometryPack independently makes
  the diamond a stranger's problem. That moment is the same rollout
  tripwire as 0017's, and the designated exits are registry-backed
  version ranges (above) or hoisting shared producers out of
  `[node_packs]` entirely -- to be chosen then, not now.
- comfy-test runs additionally record each cloned dependency's resolved
  commit SHA in the run report, so even future registry-based installs
  stay reproducible after the fact.
