# Import-time side effects in the wild

ComfyUI does not sandbox a pack's `__init__.py`. It calls
`exec_module()` on it ([ComfyUI custom nodepack background](comfyui-background.md)), so every
statement at module level runs, in ComfyUI's process, with ComfyUI's
permissions, before the server starts. Whatever a pack does there, it does to
everybody.

This page is what that actually looks like. Every number and every snippet
below was measured over **491 `__init__.py` files** from the top-500 pack
corpus, counting only statements that execute on import -- module body,
module-level `if`/`try`, and decorators (a `@routes.post(...)` on a
module-level function *does* run at import).

None of this is exotic code by bad authors. Most of it is a reasonable person
solving a real problem with the only tool the platform offers.

## The distribution

| Side effect at import | Packs | % |
|---|--:|--:|
| Prints to stdout/stderr | 83 | 17% |
| Mutates `sys.path` | 44 | 9% |
| Registers HTTP routes | 24 | 5% |
| Filesystem writes (`makedirs` / `copy` / `rmtree`) | 16 | 3% |
| Reconfigures logging | 12 | 2% |
| Writes environment variables | 5 | 1% |
| Spawns a thread or subprocess | 5 | 1% |
| Reaches into core's `nodes.NODE_CLASS_MAPPINGS` | 2 | <1% |
| Runs `pip install` | 2 | <1% |
| Replaces a core symbol | 1 | <1% |
| `warnings` filter / `atexit` / torch global | 1 each | <1% |

One piece of good news first: **only 2 packs in 491 bypass the documented
contract** by writing into core's registry instead of exporting
`NODE_CLASS_MAPPINGS`. The contract really is how nodes arrive.

## Writing into ComfyUI's own directory, then deleting from it

`comfyui_ryanonyheinside/__init__.py:64-76`, at import:

```python
extension_path = os.path.join(os.path.dirname(folder_paths.__file__), "web", "extensions")
roti_extension_path = os.path.join(extension_path, "RyanOnTheInside")
os.makedirs(roti_extension_path, exist_ok=True)

# Clean up existing files in the RyanOnTheInside folder
for file in os.listdir(roti_extension_path):
    os.remove(os.path.join(roti_extension_path, file))
```

`os.path.dirname(folder_paths.__file__)` is the **ComfyUI install root**. The
pack creates a directory inside core's tree, unconditionally deletes every
file in it, and copies its own JS in -- on every launch.

Why it exists: this predates `WEB_DIRECTORY`, when shipping frontend JS meant
putting files where ComfyUI would serve them. The mechanism it replaces
([WEB_DIRECTORY](comfyui-background.md#frontend-javascript))
does the same job by *registering* a directory rather than copying into
someone else's.

Why it hurts: the delete loop assumes it owns a path it does not own, and it
runs before anything can object. Nothing in ComfyUI tells you a pack modified
your install.

## Updating its own source code at startup

`comfyui_tinyterranodes/__init__.py:113-121`:

```python
if config_value_validator("ttNodes", "auto_update", 'false') == 'true':
    try:
        with subprocess.Popen(["git", "pull"], cwd=cwd_path, stdout=subprocess.PIPE) as p:
            p.wait()
            ...
    except:
        pass
```

A `git pull` during ComfyUI startup, from the pack's own directory. It is
off by default and opt-in through config, which is the responsible version of
this idea -- but when it is on, **the code that runs is not the code you
checked out**, and the bare `except: pass` means a failed pull is
indistinguishable from a successful one.

The cost is reproducibility. A workflow that worked yesterday can break on a
restart with no local change, and the pack is the only thing that knows why.

## Replacing a core method

`comfyui-mixlab-nodes/__init__.py:574`:

```python
PromptServer.start = new_start
```

One line, and every pack in the process now runs a different server. This is
the pattern ComfyUI's own frontend-boundary work is meant to retire: packs
monkeypatch shared surfaces because there is no sanctioned extension point
(see [Frontend JavaScript isolation](../roadmap.md#frontend-javascript-isolation)
for the same argument on the browser side).

Monkeypatches only compose if every patcher chains correctly. Two packs
patching `PromptServer.start` is a load-order lottery.

## Installing packages at import

`comfyui-model-manager` and `comfyui-workflow-encrypt` both `pip install`
missing dependencies from `__init__.py`, into the shared host environment,
while ComfyUI is starting.

This is the failure mode comfy-env exists to replace, and the reason is worth
stating plainly: **the install is global.** A pack resolving its own
dependency at import can upgrade a package another pack pinned, and the
symptom appears in the *other* pack, later, as an import error nobody can
trace. `requirements.txt` and `install.py` exist so this happens at install
time, once, where it can fail visibly -- see
[`install()`](install.md).

## Starting threads before the server exists

```python
# comfyui-impact-pack/__init__.py:61
threading.Thread(target=impact.wildcards.wildcard_load).start()

# comfyui-promptchain/__init__.py:230
_threading.Thread(target=_preload, daemon=True).start()
```

Both are warming a cache off the critical path, which is a legitimate goal.
The hazard is that import time is the one moment ComfyUI has no supervision:
a thread started here outlives the import, has no owner, and is not visible to
any shutdown path. `impact-pack`'s is not even a daemon thread, so it can hold
the process open.

## The systemic one: `sys.path`

Not a curiosity -- **44 packs, 9% of the corpus.**

```python
sys.path.append(os.path.join(os.path.dirname(__file__), "some_vendored_lib"))
```

Every one is reasonable in isolation: the pack vendors a library, or needs a
sibling directory importable. But `sys.path` is **interpreter-global**. A pack
that prepends a directory changes module resolution for every pack loaded
after it, and for ComfyUI itself. Load order is `os.listdir` order.

This is the concrete, countable version of the argument in
[ADR-0001](adr/0001-process-isolation-via-persistent-subprocess-workers.md).
The problem is not that any one pack is careless; it is that 44 careful packs
sharing one interpreter is still 44 mutations of one global, resolved by
alphabet.

## The harmless one, for calibration

```python
# ComfyUI-JoyCaption/__init__.py:17, and ComfyUI-MiniCPM/__init__.py:14
if sys.platform == 'win32':
    os.system('color')
```

Spawning a shell at import to turn on ANSI colour support. It costs a process
spawn and nothing else. Included because most of what runs at import time is
this: small, well-meant, invisible -- and the point of this page is the
aggregate, not any one line.

## What comfy-env does about it

Isolation does not make these patterns safe; it makes them **local**. A pack
whose nodes run in their own process gets its own `sys.path`, its own
site-packages, and its own interpreter to reconfigure. `pip install` at import
reaches only that env.

Three things it deliberately does **not** contain, because they happen in the
parent before any worker exists:

- `__init__.py` still executes in ComfyUI's process -- comfy-env's
  [`register_nodes()`](register-nodes.md) is called *from* it. Only the node
  code moves.
- Route registration must stay in the parent, since that is where the server
  is (`_register_proxy_routes` forwards to the worker).
- Frontend JS shares one browser origin regardless
  ([ADR-0031](adr/0031-frontend-javascript-isolation.md)).

The house rule that follows: **keep `__init__.py` boring.** Import, register,
return. Everything else has a lifecycle hook that runs at a moment where
failure is visible -- `install.py` for dependencies,
[`setup_env()`](setup-env.md) for process-wide hygiene.
