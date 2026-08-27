# ComfyUI background, for newcomers

*How vanilla ComfyUI installs, loads and uses a node pack, which is the contract comfy-env has to
honour.*

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

## Anatomy of a node pack

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
    +-- subgraphs/              <- .json reusable node groups
    +-- locales/<lang>/         <- UI translations merged into /i18n
    |                              (all three scanned off disk -- see below)
    +-- fonts/, docs/, kjweb_async/                       <- misc
```

## What ComfyUI reads from installed custom nodes

### The four `__init__.py` attributes

At startup ComfyUI `import`s each pack's `__init__.py`:

```python
# __init__.py (KJNodes, condensed)
from .nodes.nodes import INTConstant, Sleep, WidgetToString, ...
from .nodes.image_nodes import ColorMatch, ImageResizeKJ, ...

NODE_CLASS_MAPPINGS = {"INTConstant": INTConstant, ...}   # id -> class
NODE_DISPLAY_NAME_MAPPINGS = {"INTConstant": "INT Constant", ...}
WEB_DIRECTORY = "./web"                                   # optional JS for the UI
__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
```
ComfyUI reads up to **four** named attributes from __init__.py and
nothing else:

| Attribute | Required | What it is |
|---|---|---|
| `NODE_CLASS_MAPPINGS` | one of these two | `id -> class` |
| `comfy_entrypoint` | one of these two | V3 alternative, taken only if the dict is absent |
| `NODE_DISPLAY_NAME_MAPPINGS` | no | `id -> pretty name` |
| `WEB_DIRECTORY` | no | frontend JS directory |

### Everything else

The ComfyUI loader takes several more things from the custom node pack as a "side effect" of the import:

- **Whatever the import *did***: importing `__init__.py` fires every
   side effect it contains. The most common one: **API route
   registration**, where the pack hangs its own HTTP endpoints off
   ComfyUI's shared server:

    ```python
    from server import PromptServer

    @PromptServer.instance.routes.post("/geompack/upload")
    async def upload_mesh(request):
        ...   # now GET/POST http://127.0.0.1:8188/geompack/upload hits this
    ```

    plus any monkeypatching or global setup the pack does at import
    time:
    [import-time "side" effects in the wild](import-side-effects.md).

- **Workflow templates and subgraphs**, found by folder name.

- Optional **node-name translations in different languages (Spanish, Japanese...)**, one folder per
       locale.

- **`pyproject.toml`**: the
       project name.

## Lifecycle hooks and who runs them

Every file besides `__init__.py` in a node pack is optional, and different actors run them
at different times:

| File | Run by | When | Logic |
|------|--------|------|-------|
| `requirements.txt` | **ComfyUI-Manager** (not core) | install / update | pip-installed line by line |
| `install.py` | **ComfyUI-Manager** (not core) | install / update, **after** requirements | run with `sys.executable` |
| `prestartup_script.py` | **ComfyUI core** | every launch, before the server boots | imported and executed (`main.py:execute_prestartup_script`) |
| `__init__.py` | **ComfyUI core** | every launch | imported; `NODE_CLASS_MAPPINGS` read |

ComfyUI core never runs `pip install -r requirements.txt` nor
`python install.py`, and if the user installs a custom node pack by plain `git clone` into custom_nodes/, they are
expected to run them manually.

## Frontend JavaScript

Registration is Python-side; execution is browser-side. Both halves matter.

**Registration** happens in `load_custom_node` (`nodes.py`), and there are
**two independent and reciprocally exclusive paths** to register JS code, which write to the same dict under *different keys*:

| Declared as | Key used | Line |
|---|---|---|
| `[tool.comfy] web` in `pyproject.toml` | the **Registry project name** (`project.name`) | `nodes.py:2280` |
| `WEB_DIRECTORY` in `__init__.py` | the **module/directory name** | `nodes.py:2289` |

Both are guarded by `os.path.isdir()`, and they are **separate `if` blocks**,
not a fallback chain.

**Serving the JS code** is a static route per registered directory,
`/extensions/<node_pack_name>` → the node pack's web folder.

**Auto-import** is driven by `GET /extensions` (`server.py:357-368`), which
returns a flat JSON list of URLs the browser then imports. For each registered
directory:

```python
files = glob.glob(os.path.join(glob.escape(dir), '**/*.js'), recursive=True)
```

Two properties of that one line govern everything:

- **`recursive=True`**: the scan reaches *every* depth of the web folder. There is no way to
  keep a `.js` file out of the shared realm by burying it in a subfolder;
  `web/js/vendor/three/build/three.js` is imported exactly like a top-level
  widget.
- **`.js` only**: `.mjs`, `.json`, `.css`, `.html` are still **served**
  statically, they are simply never *listed* for auto-import.

That second property is the only lever a pack has. A viewer bundle renamed
`viewer-bundle.js` → `viewer-bundle.mjs` disappears from the auto-import list
while remaining fetchable, so an `<iframe>` or an explicit
`import "./viewer-bundle.mjs"` still loads it, inside the iframe's realm
rather than ComfyUI's. Everything left as `.js` under the web dir shares one
global scope with every other installed pack and might come into conflict with other node pack's javascript code, as there can only be one `window`, `document` or `viewer` among all the auto imported .js files.

## Data types

A socket type in ComfyUI is **a string**, and nothing more. `RETURN_TYPES =
("INT",)` and `INPUT_TYPES` returning `{"required": {"mesh": ("TRIMESH",)}}`
declare the same kind of thing.
ComfyUI never inspects the Python object flowing along an edge: it compares the two declared strings and, if they
match, passes the object through untouched.

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
strings a pack made up, and ComfyUI wires them to each other as happily as `IMAGE`. Two
packs that independently pick `MESH` are, as far as ComfyUI is concerned, the
same type, as the string is the whole contract!

!!! note "Why this matters for comfy-env"
    In vanilla ComfyUI the object never leaves the process, so "the type is
    just a string" costs nothing: the same `Trimesh` instance is handed from
    one node to the next.

    Under comfy-env the object may have to cross a **process boundary**, and
    a string does not say how to move bytes. That is the gap
    [custom wire types](serializers.md) fills.