# ComfyUI background, for newcomers

*How vanilla ComfyUI loads a node pack, which is the contract comfy-env has to
honour.
If you already know what `NODE_CLASS_MAPPINGS` is and when
`prestartup_script.py` runs, skip to the
[architecture overview](index.md).*

Vanilla ComfyUI loads every custom node pack into
one shared Python process with one shared environment.

A node pack is a directory under `custom_nodes/` whose `__init__.py` exports
`NODE_CLASS_MAPPINGS`.

At install time, the standard installation flow (ComfyUI-Manager, nowadays
bundled with Desktop ComfyUI) is:

- `pip install -r requirements.txt`, if the `requirements.txt` file is present
- `python install.py`, if `install.py` is present

When we start ComfyUI there is also a per-pack pre-startup hook: ComfyUI itself executes each
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
    +-- example_workflows/      <- .json workflows the UI offers as templates
    |                              (scanned by the server -- see below)
    +-- fonts/, docs/, kjweb_async/                       <- misc
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
core `nodes.py` `load_custom_node`). It reads **four** named attributes and
nothing else:

| Attribute | Required | What it is |
|---|---|---|
| `NODE_CLASS_MAPPINGS` | one of these two | `id -> class` (`nodes.py:2293`) |
| `comfy_entrypoint` | one of these two | V3 alternative, taken only if the dict is absent (`:2302`) |
| `NODE_DISPLAY_NAME_MAPPINGS` | no | `id -> pretty name` (`:2298`) |
| `WEB_DIRECTORY` | no | frontend JS directory (`:2286`) |

Everything else the loader does is a side effect of the import, not a lookup.
Nothing named `__all__` is consulted -- listing an attribute there changes
nothing; leaving it out hides nothing.

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
   The exact scan rules are below -- they decide what does and does not
   end up in that shared realm.
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

Two things go the other way -- ComfyUI writes to the pack rather than reading
from it:

- **`RELATIVE_PYTHON_MODULE` is set on every registered class**
  (`nodes.py:2296`, and again on the V3 path at `:2324`). Core mutates your
  classes on the way in; the frontend uses it to attribute a node to its pack.
  Setting it yourself is pointless -- it is overwritten.
- **The pack's directory is recorded** in `LOADED_MODULE_DIRS` (`:2265`),
  keyed by module name. That table is `loadedModules`, and it is why a pack
  that fails to import can still be *listed* under workflow templates yet 404
  when opened (see below).

There is also an `ignore` set parameter (`:2294`): a caller can name node ids
to skip. Core passes it when loading its own nodes, not for custom packs.

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

### Data types: what a socket type actually is

A socket type in ComfyUI is **a string**, and nothing more. `RETURN_TYPES =
("INT",)` and `INPUT_TYPES` returning `{"required": {"mesh": ("TRIMESH",)}}`
declare the same kind of thing. ComfyUI never inspects the Python object
flowing along an edge — it compares the two declared strings and, if they
match, passes the object through untouched.

The matching rule is `validate_node_input` in
`comfy_execution/validation.py`, and it is short:

| Rule | Behaviour |
|---|---|
| Exact string equality | matches |
| `"*"` on either side (`IO.AnyType`) | matches anything |
| `COMFY_MATCHTYPE_V3` on either side | matches; the frontend validates it |
| `"A,B"` | a **union** — comma-separated. Non-strict (the default) needs a non-empty intersection; strict needs a subset |

There are ~85 built-in types (`comfy_api/latest/_io.py`), and the Python
objects behind them are ordinary:

| Socket | Python object |
|---|---|
| `IMAGE`, `MASK` | `torch.Tensor` |
| `LATENT` | `dict` with a `samples` tensor, plus optional `noise_mask`, `batch_index` |
| `CONDITIONING` | list of `[tensor, dict]` pairs |
| `MODEL`, `CLIP`, `VAE` | ComfyUI wrapper objects (`ModelPatcher` and friends) |
| `INT`, `FLOAT`, `STRING`, `BOOLEAN` | Python primitives |

**The type registry is open.** Nothing registers a type name centrally, so a
pack invents one by returning it: `TRIMESH`, `POINTCLOUD`, `SKELETON` are
strings a pack made up, and ComfyUI wires them as happily as `IMAGE`. Two
packs that independently pick `MESH` are, as far as ComfyUI is concerned, the
same type — the string is the whole contract.

!!! note "Why this matters for comfy-env"
    In vanilla ComfyUI the object never leaves the process, so "the type is
    just a string" costs nothing — the same `Trimesh` instance is handed from
    one node to the next.

    Under comfy-env the object may have to cross a **process boundary**, and
    a string does not say how to move bytes. That is the gap
    [custom wire types](serializers.md) fills.

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

### Frontend JavaScript: what gets auto-imported

Registration is Python-side; execution is browser-side. Both halves matter.

**Registration** happens in `load_custom_node` (`nodes.py`), and there are
**two independent paths**, which write to the same dict under *different keys*:

| Declared as | Key used | Line |
|---|---|---|
| `[tool.comfy] web` in `pyproject.toml` | the **Registry project name** (`project.name`) | `nodes.py:2280` |
| `WEB_DIRECTORY` in `__init__.py` | the **module/directory name** | `nodes.py:2289` |

Both are guarded by `os.path.isdir()`, and they are **separate `if` blocks**,
not a fallback chain.

!!! danger "Declaring both registers the directory twice"
    `project.name` and the directory name are rarely identical
    (`comfyui-geometrypack` vs `ComfyUI-GeometryPack`). If a pack declares
    `[tool.comfy] web` *and* `WEB_DIRECTORY`, and both paths resolve to real
    directories, `EXTENSION_WEB_DIRS` gets **two entries pointing at the same
    folder**. Every file is then listed twice under two URLs, the browser
    imports each one twice, and `app.registerExtension` is called twice with
    the same name -- the collision case. Pick one declaration.

**Serving** is a static route per registered directory,
`/extensions/<name>` → the folder (`server.py:1243-1244`).

**Auto-import** is driven by `GET /extensions` (`server.py:357-368`), which
returns a flat JSON list of URLs the browser then imports. For each registered
directory:

```python
files = glob.glob(os.path.join(glob.escape(dir), '**/*.js'), recursive=True)
```

Two properties of that one line govern everything:

- **`recursive=True`** -- the scan reaches *every* depth. There is no way to
  keep a `.js` file out of the shared realm by burying it in a subfolder;
  `web/js/vendor/three/build/three.js` is imported exactly like a top-level
  widget.
- **`.js` only** -- `.mjs`, `.json`, `.css`, `.html` are still **served**
  statically, they are simply never *listed* for auto-import.

That second property is the only lever a pack has. A viewer bundle renamed
`viewer-bundle.js` → `viewer-bundle.mjs` disappears from the auto-import list
while remaining fetchable, so an `<iframe>` or an explicit
`import "./viewer-bundle.mjs"` still loads it -- inside the iframe's realm
rather than ComfyUI's. Everything left as `.js` under the web dir shares one
global scope with every other installed pack: one `window`, one `document`,
one extension-name namespace.

### Workflow templates

A pack can ship example workflows that appear in the UI's template browser.
This is **not** part of the `__init__.py` import -- like
`prestartup_script.py`, it is a separate thing the server does, which is why
no attribute controls it. It is a directory scan, and the rules are in
`app/custom_node_manager.py`.

**Five folder names are accepted** (`:94`), first match wins per pack:

```python
example_workflow_folder_names = ["example_workflows", "example", "examples",
                                 "workflow", "workflows"]
```

`example_workflows` is the preferred spelling; the other four still work and
merely log a nudge -- at `logging.debug`, so nobody sees it at ComfyUI's
default level. A pack using `workflows/` is fine and does not need renaming.

!!! warning "The scan is exactly one level deep"
    The glob is `os.path.join(folder, f"*/{folder_name}/*.json")` (`:104`) --
    `custom_nodes/<pack>/<folder>/*.json` and **no deeper**. A workflow at
    `workflows/basic/foo.json` is never found, and nothing warns: it simply
    does not appear. Organising templates into subfolders is the one mistake
    this feature invites.

The **filename is the display name** --
`os.path.splitext(os.path.basename(file))[0]` (`:114`). There is no title
field and no ordering control, so naming is the only lever you have.

Two separate mechanisms back the feature, and they disagree about which packs
count:

| | endpoint | source of truth |
|---|---|---|
| **Listing** | `GET /workflow_templates` (`:96-119`) | globs the **filesystem**, every `custom_nodes` path |
| **Serving** | static `/api/workflow_templates/<module_name>` (`:121-137`) | iterates `loadedModules` -- only packs that **imported successfully** |

So a pack that fails to load can still be *listed* while its templates 404 on
open. That is worth knowing for an isolated pack specifically: a missing env
sends it down the in-process import fallback, and if that fails hard the pack
is absent from `loadedModules` -- its workflows stay advertised and become
unfetchable.
