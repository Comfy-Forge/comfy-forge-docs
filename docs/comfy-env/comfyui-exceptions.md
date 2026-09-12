# How ComfyUI handles exceptions

*A node that throws becomes a red node and an error dialog. A Cancel press
becomes an exception too — but one designed so that node code cannot
accidentally catch it. The second half is the part that matters across a
process boundary.*
{: .subtitle }

## Python background: two kinds of exception

Every Python exception inherits from `BaseException`. Almost all of them —
`ValueError`, `RuntimeError`, `FileNotFoundError`, anything you would raise
yourself — inherit from `Exception`, one level down:

```
BaseException
├── Exception              <- "something went wrong"    caught by `except Exception`
│   ├── RuntimeError
│   ├── ValueError
│   └── ...
├── KeyboardInterrupt      <- "stop"                     NOT caught by `except Exception`
├── SystemExit             <- "exit"                     NOT caught by `except Exception`
└── GeneratorExit
```

The split is deliberate. `except Exception` is the idiom for "handle any error
and carry on". The three direct children of `BaseException` are things you
must **not** carry on from — a user hitting Ctrl-C, a call to `sys.exit()`.
They sail through `except Exception` untouched, so a program that swallows all
errors can still be stopped.

A library that wants its own "stop everything" signal to have the same
property subclasses `BaseException` directly. That is what ComfyUI does.

## A node that throws

The executor wraps each node call and catches two things
(`execution.py`):

```python
except comfy.model_management.InterruptProcessingException as iex:
    logging.info("Processing interrupted")
    return (ExecutionResult.FAILURE, {"node_id": real_node_id}, iex)

except Exception as ex:
    typ, _, tb = sys.exc_info()
    exception_type = full_type_name(typ)          # e.g. "builtins.FileNotFoundError"
    ...
    logging.error(f"!!! Exception during processing !!! {ex}")
    logging.error(traceback.format_exc())
    if comfy.model_management.is_oom(ex):
        tips = "This error means you ran out of memory on your GPU..."
        comfy.model_management.unload_all_models()
    elif isinstance(ex, RuntimeError) and "mat1 and mat2 shapes" in str(ex) and "Sampler" in class_type:
        tips = "...make sure the correct CLIP file(s) and type is selected."
```

The second clause is the ordinary path. It records **the exception's fully
qualified type name**, the message, the traceback and the formatted inputs,
and ships them to the frontend as `execution_error`. The frontend paints the
node red and shows the dialog.

Two things hang off `exception_type` and `isinstance` here: the OOM detection
that unloads every model, and the "wrong CLIP" tip keyed on `RuntimeError`.
Anything downstream that special-cases an error — the frontend, ComfyUI-Manager
heuristics, telemetry — keys on that type name.

## Cancel is an exception, on purpose

The Cancel button is `POST /interrupt` (`server.py`), which does one
thing: set a module-level flag.

```python
# comfy/model_management.py
class InterruptProcessingException(BaseException):     # <- not Exception
    pass

interrupt_processing = False

def interrupt_current_processing(value=True):
    with interrupt_processing_mutex:
        interrupt_processing = value

def throw_exception_if_processing_interrupted():
    with interrupt_processing_mutex:
        if interrupt_processing:
            interrupt_processing = False                 # <- cleared BEFORE raising
            raise InterruptProcessingException()
```

Nothing is interrupted by the button. The flag just sits there until
something **polls** it. And the polls are everywhere:

| Poll site | Granularity |
|---|---|
| `nodes.py` `before_node_execution` | once per node |
| `comfy/ops.py` `run_every_op` | **every Linear and Conv forward** — 14 call sites in `ops.py` |
| `comfy/sd.py` | every tile of a tiled VAE encode/decode |
| `comfy/context_windows.py` | every context window |
| `comfy/ldm/minimax_music/ar.py` | every autoregressive step |

So a running sampler checks the flag many times per step. The moment it sees
it, `throw_exception_if_processing_interrupted` raises, and the exception
unwinds through the model code, through the node, through the executor's
first `except` clause, and the prompt stops.

### Why `BaseException`

Look at what the exception has to pass through on the way out: **the node's
own code.** Node authors write things like

```python
for tile in tiles:
    try:
        result = process(tile)
    except Exception:
        continue          # skip a bad tile, keep going
```

If `InterruptProcessingException` inherited from `Exception`, that loop would
catch it, log nothing, and continue to the next tile. The user pressed Cancel;
the node kept running. Making it a `BaseException` means `except Exception`
cannot see it, for the same reason it cannot see Ctrl-C.

### Why the flag is cleared before raising

`interrupt_processing = False` happens *inside* the check, before the `raise`.
That makes the interrupt **one-shot**: whoever polls first consumes it. It
prevents the next prompt from being cancelled by a stale flag — but it also
means that if the raised exception is somehow swallowed, the cancel is gone.
There is no second chance; the flag is already `False`.

Upstream never has to worry about that, because `BaseException` guarantees the
exception is not swallowed by ordinary code.

## What this means for a second process

Three separate facts, each of which is a gap on its own:

1. **The flag is a module global in the host's `comfy.model_management`.** A
   worker imports its own copy of that module, with its own flag, which
   nothing ever sets. Every poll site in the worker — every `run_every_op`,
   every tiled VAE step — reads `False` forever.
2. **The only way a cancel reaches a worker is as a reply to something the
   worker sent.** If the node drives a `ProgressBar`, the progress callback's
   reply can say "stop". If it does not, there is no channel.
3. **What the worker raises on that reply is not a `BaseException`.**
   comfy-env's `_InterruptedError` and `InterruptRequested` both subclass
   `RuntimeError` — so the tile loop above swallows it, and because the host
   already cleared its flag before replying, the cancel is lost for good.

[How comfy-env handles it](exceptions.md) covers each.

## See also

- [How comfy-env handles it](exceptions.md)
- [Worker lifecycle](worker-lifecycle.md) — what happens when a node call is killed rather than cancelled
