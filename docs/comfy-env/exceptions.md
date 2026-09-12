# comfy-env and exceptions

*An ordinary error in a worker comes back as `WorkerError`. A cancel is a
`BaseException` on both sides of the socket, as upstream made sure it would
be — but it only reaches a node that reports progress.*
{: .subtitle }

## ComfyUI background

How ComfyUI turns a throw into a red node, and why Cancel is a
`BaseException`, is the subject of its own page.

**[Read it first](comfyui-exceptions.md)**.

## Ordinary errors

A node exception in a worker is caught there and serialized as an error
frame carrying `error` (the message, `str(e)`), `traceback` (the formatted
worker traceback, which is the only place the original type name appears)
and, when the worker can tell, `error_kind` plus `oom_stats`; the host
re-raises it as `WorkerError` (`isolation/workers/base.py`).
`isolation/errors.py` then translates it back — for exactly two cases:

```python
# isolation/errors.py -- the closed vocabulary
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

Three parts, matching the three facts on the background page: two are
still breaks, the third was one and is now the part that works.

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
(`isolation/pool.py`) checks the host's flag and answers with an
interrupt if it is set. That is the entire cancel path. It rides on
`PROGRESS_BAR_HOOK`, which is also the entire progress path.

### What arrives is a `BaseException`

```python
# isolation/workers/_persistent_worker.py
class _InterruptedError(BaseException):
# isolation/workers/base.py
class InterruptRequested(RuntimeError):
```

Upstream chose `BaseException` so node code's `except Exception` cannot eat a
cancel, and the worker-side class follows it: `_call_parent` raises
`_InterruptedError` when the callback reply carries
`error_kind: "interrupt"`, the progress hook re-raises it untouched, and it
unwinds through the node's `except Exception` blocks exactly as upstream's
`InterruptProcessingException` would. Every handler in the worker that must
still turn it into an error frame names it explicitly —
`except (Exception, _InterruptedError)` — so the main loop catches it, stamps
`error_kind: "interrupt"`, and the host's `errors.py` re-raises upstream's
real `InterruptProcessingException`.

`InterruptRequested` is still a `RuntimeError`, and that is fine: it exists
only on the parent side, raised by `_handle_progress` and caught by its
caller `_handle_callback`, which turns it into the typed error reply. It never
passes through node code on either side.

The host's flag is not spent by the callback either. `_handle_progress` reads
it with `mm.processing_interrupted()`, which does not clear it (only
`throw_exception_if_processing_interrupted` does), and deliberately leaves it
set so ComfyUI's own per-node check finds it too. The flag is reset at the
next prompt start, not by comfy-env.

## Known gaps

| # | Gap | Status |
|---|---|---|
| 1 | ~~Cancel is swallowable — `RuntimeError` where upstream uses `BaseException`~~ | **fixed**: `_InterruptedError` is a `BaseException`, and the host reads the interrupt flag without clearing it |
| 2 | Cancel never reaches a node that does not drive a `ProgressBar` | deliberate, ADR-0018 |
| 3 | `WorkerError` masks the original exception type | open |
| 4 | A native crash (segfault) is not an exception at all — see the faulthandler readback in [logging](logging-approach.md) | handled, different mechanism |

## See also

- [How ComfyUI handles exceptions](comfyui-exceptions.md)
- [ADR-0018: Worker call timeout](adr/0018-worker-call-timeout.md) — the cancellation decision
- [Worker lifecycle](worker-lifecycle.md)
