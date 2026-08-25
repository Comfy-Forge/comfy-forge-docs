# `syntax`

> Static checks on your repository: it has a dependency file, its source
> encodes on Windows, and it does not hardcode what ComfyUI wants to control.

| | |
|---|---|
| **Needs** | nothing -- pure source scan, no install, no server |
| **Default** | yes |
| **Fails the run** | yes |
| **Source** | `orchestration/levels/syntax.py` |

The cheapest level, and the one **most likely to surprise a new adopter**: it is
the only default level that enforces a house style on code that already works.

## How it works

Three checks, in order, raising on the first failure:

1. **Project structure** -- the node directory must have `pyproject.toml` or
   `requirements.txt`. A pack with no dependencies still needs one; their
   absence is indistinguishable from a packaging mistake.
2. **cp1252 encodability** -- every `.py` file is read as UTF-8 and each
   character re-encoded to cp1252.
3. **Forbidden patterns** -- every `.py` file is scanned line by line.

Both file scans `rglob("*.py")` and skip `.git`, `__pycache__`, `.venv`,
`venv`, `node_modules`, `site-packages`, `lib`, `Lib`, `.pixi`, plus anything
starting with `_env_` or `.`. The pattern scan also skips `scripts/`.

## Why cp1252

ComfyUI on Windows runs under a cp1252 console. A source file containing a
character outside that codepage can raise `UnicodeEncodeError` when a traceback
or log line containing it is written -- surfacing as an unrelated crash on
somebody else's machine. Usual causes: curly quotes pasted from docs, emoji in
log strings, check marks in progress output. All invisible in most editors,
which is why the level names the codepoint:

```
nodes/loader.py:
  Line 47, col 32: RIGHT SINGLE QUOTATION MARK - not encodable in cp1252
```

## Forbidden patterns

These **fail** the level. The bar is *"wrong on a lane the pack claims to
support"*.

| Pattern | Use instead |
|---|---|
| `.cuda(`, `.to("cuda...")`, `.to(torch.device("cuda"))` | `comfy.model_management.get_torch_device()` |
| `torch.autocast(`, `torch.cuda.amp.autocast`, `torch.amp.autocast` | `comfy.ops` via `operations=` |
| `nn.Linear(`, `nn.Conv[123]d(`, `nn.ConvTranspose[12]d(` | `operations.Linear()`, `operations.Conv*d()`, ... |
| `nn.LayerNorm(`, `nn.GroupNorm(`, `nn.Embedding(` | `operations.LayerNorm()`, etc. |

Two different rationales are bundled here. **Device hardcoding** crashes on
every non-CUDA machine. **Raw `nn.` layers** work everywhere but opt out of
ComfyUI's VRAM accounting -- `comfy.ops` layers participate in the memory
manager's load/offload decisions, plain `torch.nn` ones do not, so the pack is
invisible to eviction under pressure.

The layer rules are anchored on the module, so lookalikes are not flagged:

```python
_NN = r'(?<![\w.])(?:torch\.)?nn\.'
```

Unanchored, this hit `torchsparse.nn.Conv3d` (a different library with no
`comfy.ops` equivalent), `spnn.Conv3d` and any alias *ending* in `nn`, and
`cudnn.Conv2d`.

## Warning patterns

These print and continue -- the code works, but a ComfyUI-aware equivalent does
more. Erroring on them would train people to ignore the level.

| Pattern | Suggested |
|---|---|
| `torch.load(` | `comfy.utils.load_torch_file()` |
| `torch.cuda.empty_cache(` | `comfy.model_management.soft_empty_cache()` -- dispatches across MPS/XPU/NPU/MLU/CUDA; the torch call is a no-op off CUDA |

## What it does not catch

Line-based regex over raw text, skipping only lines whose first non-whitespace
character is `#`. So:

- Matches **inside strings and docstrings count**; a trailing
  `# intentional` does not exempt its line.
- Anything assembled at runtime is invisible (`getattr(t, "cu" + "da")()`).
- Non-Python files are never scanned.
- **There is no suppression comment.** If a pattern is genuinely correct for
  your pack, the only escapes are moving the code into `scripts/` or dropping
  `syntax` from `levels`.

## Config

None. It is in the default set; omit it by listing `levels` without it. It
needs no resources, so it never pulls another level in.

## See also

- [The ladder](../levels.md) -- all 13 levels and the resource model
- [`warnings`](warnings.md) -- opt-in, report-only antipattern scan
- [`hazards`](hazards.md) -- opt-in report on in-process behaviour
