# `coverage`

> Cross-references the nodes your pack registers against the nodes your
> workflows actually use. A pack that registers 40 nodes and exercises 3 gets a
> number, not a pass.

| | |
|---|---|
| **Needs** | nothing -- static, no install, no server, no imports |
| **Default** | no (opt-in) |
| **Fails the run** | yes |
| **Source** | `orchestration/levels/coverage.py`, `comfyui/coverage.py` |

Despite testing workflows, this level installs nothing. It reads
`NODE_CLASS_MAPPINGS` statically out of your source and reads your workflow
JSONs as data, so it runs against a bare checkout in a second.

## How it works

`analyze_coverage(node_dir, input_coverage=...)` does the whole job:

1. Statically scans the pack for `NODE_CLASS_MAPPINGS` to get the
   **registered** set. Nothing is imported.
2. Reads every discovered workflow JSON and collects the node types used.
3. Credits nodes reached indirectly through a dispatcher's backend map --
   reported separately, so you can see how much of the score is direct.
4. Checks any values declared in `[test.coverage.inputs]` against the values
   actually saved in the workflows.

The result is printed as a ratio and a percentage:

```
Coverage: 37/40 registered nodes used across 6 workflow(s) (93%)
  (4 of those credited via dispatcher backend-map tracing, not a direct
   workflow reference)
```

## Zero registered nodes is a hard failure

If the scan finds no registered nodes, the level raises rather than reporting a
vacuous 0/0. The error says why, because the cause is almost never "this pack
has no nodes":

> Found 0 registered nodes -- this almost always means the static
> `NODE_CLASS_MAPPINGS` scan couldn't recognize this pack's registration
> pattern (e.g. a dict built from a function call, or an unsupported
> comprehension shape), not that the pack truly registers no nodes.

A dict assembled by a helper, an exotic comprehension, or registration through
ComfyUI's V3 `comfy_entrypoint()` alone will all scan as zero. Treat this as
"the scanner does not understand your pack", not as a coverage result.

## Declared input values

`[test.coverage.inputs]` turns coverage from "was this node used" into "was
this node used *with these values*":

```toml
[test.coverage.inputs]
inputs = { GeomPackLoadMesh = { file_path = ["3d/cube.glb", "3d/sphere.glb"] } }
```

Each value must appear as that input's **saved value** on at least one node of
that type across your workflows. Values are lists of strings -- a bare string
is a config error. Each declared input is reported on its own line, and any
value not exercised fails the level alongside untested nodes.

Use it for the case coverage otherwise misses: one loader node exercised only
with the one file format that happens to work.

## What it does not catch

- **Reachability, not execution.** A node referenced by a workflow counts even
  if that workflow is never run, is excluded by a backend list, or fails at
  runtime. Pair it with `execution`.
- **Static scanning only.** Anything computed at import time is invisible.
- **No per-node exemption.** A deliberately unexercised node (a debug utility,
  a deprecated alias) cannot be excluded -- it will fail the level until a
  workflow uses it.

## Config

```toml
[test]
levels = ["syntax", "coverage", "install", "registration", "execution"]

[test.coverage]
inputs = { MyLoader = { path = ["assets/a.glb"] } }
```

Opt-in: `coverage` must be listed explicitly. It requires no resources, so
listing it pulls nothing else in -- it can be the only level you run.

`comfy-test coverage` runs the same analysis standalone, with `--json` and a
`--strict` exit code, which makes it usable as a pre-commit hook.

## See also

- [The ladder](../levels.md) -- all 13 levels and the resource model
- [`execution`](execution.md) -- proves the workflows actually run
- [`comfy-test.toml` reference](../config.md#testcoverage)
