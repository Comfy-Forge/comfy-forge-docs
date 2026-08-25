# `javascript`

> Static collision lint of your frontend. ComfyUI auto-imports every pack's
> JS into **one shared browser page**, so this checks you touch nothing you
> do not own.

| | |
|---|---|
| **Needs** | `server` (the pack's `web/` is generated at server boot) |
| **Default** | no (opt-in) |
| **Fails the run** | yes, on any **error**-level finding |
| **Source** | `orchestration/levels/javascript.py`, `reporting/js_lint.py` |

Backend isolation buys nothing here: there is no per-pack browser boundary,
only one origin. See
[ADR-0014](../adr/0014-javascript-isolation-is-static.md).

## One pack, one namespace

Your pack gets exactly one prefix, derived from its **published identity** in
`pyproject.toml`:

1. `[tool.comfy] DisplayName`, lowercased and stripped to alphanumerics
2. failing that, `[project] name` with any `comfyui-` prefix removed

```toml
# either of these is enough -- nothing goes in comfy-test.toml
[project]
name = "comfyui-geometrypack"     # -> namespace "geometrypack"

[tool.comfy]
DisplayName = "GeomPack"          # -> namespace "geompack"
```

This is zero-config for any normally packaged pack: both fields are required
to publish to the Comfy Registry anyway.

!!! danger "No identity is a hard failure"
    If `pyproject.toml` declares neither, the level **errors out** rather than
    guessing:

    ```
    Pack has no published identity, so its JS namespace cannot be determined
    ```

    It used to guess a prefix from the folder name and quietly downgrade the
    naming rules to warnings -- a green run that proved much less than it
    looked. Guessing is gone; a prefix the pack cannot prove it owns is not a
    basis for letting it claim names in a shared page.

!!! note "Packs bundling several namespaces must rename"
    There is no config key for declaring extra prefixes. A pack that absorbed
    other packs and kept their JS prefixes (`comfy3d.*`, `unirig.*` alongside
    `geompack.*`) will fail this level until that JS is renamed under one
    namespace. That is the intended outcome, not an oversight
    ([ADR-0014](../adr/0014-javascript-isolation-is-static.md)).

## What it lints

The web directory is resolved from the **installed** copy under
`custom_nodes/` when an install ran, falling back to the source tree. A pack
shipping no web dir logs and passes.

Findings split into errors (fail) and warnings (report). The lint covers the
ways packs collide in a shared realm: global writes, duplicate
`registerExtension` names, unguarded `message` listeners, monkeypatches of
shared objects, and shared DOM or storage writes.

## `.mjs` is exempt only when unreachable

The lint scans every auto-imported `.js` **plus any `.mjs` reachable by a
relative import from one** -- explicitly so a leaking file cannot hide behind
the extension.

That matters because ComfyUI's auto-import glob is `**/*.js` only, but
`web.static` serves the *whole* directory: an auto-loaded one-line `.js` doing
`import "./bundle.mjs"` executes it in the same realm, same `window`, same
LiteGraph prototypes. Renaming does not remove a file from the shared realm --
it only removes it from the auto-import list.

## Output

Alongside the pass/fail, the level writes `javascript.json` into the run
output for the dashboard, recording the web dir, the namespaces used, and
and the resolved namespace.

## Config

The level itself takes **no configuration** -- there is no `[test.javascript]`
section. The only choice is whether to run it:

```toml
[test]
levels = ["syntax", "install", "registration", "javascript"]
```

Opt-in. It needs `server`, so listing it pulls in `install` and
`registration` automatically. `comfy-test lint --check javascript` runs it
standalone against a source tree, with no install and no server.

## See also

- [The ladder](../levels.md) -- all 13 levels and the resource model
- [ADR-0014](../adr/0014-javascript-isolation-is-static.md) -- why this is
  static, and what it cannot see
- [`hazards`](hazards.md) -- the Python-side equivalent: what your pack does
  to a process it shares
