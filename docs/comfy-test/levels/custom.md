# `custom`

> Runs a hook from your own repository against the live server. The escape
> hatch for assertions the generic levels cannot express.

| | |
|---|---|
| **Needs** | `server` and `api` (provided by [`registration`](registration.md)) |
| **Default** | no (opt-in, but see below) |
| **Fails the run** | yes |
| **Source** | `orchestration/levels/custom.py` |

Runs **last**, after execution, so your hook can inspect what the run
produced.

## Writing a hook

Point at a **file path relative to your pack**:

```toml
[test]
custom = "tests/my_check.py"
```

The file exposes `run(ctx)` -- or `check(ctx)`, both are accepted:

```python
def run(ctx):
    # ctx.api          live ComfyUI API client
    # ctx.server       the running server
    # ctx.node_dir     your pack's directory
    # ctx.paths        TestPaths for this environment
    # ctx.registered_nodes  tuple of node ids from /object_info
    # ctx.config       the parsed comfy-test.toml
    # ctx.log(msg)     goes into session.log like any level

    out = ctx.node_dir / "output" / "result.step"
    if not out.exists():
        raise AssertionError("expected a STEP file, got nothing")
    ctx.log(f"STEP file OK ({out.stat().st_size} bytes)")
```

**Raise to fail, return to pass** -- the same contract the built-in levels
use. Returning a `LevelContext` replaces the context for anything downstream;
returning anything else (including `None`) keeps the existing one.

The kinds of assertion this is for are domain-specific and unguessable from
outside: *"my node produced a valid STEP file"*, *"the output isn't
all-black"*, *"the mesh is watertight"*.

## Failure modes

Each is reported distinctly, so you can tell a broken hook from a real
finding:

| Situation | Result |
|---|---|
| `[test] custom` not set | logged, skipped, level passes |
| the path does not exist | fails, naming the resolved path |
| the file raises on import | fails as "failed to import" |
| no `run` or `check` defined | fails, telling you to add `def run(ctx)` |
| the hook raises | fails with the exception |

## It auto-enables

Although `custom` is not in the default set, **setting `[test] custom`
adds the level automatically** -- you do not also have to list it in `levels`.

!!! warning "`--level` can silently drop it"
    `custom` is the last member of the level enum, so a `--level execution`
    (index 9) truncates it away (index 10). Every hosted CI lane passes
    `--level execution`, which means a configured hook does not run there.
    Check `provenance.levels` for what actually ran.

## Trust

No new trust boundary: comfy-test already runs your pack's `install.py` and
executes its workflows, so running one more file from the same repository
changes nothing about what is trusted.

## See also

- [The ladder](../levels.md) -- all 13 levels and the resource model
- [`execution`](execution.md) -- runs before this and produces what you inspect
- [`comfy-test.toml` reference](../config.md) -- the `custom` key
