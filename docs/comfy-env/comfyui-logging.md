# ComfyUI logging background

*How ComfyUI captures, stores and ships console output. This is the machinery
every comfy-env line has to travel through, and the reason worker output needs
help getting there.*
{: .subtitle }

The rest of this page assumes you know what `print()` and the `logging` module
actually do, what `sys.stdout` is as distinct from file descriptor 1, and what
`2>&1` means.

**[If any of that is fuzzy, read this page first](streams-and-descriptors.md)**.

## Where does ComfyUI log to?

A single `print()` in the ComfyUI process can end up in three places, and a
`logging` call in a fourth that raw writes never reach. Only the first is
unconditional.

| # | Destination | What it is | On by default |
|---|---|---|---|
| 1 | **The real terminal** | the original `sys.stdout` / `sys.stderr`, written last by `super().write(data)` (`app/logger.py:70`) | yes |
| 2 | **A 300-entry ring** | `deque(maxlen=capacity)`, `capacity=300` (`app/logger.py:97,103`), in memory, module-global | yes |
| 3 | **The browser terminal panel** | pushed over the websocket by `TerminalService`, which registers `on_flush(self.send_messages)` (`api_server/services/terminal_service.py:12`) | only while a client is subscribed |
| 4 | **A log file** | a `logging.FileHandler` added by `setup_logger` (`app/logger.py:140`); fed by `logging` records only, never by `print()` or by a traceback written to `sys.stderr` | **on Desktop yes, running `main.py` yourself no** |

Which of those a line reaches depends on how it was produced, not on which
stream it names. Both streams are wrapped, each by its own `LogInterceptor`,
and each writes through to the stream it wrapped, so a stdout write still
ends on fd 1 and a stderr write on fd 2:

| Produced by | Terminal | Ring | Browser panel | Log file |
|---|---|---|---|---|
| `print()`, or any write to `sys.stdout` | yes, fd 1 | yes | yes | **no** |
| any write to `sys.stderr` (an uncaught traceback, the `warnings` module, a pack writing to stderr) | yes, fd 2 | yes | yes | **no** |
| a `logging` record (ComfyUI's own `[INFO]` and `[WARNING]` lines, `logging.exception`) | yes, on **stderr** by default; with `--log-stdout`, below ERROR to stdout and ERROR and above to stderr | yes | yes | **yes**, at the file's own level |

The ring and the panel therefore see both streams merged in write order. The
file is fed only by the `logging` handler: a `print()` never lands there, and
neither does a traceback Python writes to `sys.stderr` on its own; only
`logging.exception` does.

Destination 4 is the one that depends on how you launched ComfyUI.

**ComfyUI Desktop always has a log**: `<install>/logs/comfyui.log`, plus
Electron's rotated `main.log` beside it. The Desktop wrapper produces those
around the server process; they are not `app/logger.py`'s file.

**Running `python main.py` yourself, you get nothing** unless you ask:

```bash
python main.py --verbose DETAIL comfyui.log
```

It briefly worked the other way. `comfyui_detail.log` was on by default for one
day — added 2026-07-29 (#15064), switched off 2026-07-30 (#15159) — and before
that `setup_logger` had no file output at all. The `if file_outputs is None`
default still sitting at `app/logger.py:111` is left over from that day;
`main.py` always passes a real list, so it never fires.

## The interception point

At startup ComfyUI replaces both standard streams with a wrapper:

```python
stdout_interceptor = sys.stdout = LogInterceptor(sys.stdout)   # app/logger.py:107
stderr_interceptor = sys.stderr = LogInterceptor(sys.stderr)   # app/logger.py:108
```

**This replaces the Python object, not the file descriptor.** That one
sentence governs everything on the comfy-env side: `sys.stdout` is a name in
one interpreter's memory, while fd 1 is a kernel resource a child process
inherits. Anything reaching the terminal without passing through the
`LogInterceptor` object is invisible to destinations 2, 3 and 4.

Each write fans out, terminal last:

```python
def write(self, data):                                  # app/logger.py:60
    entry = {"t": datetime.now().isoformat(), "m": data}
    with self._lock:
        self._logs_since_flush.append(entry)            # -> websocket, on flush
        if isinstance(data, str) and data.startswith("\r") \
                and not logs[-1]["m"].endswith("\n"):
            logs.pop()                                  # \r overwrites, so drop the last
        logs.append(entry)                              # -> the ring
    super().write(data)                                 # -> the terminal
```

The websocket half is deferred: `flush()` hands `_logs_since_flush` to every
registered callback and clears it, so the browser sees output in flush-sized
batches rather than per write.

## The ring is a replay buffer

The ring exists to backfill the browser. Opening the terminal panel makes two
calls (`api_server/routes/internal/internal_routes.py:22-43`):

```
GET   /internal/logs/raw        -> {"entries": list(get_logs()), "size": {...}}
PATCH /internal/logs/subscribe  -> start the live websocket feed
```

The websocket only carries what happens after subscription, so without the
ring a freshly-opened panel would be blank until the next line printed — no
startup banner, no earlier traceback.

Its unit is **write calls, not lines**. `logging.info("x")` costs one slot;
`print("a", "b")` costs four, because `print` writes each argument, each
separator and the newline separately. Real replay depth therefore varies with
how the code that produced it was written, and a chatty pack can push the
startup banner out of a 300-slot ring in well under 300 lines.

## Defaults worth knowing

| Setting | Default | Flag |
|---|---|---|
| Console level | `INFO` | `--verbose` (bare = `DEBUG`), or `--verbose LEVEL` |
| Stream for normal output | **stderr** | `--log-stdout` sends sub-ERROR records to stdout |
| Log file | none | `--verbose LEVEL FILE`, repeatable |
| Ring capacity | 300 writes | not exposed on the CLI |

`DETAIL` is a ComfyUI-specific level between `DEBUG` and `INFO`
(`comfy/internal_logging.py`), which is why the level list is
`('DEBUG', 'DETAIL', 'INFO', 'WARNING', 'ERROR', 'CRITICAL')` rather than the
stdlib's five.

## What this means for comfy-env

A subprocess inherits file descriptors, not Python objects. Its output lands
in destination 1 and nowhere else — visible in the terminal the server was
launched from, absent from the web UI. That is the whole problem
[comfy-env's logging](logging-approach.md) has to solve, and it is why
comfy-env forwards worker output over its own IPC channel instead of letting
the worker write to a console.
