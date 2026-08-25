# `registration`

> Boots the real ComfyUI server and confirms your pack imports and registers.
> The highest-value cheap level.

| | |
|---|---|
| **Needs** | `env` (provided by [`install`](install.md)) |
| **Default** | yes |
| **Fails the run** | yes |
| **Source** | `orchestration/levels/registration.py` |

This is the level that catches the single most common real-world failure: a
pack that works on the author's machine and dies on import everywhere else. It
is also the **provider** of both the `server` and `api` resources, so every
capture, validation and execution level depends on it.

## How it works

1. **Start or attach.** Fresh runs launch `ComfyUIServer` with the pack's env
   vars and any `--novram` / `--vram-debug` flags. Attach runs wrap the
   already-running server in `AttachedServer` and health-check it -- a
   non-responding URL fails immediately, since attach mode expects CI to have
   booted it first.
2. **Scan the log for import errors.** `get_import_errors()` reads the server's
   startup output. Any hit stops the server and fails the level with every
   error included.
3. **Read the registry.** `GET /object_info`; its keys become
   `registered_nodes`, which later levels consume.
4. **Record provenance.** The running server's own `system_stats` version
   report overrides the `pyproject` value read during `install` -- what is
   running beats what was cloned.

In attach mode the log comes from the workflow's `server.log` under
`COMFY_TEST_LOGS_DIR`, so the import-error check keeps identical semantics.

## What it catches

- **Missing requirements** -- an import of something never declared.
- **Import-time crashes** -- a module-scope `torch.cuda` call on a CPU lane, a
  missing model file, a network fetch during import.
- **Name collisions** -- two packs claiming the same node id.

## Imported fine, registered nothing

A clean import is not a registration, and this is the level that says so.

ComfyUI's own loader will not tell you. `load_custom_node` takes the V1 branch
on any `NODE_CLASS_MAPPINGS` that is not `None` -- **an empty dict included** --
iterates nothing, and returns `True` (`nodes.py:2292-2301`). No warning is
logged, because from core's point of view nothing went wrong. (The
`Skip <pack> module ... lack of NODE_CLASS_MAPPINGS or comfy_entrypoint`
message covers a different case: neither symbol present at all.)

So the level counts the nodes that are **yours**, using upstream's own
attribution: every `/object_info` entry carries `python_module`
(`server.py:765`), set from `RELATIVE_PYTHON_MODULE` as `custom_nodes.<dir>`
(`nodes.py:2296`), where `<dir>` is your pack's directory under
`custom_nodes/`. Zero entries attributed to your pack **fails the level**.

The usual causes: `__init__.py` does not export `NODE_CLASS_MAPPINGS`, exports
it empty, or builds it under a condition that was false on this lane -- an
optional dependency that did not install, an accelerator check.

!!! note "A server that reports no attribution is not a failure"
    If *no* entry in `/object_info` carries `python_module` -- a ComfyUI
    predating that field -- the level logs a warning and moves on. Unknown
    attribution is not the same as zero nodes.

## What it still misses

The log scan matches ComfyUI's failure strings, so it catches packs that
*fail* to import. It cannot see a node that registers but is broken: that is
what [`instantiation`](instantiation.md) and the execution levels are for.

## Config

No keys of its own. It is in the default set, and because it provides `server`
and `api`, requesting any capture or execution level pulls it in automatically
whether or not you list it.

Relevant environment: `COMFY_TEST_VERBOSE` echoes every server line rather than
the interesting ones; `COMFY_TEST_SHOW_CONSOLE_ERRORS` surfaces browser console
errors. See the [settings reference](../settings.md).

## See also

- [The ladder](../levels.md) -- all 13 levels and the resource model
- [`install`](install.md) -- builds the env this level boots
- [`instantiation`](instantiation.md) -- goes further: actually constructs each
  node class
