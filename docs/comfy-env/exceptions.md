# comfy-env and exceptions

*An ordinary error in a worker comes back as `WorkerError`. A cancel comes
back as a `RuntimeError` — which is the one thing upstream made sure it would
never be.*
{: .subtitle }

## ComfyUI background

How ComfyUI turns a throw into a red node, and why Cancel is a
`BaseException`, is the subject of its own page.

**[Read it first](comfyui-exceptions.md)**.

## Ordinary errors

A node exception in a worker is caught there, serialized with its type name,
message and traceback, and re-raised in the host as `WorkerError`
(`isolation/workers/base.py:59-63`). `isolation/errors.py` then translates
it back — for exactly two cases:

```python
# isolation/errors.py:54-58 -- the closed vocabulary
{"oom", "interrupt"}
```

An OOM is re-raised as a real `torch.cuda.OutOfMemoryError`, so upstream's
`is_oom` check fires and it unloads models. An interrupt is re-raised as
upstream's `InterruptProcessingException`. **Everything else stays
`WorkerError`.**

The message and worker traceback survive, so the dialog is readable. What is
lost is the **type**. `exception_type` in the error payload says
`comfy_env.isolation.workers.base.WorkerError` instead of
`builtins.FileNotFoundError` or the pack's own class. Anything keyed on that
name misfires: the frontend's special-casing, ComfyUI-Manager's heuristics,
and upstream's own "wrong CLIP" tip, which is gated on `isinstance(ex,
RuntimeError)` and so never fires for an isolated sampler.

`errors.py`'s own docstring states the rule it cannot keep: *"a `comfy_env.*`
name there breaks node invisibility."* Closing it means re-raising as the
original type when that type is importable in the host, falling back to
`WorkerError` only when it is not.

## Cancel

Three separate breaks, matching the three facts on the background page.

### The worker's flag is never set

`comfy.model_management.interrupt_processing` in the worker is a different
variable from the host's, and nothing writes it.
`grep -rn "interrupt_current_processing" src/` returns no worker-side call.
So every poll site in the worker — `run_every_op` on every Linear forward,
every tiled VAE step — reads `False`. A loader, decoder or mesh op that never
drives a `ProgressBar` **cannot be cancelled**; the queue looks stuck until
the node finishes.

This one is deliberate and recorded:
[ADR-0018](adr/0018-worker-call-timeout.md) — *"a node that never reports
progress is uncancellable."*

### The only channel is the progress reply

When a node does drive a `ProgressBar`, the worker's hook sends a
`report_progress` callback, and the parent's handler
(`isolation/pool.py:222-231`) checks the host's flag and answers with an
interrupt if it is set. That is the entire cancel path. It rides on
`PROGRESS_BAR_HOOK`, which is also the entire progress path.

### What arrives is a `RuntimeError`

```python
# isolation/workers/_persistent_worker.py:1481
class _InterruptedError(RuntimeError):
# isolation/workers/base.py:65
class InterruptRequested(RuntimeError):
```

Upstream chose `BaseException` so node code's `except Exception` cannot eat a
cancel. comfy-env chose `RuntimeError`, which it can. Measured:

```
upstream BaseException   -> survives except Exception
comfy-env RuntimeError   -> SWALLOWED by except Exception
```

And it is worse than a missed cancel, because of the one-shot flag: the host
cleared `interrupt_processing` *before* answering the callback. Once the
worker's `except Exception: continue` swallows the reply, the cancel is gone.
The user presses Cancel again, and again, and only a press that lands while
the node is *outside* its `try` block ever takes.

**Fix: one word.** Both classes subclass `BaseException`. The transport
already distinguishes them by type, so nothing else changes.

## Known gaps

| # | Gap | Status |
|---|---|---|
| 1 | Cancel is swallowable — `RuntimeError` where upstream uses `BaseException` | open; one-word fix |
| 2 | Cancel never reaches a node that does not drive a `ProgressBar` | deliberate, ADR-0018 |
| 3 | `WorkerError` masks the original exception type | open |
| 4 | A native crash (segfault) is not an exception at all — see the faulthandler readback in [logging](logging-approach.md) | handled, different mechanism |

## See also

- [How ComfyUI handles exceptions](comfyui-exceptions.md)
- [ADR-0018: Worker call timeout](adr/0018-worker-call-timeout.md) — the cancellation decision
- [Worker lifecycle](worker-lifecycle.md)
