# ADR-0031: Frontend JavaScript isolation (deferred)

**Status:** accepted as a deferral (2026-08-15) -- a known, unclosed hole
with a mapped path, in the shape of
[ADR-0011](0011-isolation-before-sandboxing.md). The detect-and-contain
half comfy-env can build alone -- the comfy-test `javascript` collision
lint -- is shipped (2026-08); its extensions and the real upstream
boundary are tracked below.

## The concession, first

comfy-env's headline promise is *one pack cannot interfere with another*.
**On the JavaScript plane that promise is currently zero.** Every pack's
`web/` extension (declared via `WEB_DIRECTORY`) is served to ComfyUI's
single browser frontend and loaded into **one origin**: shared `window`,
shared DOM, shared LiteGraph / `app` prototypes, shared `localStorage`
and cookies, shared authenticated `fetch` to `/api`. The entire backend
isolation investment -- separate interpreters, per-env pixi stacks,
authkey'd sockets ([ADR-0026](0026-trust-and-supply-chain.md)), VRAM
leases -- buys **nothing** here, because the frontend never crosses the
process boundary that isolation is built on. Backend isolation and
frontend isolation are orthogonal planes; success on the first confers
no safety on the second. Any statement of comfy-env's guarantees that
does not carry the "...except the half that runs in your browser" clause
is false by omission.

## Threat model (one pack's JS vs every other pack's JS + the user's session)

Three tiers, worst last:

1. **Namespace / dependency collision -- the honest analog of the
   backend problem comfy-env exists to solve, and it breaks TODAY,
   silently.** Two packs write the same `window.X`, monkeypatch the same
   `app.graph` / LiteGraph method, or bundle conflicting copies of a
   library; load order decides who wins. This is the JS mirror of
   torch-2.4-vs-2.8, and unlike the backend case nothing separates the
   combatants. Concrete case: two packs each patching `app.graph` or
   each shipping a different LiteGraph build.
2. **Buggy JS breaks the whole frontend.** An exception in one
   extension's setup can leave the graph UI broken for *every* pack. The
   crash containment the backend has (a worker dies, the pool restarts
   it -- [ADR-0019](0019-worker-lifecycle.md)) has no frontend analog.
3. **Malicious JS runs at full origin authority.** Same-origin means
   total: it can call every `/api` route -- *including the isolated
   backends comfy-env worked to separate* -- read or modify any
   workflow, exfiltrate via `fetch` to anywhere, keylog the prompt box,
   and tamper with other packs' widgets. Note the direction: frontend JS
   is a **higher**-authority position than a sandboxed backend worker,
   because it reaches back through the API into the very backends we
   isolated. It is the soft underbelly, not a lesser afterthought.

## Why deferring is defensible now (earned in ADR-0011's terms, not asserted)

1. **No regression vs vanilla.** Vanilla ComfyUI already loads all pack
   JS unsandboxed into one frontend origin. comfy-env removes nothing
   that was ever there; deferring is not negligence for the same reason
   [ADR-0011](0011-isolation-before-sandboxing.md) gives for the backend
   sandbox.
2. **The boundary is not comfy-env's to draw.** Real per-pack JS
   isolation -- iframe/worker origins, module federation, a
   capability-scoped extension API -- is a **ComfyUI-core** change. A
   backend-isolation library cannot bolt origin separation onto a
   frontend it does not own. This is an **upstream ask**, and it is
   recorded as one in the loan book
   ([ADR-0024](0024-upstream-interface-contract.md)), not pretended to
   be on comfy-env's own roadmap.
3. **Demand asymmetry.** JS collisions annoy; backend dependency
   conflicts are the paying problem comfy-env was built for. Sequence by
   observed pain, per 0011.

This deferral is weaker than 0011's backend deferral in one honest
respect: 0011 can map a concrete next step it will itself build (bwrap
allow-list). Here, the strongest mechanisms are upstream's; comfy-env's
own reach is limited to detection and blunt mitigation (below). The ADR
states that gap rather than borrowing 0011's optimism.

## Options, and why each is deferred (not "out of scope")

Mechanisms that exist, ranked by whose cooperation they need:

- **Drop isolated packs' `web/` entirely.** comfy-env already synthesizes
  node proxies server-side ([ADR-0023](0023-metadata-scan-and-proxy-synthesis.md)),
  so it *could* refuse to serve an isolated pack's JS and lose only that
  pack's custom widgets, not its nodes. Blunt, unilateral, available.
  Deferred because it silently breaks legitimate custom UI and users
  have not reported the collisions that would justify the cost.
- **Namespace / wrap each pack's extension registration** (the JS analog
  of the identity-tag convention in
  [ADR-0015](0015-declared-wire-types.md)). The cheapest thing that
  addresses tier 1 without breaking widgets. The likely **first** step if
  JS collisions are ever reported; not built because they have not been.
- **Module-scope / import-map namespacing.** Contains collisions, not
  malice. Same trigger as above.
- **Shadow DOM.** Contains CSS/DOM leakage but **not script** -- same
  realm, same globals, same `fetch`. Cosmetic, not security. Listed only
  to record that it was considered and is *not* isolation; calling it so
  would be the hand-wave this ADR refuses.
- **CSP on the served frontend, iframe/worker sandboxing, a permission
  model.** The real security answers -- and all host-global or
  origin-level, i.e. ComfyUI-core's to own. Deferred to the upstream ask.

## The collision gate: shipped static lint, plus its named gaps

Security (tiers 2-3) cannot be meaningfully tested by install-testing --
you cannot unit-test "a malicious pack." **Collisions (tier 1) can**, and
that is the plane matching comfy-env's actual mission, so it is where the
testable gate lives. This is the JS analog of the wire-coverage idea in
[ADR-0015](0015-declared-wire-types.md): detect what actually touches the
shared surface, fail on what a pack does not own. It does **not** claim to
test isolation -- it tests *collision*, honestly labeled.

**Shipped (comfy-test `javascript` level, 2026-08):** a static lint
(`orchestration/levels/javascript.py` + `reporting/js_lint.py`). ComfyUI
auto-imports every `web/**/*.js` into one shared page, so the lint derives
the pack's required namespace from `[tool.comfy].DisplayName` (fallback:
the registry id) and errors on any main-realm touch of state the pack does
not own: bare `window.*` writes, `registerExtension` names outside the
namespace, unguarded `message` listeners, shared-object /
LiteGraph-prototype monkeypatches, shared-DOM/storage writes. `.mjs`
(iframe-only) is exempt; a `.mjs` reached by a `.js` import is followed.

**Named TODOs on the gate** (tracked on the [roadmap](../../roadmap.md)):
1. Thread the pack's own node names into the lint so a namespaced
   extension that hooks *another* pack's node -- the squat class -- is
   caught (identity, not just name-prefix).
2. A runtime tier: diff `window` / DOM after load to catch
   variable-aliased globals (`const w = window`) the static pass cannot
   see -- the frontend analog of the ADR-0005 canary (observe reality,
   don't predict it).
3. A cross-pack variant (opt-in level or a `[test] custom` level-10
   hook): install the pack alongside a second fixture pack, load the real
   frontend, assert no two extensions share a `name`, no `setup` throws,
   and a canary global set by pack A survives pack B's load.

The static lint *detects and contains*; it cannot make a same-origin
full-JS plugin **safe** (aliased globals, iframe `parent.*` reach-through,
and the fact that popular packs -- rgthree, Crystools, cg-use-everywhere
-- *legitimately* patch the shared canvas because core offers no
sanctioned point). That safety is the upstream ask below.

## Consequences

- comfy-env's user-facing guarantee statement must carry the frontend
  caveat explicitly (docs action, not just this ADR): "packs are isolated
  at the dependency/backend layer; their frontend JavaScript shares one
  browser origin and is not isolated."
- The upstream ask -- an entry-point manifest replacing the `**/*.js`
  auto-import glob, a sandboxed port-based iframe-widget API, and
  sanctioned hooks for the shared canvas/menubar so packs stop
  monkeypatching `LGraphCanvas.prototype` -- is a ComfyUI-core PR
  (detailed on the [roadmap](../../roadmap.md)) and belongs in the
  [ADR-0024](0024-upstream-interface-contract.md) loan book, not as a
  comfy-env feature. This is the frontend twin of ADR-0011: detect and
  contain now, real boundary upstream.
- The static collision lint is shipped; its three named extensions (node
  identity, runtime diff, cross-pack) are on the roadmap. Everything on
  the *security* plane is deferred until a reported collision (tier 1) or
  the [ADR-0017](0017-pre-1-0-no-backward-compatibility.md) rollout
  tripwire (tiers 2-3, where third-party packs make malice a real threat
  model).
- Revisit trigger, stated so it is not open-ended: the first reported
  cross-pack JS conflict promotes the namespacing option from deferred to
  next; the rollout tripwire promotes the security tiers.
