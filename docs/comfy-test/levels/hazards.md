# `hazards`

> An opt-in report on how your pack behaves once ComfyUI imports it into a
> process it shares with everyone else's. **Never fails a build.**

| | |
|---|---|
| **Needs** | nothing -- static AST analysis, no install, no server |
| **Default** | no (opt-in) |
| **Fails the run** | **no** -- report only |
| **Source** | `orchestration/levels/hazards.py` |

[`warnings`](warnings.md) asks whether the pack is laid out sanely. This asks
whether its code misbehaves in a shared process: device handling, object
ownership, import-time side effects, cancellation.

## Confidence bands

Findings are grouped by how sure the check is, not by how bad the outcome
would be. For a report-only level the useful question is *"if this fires, how
confident am I that something is actually wrong?"*

| Band | Meaning |
|---|---|
| **A** | presence proves it -- no context needed; a hit is a finding |
| **B** | near-certain -- rests on one fact about how ComfyUI works |
| **C** | worth a look -- the pattern is real, the consequence varies |
| **D** | judgement -- a human decides every time |

The band is printed with every finding and the order never changes. As the
source puts it: a report that mixes *"this crashes on every Mac"* with *"this
could be tidier"* gets skimmed, and then the first one goes unread too.

## The checks

**Band A -- presence proves it**

| Check | Looks for |
|---|---|
| `cpu-into-cuda` | a CPU-capable device reaching a CUDA-only API |
| `cuda-at-import` | `torch.cuda.*` at module scope |

**Band B -- near-certain**

| Check | Looks for |
|---|---|
| `input-mutation` | in-place write to a shared input object |
| `dist-conflict` | two distributions providing one import name |
| `cv2-contrib` | `cv2` usage the manifest does not support |
| `bare-pip` | `pip` invoked from PATH instead of `sys.executable` |
| `cuda-literal` | hardcoded CUDA device with no fallback |

**Band C -- worth a look**

| Check | Looks for |
|---|---|
| `import-network` | network access or `pip install` during import |
| `unrestored-patch` | a ComfyUI attribute replaced and never saved |
| `swallowed-registration` | broad `except` around node registration |
| `uncancellable-loop` | long loop with no interrupt check |

**Band D -- judgement**

| Check | Looks for |
|---|---|
| `hardcoded-precision` | fp16/bf16 not chosen by `model_management` |
| `cache-clear-in-loop` | allocator-wide free inside a loop |
| `import-global-mutation` | process-wide state changed at import |
| `unmanaged-model` | weights on GPU outside ComfyUI's management |

## Why input mutation matters

The `input-mutation` check only fires on sockets carrying an object ComfyUI
**owns, caches, and hands to every downstream consumer of the link**:

`MODEL` · `VAE` · `CLIP` · `CLIP_VISION` · `LATENT` · `CONDITIONING` ·
`CONTROL_NET` · `STYLE_MODEL` · `GLIGEN`

Writing into one of these reaches other branches of the graph. Writing into an
`IMAGE`, `INT` or `STRING` does not, so those are not flagged. This is the
class of bug that shows up as "my workflow behaves differently depending on
node order".

## How it analyses

Unlike `syntax` and `warnings`, which are line-regex scans, this level parses
your source into **AST trees** and follows the import closure from the pack's
entry points -- so "at import time" means genuinely reachable during import,
not merely "at module scope in some file". Checks that need socket types read
them off the node class; several cross-reference your declared requirements.

## Config

```toml
[test]
levels = ["syntax", "hazards", "install", "registration"]
```

Opt-in and resource-free -- it can run alone against a bare checkout, which
makes it cheap to run on every commit even though nothing it says is
conclusive.

The house rule for adding a check: put it in the band you can defend, not the
band you want. If you cannot say what a hit proves, it belongs in D or nowhere.

## See also

- [The ladder](../levels.md) -- all 13 levels and the resource model
- [`warnings`](warnings.md) -- the layout-level report, same report-only stance
- [`syntax`](syntax.md) -- the conclusive device-and-layer rules that do fail
