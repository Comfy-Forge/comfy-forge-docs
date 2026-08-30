# `windows-cpu`

> Windows with an ordinary ComfyUI checkout. The lane that catches path
> separators, cp1252 console encoding and file locking.

| | |
|---|---|
| **OS / accelerator** | Windows / CPU |
| **Install method** | `manual` -- venv + a ComfyUI checkout |
| **Runner** | `windows-latest` (GitHub-hosted, **2x** billing) |
| **Install path** | **attach** |
| **Config key** | `[test.windows]` |
| **Also accepts** | `windows`, `windows_cpu` |

## What it catches that Linux does not

- **Path handling.** Anything joining paths with `/` by hand, or assuming
  `os.sep == "/"`.
- **cp1252.** ComfyUI on Windows runs under a cp1252 console, so a curly quote
  or an emoji in a log string can raise `UnicodeEncodeError` from a traceback
  on somebody else's machine. [`syntax`](../levels/syntax.md) fails that
  statically, before this lane ever runs.
- **File locking.** Windows refuses to delete or rename an open file. Packs
  that leave handles open surface here and nowhere else.
- **`.exe` suffixes.** The venv interpreter is `Scripts/python.exe`, not
  `bin/python`.

## What it does not prove

Installability -- this is an **attach** lane, same as `linux-cpu`. See
[Lanes](../lanes.md#what-a-green-cell-means-read-this-one).

## Gotchas

- **It bills at 2x.** A full hosted matrix spends most of its budget on the
  Windows and macOS lanes; see [Lanes](../lanes.md) before adding more.
- **Case insensitivity hides bugs.** `import MyNodes` against `mynodes.py`
  passes here and fails on `linux-cpu`. Run both.

## See also

- [`windows-portable-cpu`](windows-portable-cpu.md) -- the same OS, a very
  different ComfyUI
- [`windows-cuda`](windows-cuda.md) -- fresh install, with a GPU
