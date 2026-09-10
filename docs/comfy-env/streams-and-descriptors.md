# How programs write output

*`print()`, the `logging` module, `sys.stdout`, file descriptors and `2>&1` —
the layer everything else in this section sits on. Skip it if you already know
why `2>&1` needs the `&`.*
{: .subtitle }

Every claim on this page was measured on this machine; the commands are
included so you can re-run them.

## Three layers, and only two are interceptable

"Writing output" is three different mechanisms wearing the same coat. What
separates them is **where** the write happens, and therefore who can get in
front of it.

| # | You call | It reaches the screen via | Interceptable from Python |
|---|---|---|---|
| 1 | `print(x)` | `sys.stdout.write(...)` | **yes** — rebind `sys.stdout` |
| 2 | `logging.info(x)` | a handler, which calls `stream.write(...)` | **yes** — same objects, or add a handler |
| 3 | `os.write(1, b"x")`, a C library, a subprocess | **file descriptor 1**, directly | **no** |

Layers 1 and 2 go through a Python object you can replace. Layer 3 goes
through a kernel resource you cannot, at least not without OS-level tricks.
This distinction is the reason the rest of this section exists.

## `print()` is not one write

`print` writes each argument, each separator and the terminator *separately*:

```
print('x')             ->  2 write() calls  ['x', '\n']
print('a','b')         ->  4 write() calls  ['a', ' ', 'b', '\n']
print('a','b','c')     ->  6 write() calls  ['a', ' ', 'b', ' ', 'c', '\n']
print('x', end='')     ->  2 write() calls  ['x', '']
logging.info('x')      ->  1 write() calls  ['INFO:root:x\n']
```

*(Measured by swapping `sys.stdout` for an object that records every `write`.)*

Two consequences worth carrying forward. Anything counting writes rather than
lines counts `print` at roughly `2n` — which is exactly what ComfyUI's log ring
does. And `end=''` does not suppress the second write, it writes the empty
string, so even a "silent" terminator costs a slot.

`logging` is the opposite: one formatted string, one `write`, because
`StreamHandler.emit` does `stream.write(msg + self.terminator)`.

## `sys.stdout` is a name, not a pipe

This is the single most load-bearing idea here.

```python
sys.stdout = MyWrapper(sys.stdout)
```

That rebinds a name in **one interpreter's memory**. Every `print()` executed
afterwards in that process goes through `MyWrapper`. Nothing else changes:
the terminal, the shell, other processes, and the kernel are all unaware.

In particular it does **not** change file descriptor 1. So:

- Python code in that process → intercepted.
- A C extension writing to fd 1 → not intercepted.
- A subprocess → not intercepted, because a child inherits *descriptors*, and
  never its parent's Python objects.

## File descriptors

A file descriptor — `fd` — is just a small integer the kernel uses as an index
into a per-process table of open things. Three are conventional:

| fd | Name | Convention |
|---|---|---|
| 0 | stdin | input |
| 1 | stdout | normal output |
| 2 | stderr | errors, diagnostics, progress — anything that is *not* the program's data |

The convention is the whole point of the split. `ls | grep x` works because
`ls` writes filenames to fd 1 and complaints to fd 2, so the complaints do not
end up in `grep`'s input. A program that logs to fd 1 pollutes everyone
downstream.

What fd 1 *points at* is set by whoever launched the process, and the program
cannot tell without asking (`isatty()`):

```
python app.py                  fd 1 -> the terminal
python app.py > out.txt        fd 1 -> out.txt
python app.py | grep error     fd 1 -> a pipe into grep
```

Descriptors survive `exec` and are inherited by children unless explicitly
closed or redirected. That inheritance is what makes a subprocess's output
appear in your terminal at all — and, because it is *only* descriptors that
are inherited, it is also why that is the only place it appears.

## `2>&1`, and why the `&`

`2>&1` means **"make fd 2 point at whatever fd 1 currently points at."**

The `&` is what says *"1 is a descriptor number, not a filename"*. Without it,
`2>1` redirects stderr into a **file called `1`**:

```
$ ./two.sh 2>1 >/dev/null
$ cat ./1
ERR
```

And "currently" is load bearing, because the shell applies redirections
**left to right**:

```bash
cmd >/dev/null 2>&1     # 1 -> /dev/null, THEN 2 copies 1   -> both discarded
cmd 2>&1 >/dev/null     # 2 copies 1 (still the TERMINAL), THEN 1 moves
                        # -> stdout discarded, stderr still on your screen
```

Demonstrated with a script that prints `OUT` to stdout and `ERR` to stderr:

```
A: ./two.sh >/dev/null 2>&1  -> []
B: ./two.sh 2>&1 >/dev/null  -> [ERR]     <- the classic bug
C: ./two.sh 2>&1 | wc -l     -> 2         <- both streams reach the pipe
D: ./two.sh 2>/dev/null | wc -l -> 1      <- only stdout does
```

Case B is the one that bites: it *looks* like "silence everything" and is
actually "silence the data, keep the noise".

## Buffering, and why piped output looks frozen

Python picks a buffering strategy per stream based on what the descriptor is
pointing at, and it decides **once, at startup**:

```
--- to a pipe ---
stdout isatty=False line_buffering=False   <- block buffered, ~8 KB
stderr isatty=False line_buffering=True
--- to a pty ---
stdout isatty=True  line_buffering=True
stderr isatty=True  line_buffering=True
```

So the same program behaves differently depending on who launched it. On a
terminal, stdout flushes every newline and you see progress. Through a pipe,
stdout accumulates until the buffer fills, so a long-running job can appear to
produce nothing for minutes and then emit everything at once — output that is
already *written* but not yet *flushed*.

**stderr is line-buffered either way.** The old lore that "stderr is
unbuffered" is out of date, but the practical conclusion survives: diagnostics
on stderr show up promptly even when piped, which is a large part of why
programs put progress there.

Three ways out, in order of bluntness: pass `flush=True` on the individual
calls that matter; set `PYTHONUNBUFFERED=1` for the whole process; or give the
child a pseudo-terminal so `isatty()` returns true and it configures itself the
way it would on a console.

## Why this decides everything downstream

Put the three facts together:

1. Interception in Python happens at the **object** layer (`sys.stdout`).
2. A subprocess inherits the **descriptor** layer, and nothing else.
3. Therefore a subprocess's output is, from the parent's Python code,
   completely invisible — unless the parent captures the descriptor and
   re-emits what it reads.

ComfyUI intercepts at layer 1 and gets four destinations out of it. comfy-env
runs everything in subprocesses, which sit at layer 3, and so has to do the
capture-and-re-emit work itself.

## Where to go next

- [ComfyUI logging background](comfyui-logging.md) — what ComfyUI builds on
  top of layer 1, and the four places a line ends up
- [comfy-env's logging](logging-approach.md) — how output crosses the process
  boundary, and what still does not
