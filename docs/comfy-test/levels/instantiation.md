# `instantiation`

> Calls every node's constructor. Registration proves the module imported;
> this proves the classes can actually be built.

| | |
|---|---|
| **Needs** | `env` (provided by [`install`](install.md)) -- **not** a server |
| **Default** | yes |
| **Fails the run** | yes |
| **Source** | `orchestration/levels/instantiation.py` |

!!! note "It does not need a running server"
    `LEVEL_REQUIRES` gives this level `["env"]` only. It spawns its own
    subprocess from the installed environment, so it can run without
    [`registration`](registration.md) -- cheaper than the ladder position
    suggests.

## How it works

The level generates a script and runs it in a **subprocess** against the
installed environment. The script imports `NODE_CLASS_MAPPINGS` and calls each
constructor in turn, reporting results as JSON. A subprocess is used because a
node constructor can crash the interpreter outright -- an access violation
should fail one level, not take the run down.

## CPU-lane hardening

On a non-CUDA runner the script disables CUDA before anything imports ComfyUI,
because `model_management.py` calls `torch.cuda` at import time:

```python
os.environ["CUDA_VISIBLE_DEVICES"] = ""
torch.cuda.is_available = lambda: False
torch.cuda.device_count = lambda: 0
torch.cuda.current_device = lambda: 0
```

`CUDA_VISIBLE_DEVICES=""` alone is not enough on Windows, where torch's C++
CUDA calls can still fault with an access violation when no driver is present
-- hence the monkeypatch. Declared CUDA packages are mocked by the same
probe-based rule [`install`](install.md) uses.

## What it catches

Constructor work that assumes something the machine does not have:

- a GPU (`self.device = torch.device("cuda")` in `__init__`)
- a model file on disk at import or construction time
- network access to fetch weights or config
- a writable path that does not exist on a fresh install

These are invisible to `registration`, which only proves the *module*
imported. A class can register fine and fail the moment ComfyUI builds it.

## What it does not catch

Only `__init__` runs. Nothing calls the node's `FUNCTION`, so anything that
happens during execution -- shape errors, dtype mismatches, actual inference --
belongs to [`execution`](execution.md). It also constructs with no arguments,
so it cannot catch failures that depend on particular input values.

## Failure output

Failures are collected rather than raised on the first one: the level reports
how many nodes failed and lists them, so one broken constructor does not hide
the other four.

## Config

No keys of its own. In the default set; needs `env`, so listing it pulls in
`install`.

## See also

- [The ladder](../levels.md) -- all 13 levels and the resource model
- [`registration`](registration.md) -- the cheaper check that the module imports
- [ADR-0004](../adr/0004-mocking-is-earned-by-probing.md) -- how mocking is
  decided
