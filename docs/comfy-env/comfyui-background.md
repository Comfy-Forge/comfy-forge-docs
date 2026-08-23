# ComfyUI background, for newcomers

*How vanilla ComfyUI loads a node pack -- the contract comfy-env has to
honour. If you already know what `NODE_CLASS_MAPPINGS` is and when
`prestartup_script.py` runs, skip to the
[architecture overview](index.md).*

Vanilla ComfyUI loads every custom node pack into
one shared Python process with one shared environment. A node pack is a
directory under `custom_nodes/` whose `__init__.py` exports
`NODE_CLASS_MAPPINGS`.

At install time, the standard installation flow (ComfyUI-Manager, nowadays
bundled with ComfyUI -- at least the Desktop version):

- first pip-installs the pack's `requirements.txt`, if present
- then runs its `install.py`, if present

At startup time there is also a per-pack hook: ComfyUI itself executes each
pack's `prestartup_script.py`, if present, before the server boots.

### Anatomy of a node pack

Using [ComfyUI-KJNodes](https://github.com/kijai/ComfyUI-KJNodes) (a popular
real-world pack) as the example:

```
ComfyUI/custom_nodes/
`-- ComfyUI-KJNodes/
    +-- __init__.py             <- THE contract: exports NODE_CLASS_MAPPINGS,
    |                              NODE_DISPLAY_NAME_MAPPINGS, WEB_DIRECTORY
    +-- requirements.txt        <- PyPI deps, pip-installed into the ONE shared env
    +-- pyproject.toml          <- Comfy Registry metadata (name, version, publisher, ...)
    +-- nodes/                  <- the node classes, grouped by topic
    |   +-- nodes.py               (constants, scheduling, utils ...)
    |   +-- image_nodes.py         (ColorMatch, ImageResizeKJ, ...)
    |   +-- curve_nodes.py, mask_nodes.py, batchcrop_nodes.py, ...
    +-- web/                    <- JS extensions served to the browser UI
    |                              (pointed at by WEB_DIRECTORY = "./web")
    +-- fonts/, docs/, example_workflows/, kjweb_async/   <- assets
```

At startup ComfyUI `import`s each pack's `__init__.py` and takes several
distinct things from it:

```python
# __init__.py (KJNodes, condensed)
from .nodes.nodes import INTConstant, Sleep, WidgetToString, ...
from .nodes.image_nodes import ColorMatch, ImageResizeKJ, ...

NODE_CLASS_MAPPINGS = {"INTConstant": INTConstant, ...}   # id -> class
NODE_DISPLAY_NAME_MAPPINGS = {"INTConstant": "INT Constant", ...}
WEB_DIRECTORY = "./web"                                   # optional JS for the UI
__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
```

Exactly what ComfyUI takes from the imported module (verified against
core `nodes.py` `load_custom_node`):

1. **The nodes** -- one of two ways:
    - **V1 (the common one):** the `NODE_CLASS_MAPPINGS` dict (`id -> class`,
      **required** -- no dict, no nodes) plus the optional
      `NODE_DISPLAY_NAME_MAPPINGS` (`id -> pretty name`).
    - **V3 (newer):** a `comfy_entrypoint()` function returning a
      `ComfyExtension`, whose `get_node_list()` + each class's
      `GET_SCHEMA()` produce the same node registry. comfy-env's metadata
      scan reads both: the V1 dict, and `comfy_entrypoint()` when no dict is
      exported.

    !!! note "An empty dict is not the same as no dict"
        Core's loader takes the V1 branch whenever `NODE_CLASS_MAPPINGS` is
        *present and not `None`* -- an empty `{}` still wins, and the
        `comfy_entrypoint` branch is never reached. A pack exporting both an
        empty dict and an entrypoint therefore registers nothing upstream,
        and `load_custom_node` still returns success, so nothing warns.
2. **The frontend JS directory** -- `WEB_DIRECTORY` (or `[tool.comfy].web`
   in `pyproject.toml`). **ComfyUI does NOT import this into Python.** It
   just *registers the directory* and serves the files statically; the
   **browser** then auto-imports every `.js` under it when the UI loads.
   Python-side registration, browser-side execution -- two different
   processes. (This split is why frontend JS **cannot currently be
   isolated** the way the Python can be -- there is no per-pack browser
   boundary to isolate at, only one shared origin; deferred with the
   reasoning in [ADR-0031](adr/0031-frontend-javascript-isolation.md).)
3. **Whatever the import *did*** -- running `__init__.py` fires every
   side effect it contains. The most common one: **API route
   registration**, where the pack hangs its own HTTP endpoints off
   ComfyUI's shared server --

    ```python
    from server import PromptServer

    @PromptServer.instance.routes.post("/geompack/upload")
    async def upload_mesh(request):
        ...   # now GET/POST http://127.0.0.1:8188/geompack/upload hits this
    ```

    -- plus any monkeypatching or global setup the pack does at import
    time. ComfyUI reads no named attribute for any of this; it just runs
    the module, and the side effects happen.

Plus two things that are *not* part of the `__init__.py` import at all,
covered in the lifecycle table below: `prestartup_script.py` (run **before**
import) and the install-time `requirements.txt` + `install.py` (run by
Manager, earlier still).

Each node is a plain class with a well-known shape -- this is the whole
interface ComfyUI needs (real node, verbatim from `nodes/nodes.py`):

```python
class INTConstant:
    @classmethod
    def INPUT_TYPES(s):                      # -> input sockets/widgets in the UI
        return {"required": {
            "value": ("INT", {"default": 0, "min": -0xffffffffffffffff,
                              "max": 0xffffffffffffffff}),
        }}
    RETURN_TYPES = ("INT",)                  # -> output socket types
    RETURN_NAMES = ("value",)                # -> output socket labels
    FUNCTION = "get_value"                   # -> method ComfyUI calls to execute
    CATEGORY = "KJNodes/constants"           # -> where it sits in the node menu

    def get_value(self, value):              # the actual work
        return (value,)
```

### Lifecycle hooks and who runs them

Every file besides `__init__.py` is optional, and different actors run them
at different times:

| File | Run by | When | Logic |
|------|--------|------|-------|
| `requirements.txt` | **ComfyUI-Manager** (not core) | install / update | pip-installed line by line |
| `install.py` | **ComfyUI-Manager** (not core) | install / update, **after** requirements | run with `sys.executable` |
| `prestartup_script.py` | **ComfyUI core** | every launch, before the server boots | imported and executed (`main.py:execute_prestartup_script`) |
| `__init__.py` | **ComfyUI core** | every launch | imported; `NODE_CLASS_MAPPINGS` read |

The install-time order is defined in Manager's `execute_install_script`
(`glob/manager_core.py`):

- if `requirements.txt` exists it is pip-installed first
- *then* `install.py` is executed, if present

ComfyUI core never runs
either -- installing by plain `git clone` skips both steps, and the user is
expected to run them manually (`pip install -r requirements.txt` and/or
`python install.py`, typically spelled out in the pack's README, or simply
assumed).

Real packs cover the whole spectrum of these hooks:

- **No `requirements.txt` at all** --
  [cg-use-everywhere](https://github.com/chrisgoringe/cg-use-everywhere)
  (the most-downloaded pack on the Comfy Registry, ~1.9M downloads) and
  [ComfyUI-Custom-Scripts](https://github.com/pythongosssss/ComfyUI-Custom-Scripts)
  ship only Python-stdlib + frontend JS: nothing to install, nothing that can
  conflict in the Python environment.
- **`requirements.txt` only** -- KJNodes, above; the common case.
- **`prestartup_script.py`** --
  [ComfyUI-Manager](https://github.com/ltdrdata/ComfyUI-Manager) itself uses
  it to execute its queued ("lazy") install scripts and set up log capture
  before the server boots;
  [rgthree-comfy](https://github.com/rgthree/rgthree-comfy) ships one too.
  This hook exists precisely because it runs *before* anything imports --
  the only moment you can still fix the environment.
