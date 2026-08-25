# Test levels

comfy-test runs a pack through **13 levels**. They execute in a fixed order --
the declaration order of the `TestLevel` enum -- but they do **not** all depend
on the previous one.

The `levels = [...]` list in `comfy-test.toml` is a **set, not a sequence**:
reordering it changes nothing. Missing prerequisites are pulled in
automatically.

## The resource model

A level never names another level. It declares the **resources** it consumes,
and the engine works out what provides them
([ADR-0002](adr/0002-levels-are-an-ordered-pipeline.md)):

| Resource | Provided by | Means |
|---|---|---|
| *(none)* | -- | static checks on your source; nothing is installed |
| `env` | [`install`](levels/install.md) | a built environment with your pack in it |
| `server` | [`registration`](levels/registration.md) | a running ComfyUI |
| `api` | [`registration`](levels/registration.md) | an API client against it |

So `instantiation` needs `env` but not a server, and four levels need nothing
at all -- they run against a bare checkout in seconds.

## The ladder

| # | Level | Asserts | Needs | Default |
|---|-------|---------|-------|---------|
| 1 | [`syntax`](levels/syntax.md) | Structure, cp1252-safe source, no forbidden patterns | -- | yes |
| 2 | [`coverage`](levels/coverage.md) | Every registered node appears in some workflow | -- | **no** |
| 3 | [`warnings`](levels/warnings.md) | Antipattern report on pack layout | -- | **no** |
| 4 | [`hazards`](levels/hazards.md) | Report on in-process behaviour, by confidence band | -- | **no** |
| 5 | [`install`](levels/install.md) | ComfyUI + the pack install; paths resolve | -- | yes |
| 6 | [`registration`](levels/registration.md) | The server boots and the pack imports | `env` | yes |
| 7 | [`javascript`](levels/javascript.md) | Frontend JS touches nothing it does not own | `server` | **no** |
| 8 | [`instantiation`](levels/instantiation.md) | Node constructors run | `env` | yes |
| 9 | [`static_capture`](levels/static_capture.md) | Workflows render; screenshots captured | `server` | yes |
| 10 | [`validation`](levels/validation.md) | Schema, graph, introspection | `api` | yes |
| 11 | [`execution_light`](levels/execution_light.md) | Workflows execute; one screenshot each | `server` | **no** |
| 12 | [`execution`](levels/execution.md) | Workflows execute; canvas recorded as video | `server` | yes |
| 13 | [`custom`](levels/custom.md) | A pack-supplied hook returns cleanly | `server`, `api` | **no** |

Six levels are opt-in: `coverage`, `warnings`, `hazards`, `javascript`,
`execution_light` and `custom`. Note that setting `[test] custom` enables its
level automatically.

Two of them -- `warnings` and `hazards` -- are **report-only and never fail a
build**, deliberately: a gate that fails on a judgement call teaches people to
ignore it.

## Selecting levels

```toml
[test]
levels = ["syntax", "install", "registration", "javascript", "execution"]
```

**That list is the whole story.** There is no command-line override: every
lane, local or hosted, runs exactly what your `comfy-test.toml` says. What ran
is recorded in `results.json` under `provenance.levels`, and it always agrees
with the config.

!!! note "`--level` was removed"
    A `--level X` flag used to let a lane override this list, truncating the
    ladder at X and swapping the terminal level. Every hosted lane passed the
    same `--level execution`, so it varied nothing -- while silently dropping
    [`custom`](levels/custom.md), which sits above `execution` in the enum.
    It is gone ([ADR-0012](adr/0012-level-flag-swaps-terminals.md)).

    To run static checks alone, without an environment or a server, use
    [`comfy-test lint`](commands.md) and `comfy-test coverage` rather than a
    level selector.

## Cheapest useful set

The four resource-free levels need no install and no server, so they are worth
running on every commit even locally:

```toml
[test]
levels = ["syntax", "coverage", "warnings", "hazards"]
```
