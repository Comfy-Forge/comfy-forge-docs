# comfy-env's logging

*Where every line comfy-env produces goes, why worker output needs an IPC
channel to get there at all, and what still falls on the floor.*
{: .subtitle }

## ComfyUI background

ComfyUI replaces `sys.stdout` and `sys.stderr` with a wrapper that fans each
write out to four places:

1. **The terminal**: the original stream, written last and visible in the Terminal/Shell ComfyUI is being launched from.
2. **An in-memory ring**: the last 300 *write calls*, always stored so if you close the browser and reconnect they can be replayed in the browser's terminal panel.
3. **The browser's terminal panel**: over the websocket while a
   client is subscribed.
4. **A file**: always on for Desktop, off by default for manual installations

The rest of this page assumes you know how ComfyUI's logging works, in detail.

**[If you don't, read this page first](comfyui-logging.md)**.

One sentence from it carries the whole design here: **interception replaces the
Python object, not the file descriptor.** A subprocess inherits fd 1 and fd 2,
never `sys.stdout`, so anything a child writes lands in the launching terminal
and in none of ComfyUI's other destinations.

comfy-env's entire architecture is subprocesses. So this is not an edge case
for us, it is the normal case.

## The shape of it

comfy-env does not use the `logging` module for user-facing output. It prints.
Four files reach for `logging.getLogger` and all four are internal;
everything an operator ever reads is a `print()` with a bracketed prefix:

| Prefix | Sites | Emitted by |
|---|---|---|
| `[comfy-env]` | 149 | the normal user-facing voice — 43 direct `print()` on the runtime path, 106 through the install path's `log` callback |
| `[SubprocessWorker]` | 15 | parent-side worker spawn and health, mostly behind `COMFY_ENV_DEBUG_WORKER` |
| `[meta-scan]` | 9 | metadata scan and proxy synthesis |
| `[worker:<name>]` | 1 | the host re-emitting a line forwarded from a worker |

The install path does not print directly. It threads a
`log: Callable[[str], None]` parameter, defaulting to `print`, through every
function that has something to say — `toml_generator.py` alone takes it in
nine signatures. That indirection is what lets `install()` swap in a tee that
also writes `install.log`, without any function below it knowing.

This is deliberate rather than lazy. comfy-env runs inside the ComfyUI process
for `setup_env()` and `register_nodes()`, so by the time any of it executes,
`sys.stdout` is already a `LogInterceptor` — a plain `print()` reaches the web
UI, the ring and the terminal with no configuration, no handler, and no
dependency on ComfyUI's logger surviving a refactor. Adding a `logging` handler
would buy levels and cost a coupling.

Roughly a third of those calls pass `file=sys.stderr` (53 of 152). Both streams
are intercepted, so the choice affects ordering and `--log-stdout` behaviour,
not visibility.

## Where comfy-env writes

| # | Sink | Contents | When |
|---|---|---|---|
| 1 | **The ComfyUI console** (and therefore the web UI) | everything printed in the host process | always |
| 2 | **`<workspace>/install.log`** | the full install narration, plus subprocess stdout/stderr at a verbosity the console never shows | during `install()`, which runs outside the host process and so never reaches sink 1 |
| 3 | **`$TMPDIR/comfy_worker_debug.log`** | worker-internal trace, 102 call sites | **always**, unrotated |
| 4 | **`$TMPDIR/comfy_worker_watchdog.log`** | every thread's stack, every 60 s | `COMFY_ENV_DEBUG_WATCHDOG` |
| 5 | **`$TMPDIR/comfy_worker_faulthandler.log`** | native traceback on SIGSEGV, SIGABRT and friends | always armed |

Sinks 3 to 5 are files because the worker cannot safely print — see below.

### The files come back on a crash

Those file sinks are not write-only, which is the answer to "why write
somewhere nobody looks". When a worker dies, the parent assembles a diagnostic
block before raising — `_worker_exit_diagnostic`,
`isolation/workers/subprocess.py:316-354`: the exit code
decoded to a signal name, then the **last 20 lines of the worker debug log**
and the **last 20 lines of the faulthandler dump**, read off disk and printed
into the ComfyUI console.

So the moment the information matters, it crosses back into all four of
ComfyUI's destinations by a path that does not depend on the dead worker's
streams. The
faulthandler basename is a shared constant in `_ipc_shared.py:103` rather than
a literal on each side, because the two spellings drifted apart once and the
readback silently found nothing.

One detail of that arrangement is easy to misread. The worker calls
`faulthandler.enable` twice — once on `sys.stderr`, then again on the file —
and the second call's comment says it "also" dumps to a file. It does not:
CPython's faulthandler holds a single descriptor, so the second call
**replaces** the first. Verified by segfaulting a process with both calls in
place: stderr received 0 bytes and the file received the whole dump.

That is the right outcome and the reason the crash row above says the traceback
reaches the web UI. The stderr `enable` is a fallback for the case where opening
the file throws, and worth keeping for exactly that.

## The worker boundary

This is the part that matters. A worker is a subprocess in a different
interpreter with a different environment, spawned like this
(`isolation/workers/subprocess.py:570-576`):

```python
self._process = subprocess.Popen(
    cmd,
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=None,   # Inherit parent stderr (avoids pipe deadlock with tqdm)
    ...
)
```

Neither `stdout=PIPE` nor an inherited stdout would do. A pipe nobody drains
fills its kernel buffer and deadlocks the worker mid-call; an inherited fd 1
would reach the terminal but not the web UI, and would interleave with the
host's own output unlabelled. So comfy-env forwards output **in band, over the
IPC socket it already has**, and lets the host re-emit it.

The worker replaces `print` and adds a root logging handler
(`isolation/workers/_persistent_worker.py:999-1032`):

```python
def _forwarded_print(*args, **kwargs):
    sep = kwargs.get('sep', ' ')
    message = sep.join(str(a) for a in args)
    transport.send({"type": "log", "message": message})
    wlog(f"[print] {message}")

builtins.print = _forwarded_print                      # :1012

class SocketLogHandler(logging.Handler):               # :1020
    def emit(self, record):
        transport.send({"type": "log", "message": self.format(record)})
```

and the parent recognises those frames anywhere in the stream, not just in
replies (`isolation/workers/subprocess.py:803`):

```python
if kind == "log":
    print(f"[worker:{self.name}] {frame.get('message', '')}",
          file=sys.stderr, flush=True)
    return True
```

That last `print` runs **in the ComfyUI process**, so it hits the
`LogInterceptor` like any other line. A node's `print()` inside an isolated env
shows up in the browser terminal panel, prefixed with which worker said it.

### What crosses and what does not

| Node code does | Route | Terminal | Web UI |
|---|---|---|---|
| `print(...)` | hijacked builtin -> IPC -> host | yes | **yes** |
| `logging.warning(...)` and above | `SocketLogHandler` -> IPC -> host | yes | **yes** |
| `logging.info(...)` | `SocketLogHandler` -> IPC -> host, at the host's level | yes | **yes** |
| `sys.stdout.write(...)` | worker fd 1, which is `DEVNULL` | **no** | **no** |
| `tqdm`, C-library writes to fd 2 | worker fd 2, inherited | yes | no |
| a native crash (SIGSEGV, SIGABRT) | `faulthandler` -> file -> parent reads it back | yes | **yes** |

The `logging.info` row is a second real hole, and a quiet one. The worker
attaches `SocketLogHandler` to `logging.root` but never sets the root
**logger's** level, and `grep -rn setLevel src/comfy_env/` returns nothing.
The host does set it — `logger.setLevel(min([console_level, *file_levels]))`
resolves to 15 (`DETAIL`) under default args (`app/logger.py:116`) — so
INFO flows there. In a worker the root logger sits at the interpreter default
of `WARNING`, which filters the record *before* any handler is consulted.
Measured: a handler on root with no `setLevel` captures `WARNING` and `ERROR`
and never sees `INFO`. A pack that logs at INFO loses every line the moment it
is isolated, while `warning` and above keep working — which is precisely why
it looks like logging is fine.

The `sys.stdout.write` row is a real hole too. The worker hijacks `builtins.print` but never
reassigns `sys.stdout` or `sys.stderr` (grepped: zero assignments in
`isolation/workers/`), so a pack that writes through the stream object rather
than the builtin vanishes completely — not even the launching terminal sees it.
That is strictly worse than the plain-subprocess case, because at least a plain
subprocess reaches the terminal.

The fourth row is the deliberate trade named in the `stderr=None` comment.
Progress bars write escape sequences to fd 2 in tight loops; forwarding them
line by line over a socket would be both slow and unreadable, and piping them
risks the deadlock. Inheriting the descriptor keeps them working in the
terminal at the cost of the web UI.

Two smaller fidelity losses in the forwarder: it honours `sep` but ignores
`end`, so `print(x, end="")` arrives as a full line; and it ignores `file=`, so
a node's deliberate stderr write is re-emitted on the host's stderr regardless.

### Why not just print from the worker

`wlog`'s comment states the constraint directly
(`_persistent_worker.py:91,105`):

```python
"""Log to file only - stdout causes pipe buffer deadlock after many requests."""
...
# NOTE: Don't print to stdout here! After 50+ requests the pipe buffer
# fills up and causes deadlock (parent blocked on recv, worker blocked on print)
```

The worker's own diagnostic trace therefore goes to a file, not a stream. It
does not `fsync`, and the comment records why: 2.78 ms per line with, 0.02 ms
without, measured on ext4, against the 92 call sites there were at the time,
thirteen of them inside `_from_shm`'s per-node recursion. `flush()` already
survives a Python-level
crash; only a kernel panic loses the tail, and a kernel panic loses the worker
anyway.

Every worker on the machine appends to the *same* file, so each line carries an
identity prefix built from the pack directory and pid
(`_persistent_worker.py:87`):

```python
_WLOG_PREFIX = "%s:%d" % (os.path.basename(os.getcwd()) or "?", os.getpid())
```

Without it, two envs' lines interleave indistinguishably.

## Install-time logging

`install()` runs under ComfyUI-Manager as a separate `sys.executable`
subprocess, which means it is on the far side of the very boundary this page is
about: its output does not reach the web UI, and comfy-env cannot change that.
What it can do is keep a complete record on disk.

`_make_tee_log` (`install/helpers.py:47`) wraps the caller's log callback so
every line goes to both the console and `<workspace>/install.log`, which opens
with the interpreter and platform that produced it:

```
# comfy-env install log - 2026-09-10T14:22:03.481922
# Python: /home/u/ComfyUI/.venv/bin/python (3.12.7)
# Platform: linux
```

Two helpers hang off that tee:

- **`_run_streaming`** (`install/helpers.py:83`) runs a subprocess with both
  pipes drained live — stderr on a thread, stdout on the main loop — so pixi's
  output appears as it happens rather than in one dump at the end. It passes
  `stdin=DEVNULL` deliberately: stdin used to be inherited, so a child that
  decided to prompt blocked forever against a console nobody was watching,
  inside an install that looked hung.
- **`_log_subprocess`** (`install/helpers.py:69`) writes a completed
  subprocess's full stdout and stderr to the log file *only*, reached through
  the `tee.file` attribute. This is how `install.log` ends up more verbose than
  the console without making the console unreadable.

### The progress bar

`pixi install` produces almost nothing on a pipe. Its `--no-progress` is
force-enabled whenever stderr is not a terminal, and comfy-env pipes stderr so
it can tee to `install.log`, so what arrives is a few warnings and the single
line `The default environment has been installed.` Raising verbosity does not
help: `-v` adds phase timings and `-vv` adds internal DEBUG, neither naming a
package, and there is no `--json`.

Giving pixi a pty would restore its native bar, but only on Unix — Windows
needs ConPTY, which means a C dependency in the *host* env, and the host-env
principle forbids that. It would also fill `install.log` with ANSI redraw
noise.

So `install/progress.py` counts the result instead of parsing the narration.
`pixi.lock` declares what the env will contain and the env fills in as it
installs: one `conda-meta/<pkg>.json` per conda package. A background thread
polls that directory and repaints:

```
  [3/26] geometrypack-nodes  ████████░░░░░░░░░░░░░░░░ 141/226  38.4s
```

Two details in there are load bearing and both are commented at the top of the
module. The lock regex requires leading whitespace, because `pixi.lock` lists
every package twice — once in the top-level `packages:` catalogue at indent 0
and once indented under `environments`; measured on a real lock, 329 indented
entries against 658 total, so dropping the indent requirement silently doubles
the denominator and the bar reports half progress forever without ever erroring.
And it counts conda packages only: pairing pypi entries with
`site-packages/*.dist-info` double counts.

The bar repaints on a clock rather than on a change — a stalled install still
shows a moving elapsed time, which is the difference between "slow" and "hung".
When stdout is not a terminal the carriage-return redraw is useless, so it
falls back to discrete lines instead:

```
  [3/26] geometrypack-nodes: 141/226 package(s), 38.4s elapsed
```

## Debug categories

Verbose tracing is off by default and granular. Categories come from
environment variables, or from a persistent `~/.comfy-env/debug.env` of
`KEY=1` lines that `debug.py` loads with `setdefault`, so an explicit env var
always wins.

| Variable | Turns on |
|---|---|
| `COMFY_ENV_DEBUG` | all of the below (master switch) |
| `COMFY_ENV_DEBUG_INPUTS_OUTPUTS` | node inputs/outputs — shapes, types, devices |
| `COMFY_ENV_DEBUG_VRAM` | GPU VRAM state before and after node execution |
| `COMFY_ENV_DEBUG_SERIALIZE` | tensor serialization / deserialization |
| `COMFY_ENV_DEBUG_IPC` | CUDA IPC, legacy and pool |
| `COMFY_ENV_DEBUG_WORKER` | worker lifecycle: start, stop, crash |
| `COMFY_ENV_DEBUG_WATCHDOG` | worker watchdog — thread dumps every 60 s |
| `COMFY_ENV_DEBUG_MODELS` | model registration and VRAM |
| `COMFY_ENV_DEBUG_META` | node metadata scanning |
| `COMFY_ENV_DEBUG_INSTALL` | environment install and build |

Workers cannot import `debug.py` — it lives in the host env, which by the
host-env principle is never installed into a worker (ADR-0006). They parse the
same variables directly, and the variables reach them because `os.environ`
propagates to subprocesses automatically.

## Known weaknesses

Stated plainly, because none of these are hypothetical.

| # | Weakness | Consequence |
|---|---|---|
| 1 | ~~`logging.info()` and `logging.debug()` from node code are dropped~~ **Fixed 2026-09-11.** The worker added a handler to `logging.root` but never set the root *logger's* level, so it sat at `WARNING` and filtered INFO before any handler saw it. The parent now ships its resolved root level as `COMFY_ENV_HOST_LOG_LEVEL` and the worker applies it — parity with the host, not "forward everything", so DEBUG stays out when the host keeps it out | was: every INFO line vanished on isolation while `warning` and above kept working — which is exactly why it looked fine |
| 2 | `sys.stdout.write` in node code goes to `DEVNULL` | output disappears entirely, with no error and no terminal copy |
| 3 | `comfy_worker_debug.log` is always on and never rotated | it grows for the life of the machine — measured at 805 KB on a developer machine from ordinary use, with nothing to cap it |
| 4 | All workers share one debug log file | the pid prefix disambiguates lines, but nothing separates sessions, and a concurrent read sees interleaving |
| 5 | The forwarder drops `end=` | `print(x, end="")` becomes a line; a pack's hand-rolled progress output becomes spam |
| 6 | Ordinary C-level stderr is terminal-only by design | a library's warning, or a native progress bar, is invisible to anyone running ComfyUI as a service. Crashes are exempt — those go through faulthandler |

Number 1 is fixed. Numbers 2 and 5
are fixable in the worker's forwarder and nowhere else. Numbers 3 and 4 want
the same fix — one file per worker generation, truncated at spawn — which would
also make the crash readback in `_worker_exit_diagnostic` unambiguous about
whose tail it is printing. Number 6 is the price of not deadlocking, and
closing it would need the same pty machinery the progress bar rejected for the
same reasons.

## See also

- [ComfyUI logging background](comfyui-logging.md) — the machinery upstream
- [The process boundary](process-boundary.md) — what else does and does not cross
- [Worker lifecycle](worker-lifecycle.md) — when workers start, restart and die
- [ADR-0006: comfy-env is never installed into worker envs](adr/0006-worker-crosses-the-boundary-as-source-text.md)
