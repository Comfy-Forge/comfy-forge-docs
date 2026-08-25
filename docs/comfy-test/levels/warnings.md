# `warnings`

> An opt-in antipattern report on how the pack is laid out. Things that are
> *probably* wrong but not provably so. **Never fails a build.**

| | |
|---|---|
| **Needs** | nothing -- static, no install, no server |
| **Default** | no (opt-in) |
| **Fails the run** | **no** -- report only |
| **Source** | `orchestration/levels/warnings.py` |

This level exists because [`syntax`](syntax.md) fails a build, and so may only
contain rules that are right every time. Everything that needs a human to judge
lives here instead.

!!! note "Report-only, deliberately"
    A gate that fails on a judgement call teaches people to ignore it -- and
    once ignored, it stops catching the cases where it was right. Findings are
    printed and the level passes regardless.

## The checks

| Check | Looks for |
|---|---|
| `layout` | node source outside `nodes/`. A short allowlist of files legitimately live at the pack root: `__init__.py`, `install.py`, `setup.py`, `prestartup_script.py`, `serialization.py` (required there by comfy-env, ADR-0015) and `conftest.py` |
| `weights` | model weights shipped inside the pack -- `.safetensors`, `.ckpt`, `.bin`, `.pth`, `.pt`, `.onnx`, `.gguf` |
| `abs-paths` | absolute paths baked into source. Almost always someone's dev box |
| `sys-path` | `sys.path` edits |
| `duplicates` | the same file vendored twice, matched by content hash |

Weights in the pack get their own check because the consequence is
non-obvious: they are invisible to ComfyUI's model resolution, cannot be shared
between packs, and are destroyed by a reinstall. They belong in ComfyUI's
`models/` tree.

## Output

Findings are grouped by check, each with its description:

```
[warnings] weights -- weights outside the pack
  models/encoder.safetensors (412.3 MB)

[warnings] abs-paths -- no hardcoded absolute paths
  nodes/loader.py:88: /home/andrea/data/checkpoints

Warnings check: 2 finding(s). None of these fail the build -- they need a
human to judge.
```

A clean run prints `Warnings check: clean`.

## Failure containment

Each check runs inside its own `try`. A check that raises is reported as
having failed *itself* and the rest continue:

```
[warnings] duplicates: check itself failed (OSError: ...)
```

A report-only level must never break a run, so a buggy check degrades to a
missing check rather than a red build.

## Scope

Scans `.py` files under the node directory, skipping `.git`, `__pycache__`,
`.venv`, `venv`, `node_modules`, `site-packages`, `lib`, `Lib`, `.pixi` and
`scripts/` -- the same skip set as `syntax`.

## Config

```toml
[test]
levels = ["syntax", "warnings", "install", "registration"]
```

Opt-in and resource-free, so it can run alone against a bare checkout.

The house rule for adding a check: keep it cheap, static, and honest about
false positives. If a check cannot be written without an allowlist of
legitimate exceptions, write the allowlist rather than leaving noise in.

## See also

- [The ladder](../levels.md) -- all 13 levels and the resource model
- [`syntax`](syntax.md) -- the conclusive rules, which do fail a build
- [`hazards`](hazards.md) -- the same report-only stance, applied to runtime
  behaviour rather than layout
