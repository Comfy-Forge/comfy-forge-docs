# ADR-0014: Frontend isolation is enforced statically

**Status:** accepted (2026-08). The runtime tier is deferred; the upstream
fix is tracked in the [roadmap](../../roadmap.md).

## Decision

> **The JAVASCRIPT level parses the pack's served frontend JS with
> tree-sitter and enforces an isolation standard: literal, unambiguous
> violations are errors; heuristics are warnings; `.mjs` files are exempt.**
> The required namespace is derived from the pack's own
> `[tool.comfy] DisplayName`, lowercased -- zero configuration
> (`reporting/js_lint.py`).

## Context

ComfyUI auto-imports every `web/**/*.js` from every installed pack into
**one shared browser page**. There is no module scoping, no namespace, no
sandbox: one pack's `window.THREE = ...` overwrites another's, a duplicate
`registerExtension` name throws and silently drops a feature, an unguarded
`message` listener receives every other pack's iframe traffic.

None of that is visible in a single-pack test -- collisions need two packs
to exist. Testing it dynamically would mean installing a corpus of other
packs and diffing the realm, which is expensive and open-ended. Static
analysis answers a narrower but decidable question: *does this pack touch
anything it does not own?*

The severity split follows the static-analysis-lies doctrine: a parser can
be certain about a literal `window.X =`; it cannot be certain that an
aliased or computed write is or is not a global. Certainty gets an error;
inference gets a warning.

`.mjs` exemption is not cosmetic: ComfyUI's auto-import glob is `**/*.js`
only, so renaming a leaking bundle to `.mjs` genuinely removes it from the
shared realm. The rule rewards the real fix.

## Alternatives rejected

- **Regex scanning.** Fires on `window.THREE` inside comments and strings;
  a hard error must never be a false positive.
- **An ESLint plugin.** Would put a Node toolchain in the dependency path of
  a Python test harness, and ESLint's rule model does not express "this is
  a fact about the shared realm".
- **Config-declared namespaces as the primary source.** Makes the check
  opt-in and lets a pack declare itself compliant. Deriving from
  `DisplayName` means the pack's published identity *is* the namespace.
  **Amended 2026-08:** the `[test.javascript] namespaces` escape hatch is
  removed. Its only use was grandfathering packs that had absorbed other
  packs and kept their JS prefixes -- which is exactly the collision this
  ADR exists to prevent, so those packs must rename rather than declare.
  With one namespace always derivable, a pack that declares **no**
  identity is now a hard failure instead of a guessed prefix with the
  naming rules downgraded to warnings.
- **Runtime realm diffing** (load the page, snapshot `window`, install the
  pack, diff). Strictly more truthful and strictly more expensive; deferred,
  not rejected -- it is the only thing that can catch aliased writes.

## Consequences

- The level is **opt-in** (absent from `DEFAULT_LEVELS`): the standard is
  strict enough that enabling it for every consumer by default would fail
  packs that are merely typical.
- Aliased globals (`const w = window; w.X = ...`), computed member access
  and `eval` are **not** caught. Documented as the known ceiling rather than
  papered over.
- Same-origin iframes can reach `parent.*` and bypass every rule; only a
  sandboxed iframe boundary would close that, which is an upstream ComfyUI
  change.
- Because the namespace derives from `DisplayName`, renaming a pack's
  display name is a breaking change for its own extension ids.
