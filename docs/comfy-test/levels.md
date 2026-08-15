# Test levels

comfy-test runs a pack through an ordered pipeline of **11 levels**. Order
is intrinsic (it is the declaration order of the `TestLevel` enum), and each
level receives the environment the previous ones built --
see [ADR-0002](adr/0002-levels-are-an-ordered-pipeline.md).

The `levels = [...]` list in `comfy-test.toml` is a **set, not a sequence**:
reordering it changes nothing. Missing prerequisites are pulled in
automatically.

## The ladder

| # | Level | Asserts | Needs | Default |
|---|-------|---------|-------|---------|
| 1 | `syntax` | Project structure, CP1252-encodable source, no forbidden patterns | source only | yes |
| 2 | `coverage` | Every registered node appears in some workflow | registered nodes | **no** |
| 3 | `install` | ComfyUI + the pack install; paths resolve | -- | yes |
| 4 | `registration` | The server boots and the pack imports without error | `install` | yes |
| 5 | `javascript` | Frontend JS touches nothing it does not own | `install`, `registration` | **no** |
| 6 | `instantiation` | Node constructors run | `install`, `registration` | yes |
| 7 | `static_capture` | Workflows load in the UI; screenshots captured | `registration` | yes |
| 8 | `validation` | Workflows pass 3-level validation (schema, graph, introspection) | `registration` | yes |
| 9 | `execution_light` | Workflows execute; one screenshot each | `registration` | no |
| 10 | `execution` | Workflows execute with per-frame capture | `registration` | yes |
| 11 | `custom` | A pack-supplied hook returns cleanly | varies | **no** |

Levels 2, 5 and 11 are opt-in: add them to `levels` to run them.

## What each level actually catches

**`syntax`** -- structural and encoding checks, plus an opinionated house
rule: source containing `nn.Linear(`, `.cuda()` or `torch.autocast(` fails.
The intent is that node code routes device and layer construction through
ComfyUI's own facilities rather than hardcoding them. It is enforced on
*your* repository, so it is the level most likely to surprise a new adopter.

**`coverage`** -- compares registered node names against the nodes used by
your workflows. A pack that registers 40 nodes and exercises 3 gets a
number, not a pass. Fails loudly on zero registered nodes rather than
vacuously passing 0/0.

**`install`** -- builds the environment, or discovers an existing one. In
**attach** mode this level does almost nothing; see
[ADR-0003](adr/0003-two-install-paths-attach-and-fresh.md), because it
changes what a green run means.

**`registration`** -- the highest-value cheap level. Boots the real server
and confirms the pack imports and registers. Catches missing requirements,
import-time crashes and name collisions.

**`javascript`** -- static isolation lint of the served frontend
([ADR-0014](adr/0014-javascript-isolation-is-static.md)). Errors on literal
shared-realm violations, warns on heuristics, exempts `.mjs`.

**`instantiation`** -- constructs each node class. Catches `__init__` work
that assumes a GPU, a model file, or network access.

**`static_capture`** -- loads each workflow in the browser and screenshots
it without executing. Proves the graph renders and nodes are not red.

**`validation`** -- three tiers: schema (the JSON is well-formed for the
API), graph (links and slots are coherent), introspection (inputs match
what the nodes declare). Needs the injected helper pack
([ADR-0009](adr/0009-a-helper-pack-is-injected.md)).

**`execution_light`** / **`execution`** -- both run the workflows; they
differ only in capture cost. Pick one per lane
([ADR-0011](adr/0011-execution-light-is-a-level.md)).

**`custom`** -- runs a `run(ctx)` hook from your own repository for
assertions comfy-test cannot express.

## Selecting levels

```toml
[test]
levels = ["syntax", "install", "registration", "javascript", "execution"]
```

On the command line, `--level X` **truncates** the ladder at X -- except for
the four *terminal* levels (`static_capture`, `validation`,
`execution_light`, `execution`), where it **replaces** whichever terminal
the config chose. That asymmetry is deliberate and documented in
[ADR-0012](adr/0012-level-flag-swaps-terminals.md); it is how one config
serves lanes that need different runtime levels.

What actually ran is recorded in `results.json` under `provenance.levels`.
