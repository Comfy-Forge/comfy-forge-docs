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

## What it misses

The scan matches ComfyUI's failure strings, so it catches packs that *fail*.
It does not catch the pack that **imports cleanly and registers nothing** --
core logs that case separately:

```
Skip <pack> module for custom nodes due to the lack of
NODE_CLASS_MAPPINGS or comfy_entrypoint (need one).
```

A pack in that state produces zero nodes in `/object_info` and still passes
this level unless it declares expected node names. Worth knowing if you
register through V3's `comfy_entrypoint()` alone.

!!! warning "On attach lanes this proves less than it looks"
    Hosted Linux and macOS lanes install `requirements.txt` and `install.py`
    in YAML with `|| true`, so **install errors are suppressed** before this
    level ever runs. "Catches missing requirements" is a fresh-lane property.
    Check `provenance.install_mode` before concluding anything.

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
