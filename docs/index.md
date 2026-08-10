# comfy-env architecture

[comfy-env](https://github.com/PozzettiAndrea/comfy-env) is environment
management and automatic CUDA wheel resolution for ComfyUI custom nodes
(~12,000 lines of Python under `src/comfy_env/`).

comfy-env solves two problems: **environment isolation** (nodes with
conflicting dependencies each get their own Python environment,
transparently) and **CUDA / prebuilt wheels / conda packages** (dependencies
pip alone cannot deliver, resolved for the user's exact machine -- no
compiler, no CUDA toolkit). Both are expanded
[below](#the-two-problems-environment-isolation-and-cudaconda-packages),
after a short ComfyUI primer.

## ComfyUI background, for newcomers

ComfyUI loads every custom node pack into
one shared Python process with one shared environment. A node pack is a
directory under `custom_nodes/` whose `__init__.py` exports
`NODE_CLASS_MAPPINGS`. At install time, the standard installation flow
(ComfyUI-Manager, nowadays bundled with ComfyUI) first pip-installs the
pack's `requirements.txt` if present, then runs its `install.py` if present.
At startup time there is also a per-pack hook: ComfyUI itself executes each
pack's `prestartup_script.py` before the server boots.

### Anatomy of a node pack

Using [ComfyUI-KJNodes](https://github.com/kijai/ComfyUI-KJNodes) (a popular
real-world pack) as the example:

```
ComfyUI/custom_nodes/
`-- ComfyUI-KJNodes/
    +-- __init__.py             <- THE contract: exports NODE_CLASS_MAPPINGS,
    |                              NODE_DISPLAY_NAME_MAPPINGS, WEB_DIRECTORY
    +-- requirements.txt        <- PyPI deps, pip-installed into the ONE shared env
    +-- pyproject.toml          <- Comfy Registry metadata (name, version, publisher)
    +-- nodes/                  <- the node classes, grouped by topic
    |   +-- nodes.py               (constants, scheduling, utils ...)
    |   +-- image_nodes.py         (ColorMatch, ImageResizeKJ, ...)
    |   +-- curve_nodes.py, mask_nodes.py, batchcrop_nodes.py, ...
    +-- web/                    <- JS extensions served to the browser UI
    |                              (pointed at by WEB_DIRECTORY = "./web")
    +-- fonts/, docs/, example_workflows/, kjweb_async/   <- assets
```

At startup ComfyUI imports each pack's `__init__.py` and reads two dicts:

```python
# __init__.py (KJNodes, condensed)
from .nodes.nodes import INTConstant, Sleep, WidgetToString, ...
from .nodes.image_nodes import ColorMatch, ImageResizeKJ, ...

NODE_CLASS_MAPPINGS = {"INTConstant": INTConstant, ...}   # id -> class
NODE_DISPLAY_NAME_MAPPINGS = {"INTConstant": "INT Constant", ...}
WEB_DIRECTORY = "./web"                                   # optional JS for the UI
__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
```

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
(`glob/manager_core.py`): if `requirements.txt` exists it is pip-installed
first, *then* `install.py` is executed if present. ComfyUI core never runs
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
  conflict.
- **`requirements.txt` only** -- KJNodes, above; the common case.
- **`prestartup_script.py`** --
  [ComfyUI-Manager](https://github.com/ltdrdata/ComfyUI-Manager) itself uses
  it to execute its queued ("lazy") install scripts and set up log capture
  before the server boots;
  [rgthree-comfy](https://github.com/rgthree/rgthree-comfy) ships one too.
  This hook exists precisely because it runs *before* anything imports --
  the only moment you can still fix the environment.
- **All three** -- comfy-env packs, which occupy each hook with one line
  (the three-call contract below).

That "well-known class shape" of a node is also what makes comfy-env's
isolation possible: `register_nodes()` reads `INPUT_TYPES`/`RETURN_TYPES`
metadata out of a subprocess and synthesizes proxy classes with the same
shape, so ComfyUI cannot tell a proxied node from a local one.

## The two problems: environment isolation and CUDA/conda packages

### Environment isolation

One shared environment for every pack breaks in predictable ways:

- **Conflicting Python deps** -- node A needs torch 2.4, node B needs
  torch 2.8; whichever installs last wins, the other breaks.
- **Conflicting native libraries** -- two packages bundle their own libomp,
  CUDA runtimes, or cv2; loading both into one process corrupts state or
  segfaults.
- **Wrong interpreter entirely** -- a node needs a different Python version
  than ComfyUI runs (Blender's `bpy` wants 3.11, pymesh2 wants 3.9).

comfy-env's answer is **process isolation**: any subdirectory that declares a
`comfy-env.toml` gets its own pixi-managed environment -- separate
interpreter, conda packages, pip packages -- and its nodes execute in a
persistent subprocess worker using that interpreter. The parent synthesizes
proxy classes with the standard node shape (see the anatomy above), so to
ComfyUI -- and to the user wiring a workflow -- nothing changed.
([ADR-0001](adr/0001-process-isolation-via-persistent-subprocess-workers.md),
[ADR-0002](adr/0002-pixi-as-environment-manager.md))

### CUDA, prebuilt wheels, and conda packages

The second problem is delivering the dependencies that `pip install` alone
cannot: CUDA-compiled packages that need matching binaries, and packages that
do not exist on PyPI at all.

**CUDA packages -> prebuilt wheels.** Modern CV/ML packs depend on
CUDA-compiled packages: flash-attn, nvdiffrast, pytorch3d, gsplat, nunchaku.
Every such wheel is compiled for one exact combination of:

- Python ABI (3.10 / 3.11 / 3.12 / 3.13)
- torch version (2.4 ... 2.11)
- CUDA version (12.x / 13.0)
- OS (Windows / Linux)
- GPU architecture (SM 8.0+)

Upstream projects publish only a fraction of that matrix, and building from
source needs a CUDA toolkit plus a C++ compiler -- something end users do not
have. comfy-env's answer is a **prebuilt wheel index**:
[cuda-wheels](https://github.com/PozzettiAndrea/cuda-wheels). Packages listed
under `[cuda]` in the config are resolved at install time against the user's
detected GPU/torch/Python combination and installed as ready-made wheels --
no compiler, no CUDA toolkit, with a Releases-API fallback when the index is
unreachable.
([ADR-0004](adr/0004-prebuilt-cuda-wheel-index.md))

**Conda packages.** Some dependencies -- `ffmpeg`, `av`'s native half, CGAL,
Blender's `bpy`, mesa -- are not on PyPI in any usable form; they live on
conda-forge. This is exactly why comfy-env generates **pixi** manifests:
pixi speaks conda-forge and PyPI in the same file with one lockfile, so a
`comfy-env.toml` can declare conda packages, pip packages, and CUDA wheels
side by side and have them resolved together.
([ADR-0002](adr/0002-pixi-as-environment-manager.md),
[ADR-0003](adr/0003-two-config-files-with-two-roles.md))

## The three-call contract

A consuming node pack integrates with exactly three lines:

```python
# install.py
from comfy_env import install; install()

# prestartup_script.py
from comfy_env import setup_env; setup_env()

# __init__.py
from comfy_env import register_nodes
NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS = register_nodes()
```

These map to the three lifecycle phases below: **build time**
(`install/__init__.py:39`), **startup** (`environment/setup.py:135`), and
**runtime** (`isolation/wrap.py:701`).

## System context

```mermaid
flowchart LR
    subgraph host["ComfyUI main process (host Python env)"]
        comfyui["ComfyUI core"]
        pack["Node pack<br/>install.py / prestartup_script.py / __init__.py"]
        ce["comfy-env library"]
        comfyui --> pack --> ce
    end

    subgraph ws["Machine-wide workspace<br/>LOCALAPPDATA/Programs/comfy-env (Windows), ~/.ce (Unix)"]
        env1["envs/&lt;name&gt;/pixi.toml<br/>+ .pixi/envs/default/"]
    end

    worker["Isolated worker subprocess<br/>(interpreter from the pixi env)"]
    pixi["pixi binary (+ uv underneath)<br/>self-bootstrapped to ~/.pixi/bin"]
    idx["cuda-wheels simple index<br/>(GitHub Pages)"]
    rel["GitHub Releases API<br/>(network fallback)"]
    reg["Comfy Registry / GitHub<br/>(node dependencies)"]

    ce -->|"generates manifests, runs pixi install"| pixi
    pixi --> env1
    ce -->|"resolves prebuilt CUDA wheel URLs"| idx
    idx -.->|"unreachable"| rel
    ce -->|"clones [node_reqs] peers"| reg
    ce ==>|"socket IPC + shared memory"| worker
    env1 -.->|"provides interpreter"| worker
```

The workspace is shared machine-wide: env names (`<plugin>-<subdir>`,
`ComfyUI-` prefix stripped, lowercased) act as global identifiers, so two
ComfyUI installs that declare the same node reuse one materialized env
([ADR-0007](adr/0007-machine-wide-workspace-with-per-env-manifests.md)).

## Module layering

The import graph is layered and acyclic at package level, with three
deliberate module-level cycles broken by function-local (lazy) imports.
Arrows read "depends on"; dotted arrows are lazy imports.

```mermaid
flowchart TD
    cli["cli.py<br/>comfy-env CLI + settings/debug TUIs"]
    facade["__init__.py<br/>public facade: install / setup_env / register_nodes"]
    install["install/<br/>build-time orchestration<br/>(plugin.py, workspace.py, helpers.py)"]
    isolation["isolation/<br/>runtime: wrap.py, metadata.py,<br/>model_patcher.py, workers/"]
    environment["environment/<br/>workspace layout (cache.py),<br/>prestartup (setup.py), libomp.py"]
    packages["packages/<br/>pixi.py, cuda_wheels.py,<br/>toml_generator.py, node_dependencies.py"]
    detection["detection/<br/>backend.py, cuda.py, gpu.py"]
    config["config/<br/>comfy-env.toml parsing"]
    settings["settings.py<br/>feature flags"]
    debug["debug.py<br/>debug categories"]

    cli --> facade
    facade --> install
    facade --> isolation
    facade --> environment
    install --> packages
    install --> environment
    install --> detection
    install --> config
    isolation --> environment
    isolation --> packages
    isolation --> install
    isolation --> config
    isolation --> settings
    isolation --> debug
    environment --> detection
    environment --> settings
    packages --> detection
    packages --> config
    detection -.->|"lazy: cuda.py uses pixi info"| packages
```

The three lazy-broken cycles, all inside `isolation/`:

- `wrap.py` <-> `metadata.py` (proxy building needs the worker pool; metadata
  fetch needs env resolution from wrap)
- `wrap.py` <-> `workers/subprocess.py` (`subprocess.py:334` lazily imports
  `build_isolation_env`)
- `tensor_utils.py` -> `workers/subprocess.py` (`tensor_utils.py:63`, for the
  CUDA IPC metadata cache)

See the [module inventory](modules.md) for every file's responsibility.

## Build time: `install()`

```mermaid
flowchart TD
    entry["plugin's install.py<br/>from comfy_env import install; install()"]
    entry --> plugin
    entry --> discover

    subgraph plughalf["Plugin half -- install/plugin.py (main env)"]
        plugin["Install [node_reqs] peers:<br/>git clone or Comfy Registry download"]
        plugin --> reqs["Re-run plugin requirements.txt<br/>in the main env"]
    end

    subgraph wshalf["Workspace half -- install/workspace.py (isolated envs)"]
        discover["Discover every comfy-env.toml<br/>under custom_nodes"]
        discover --> torchpin["Resolve bootstrap torch pin from host<br/>(CPU-only build when no GPU)"]
        torchpin --> combo["Pick CUDA wheel combo<br/>packages/cuda_wheels.py"]
        combo --> gen["Generate per-env pixi.toml<br/>packages/toml_generator.py"]
        gen --> hash{"env config<br/>hash changed?"}
        hash -->|"no"| skip["Skip -- env up to date"]
        hash -->|"yes"| pinstall["pixi install --manifest-path<br/>envs/&lt;name&gt;/pixi.toml (per env)"]
        pinstall --> stamp["write_env_stamp:<br/>Python ABI + comfy-env version + torch pin"]
    end

    stamp --> done["Materialized env at<br/>envs/&lt;name&gt;/.pixi/envs/default/"]
```

Installs run **per env manifest** deliberately: a parse error in one env's
`pixi.toml` cannot poison another env's scan or install
(`environment/cache.py` module docstring). The host's torch family pin is
replicated verbatim into every generated feature so parent and workers share
an identical torch
([ADR-0007](adr/0007-machine-wide-workspace-with-per-env-manifests.md)).

## Startup: `setup_env()`

The prestartup hook (`environment/setup.py`) is thin: enable `faulthandler`,
print the workspace banner (`[OK]` / `[MISSING -- run install.py]` per env),
dedupe macOS libomp copies, optionally register the shareable CUDA pool hook,
and ensure ComfyUI's `base_directory` is set.

## Runtime: `register_nodes()` and the process boundary

```mermaid
flowchart LR
    subgraph parent["ComfyUI main process (host env)"]
        reg["register_nodes()<br/>isolation/wrap.py"]
        meta["Metadata scan<br/>isolation/metadata.py<br/>(short-lived subprocess in the env)"]
        proxy["Synthesized proxy node classes<br/>(parent never imports node code)"]
        sw["SubprocessWorker<br/>workers/subprocess.py<br/>(persistent, one per env, auto-restart)"]
        ipcp["workers/_ipc_parent.py<br/>sockets + serialization"]
        mp["SubprocessModelPatcher<br/>isolation/model_patcher.py"]
        mm["comfy.model_management<br/>(ComfyUI VRAM manager)"]
        reg --> meta --> proxy
        proxy -->|"node execution"| sw
        sw --> ipcp
        mm -->|"eviction: unpatch_model()"| mp
        mp -->|"move model to CPU via IPC"| sw
    end

    subgraph wp["Worker subprocess (isolated pixi env)"]
        pw["workers/_persistent_worker.py<br/>main loop, watchdog,<br/>own copy of the serialization stack"]
        node["Actual node code"]
        pw --> node
    end

    shared["workers/_ipc_shared.py<br/>stdlib-only; copied (not imported)<br/>to the worker's temp dir"]

    ipcp ==>|"AF_UNIX socket (TCP on Windows)<br/>length-prefixed JSON + shared-memory tensors"| pw
    pw -.->|"callbacks: progress, VRAM budget"| sw
    ipcp --- shared
    pw --- shared
```

Key facts the diagram encodes:

- **The parent never imports node code.** `metadata.py` spawns a short-lived
  subprocess inside the isolation env to pickle out `INPUT_TYPES` /
  `RETURN_TYPES` etc., then synthesizes proxy classes in the parent
  ([ADR-0001](adr/0001-process-isolation-via-persistent-subprocess-workers.md)).
- **`_persistent_worker.py` is never imported by the parent.** It crosses the
  process boundary as *source text*: read at `subprocess.py:106-109` and
  materialized into a temp dir for the isolated interpreter
  ([ADR-0006](adr/0006-worker-crosses-the-boundary-as-source-text.md)).
- **`_ipc_shared.py` exists on both sides.** It is deliberately stdlib-only
  and is copied next to the worker script so the worker can `import
  _ipc_shared` without having comfy-env installed.
- **Models stay resident in workers** but participate in ComfyUI's VRAM
  accounting via `SubprocessModelPatcher` -- the only module that imports
  ComfyUI at module scope.

## Tensor serialization ladder

Results and inputs cross the boundary via the first applicable strategy
([ADR-0005](adr/0005-tiered-tensor-serialization.md)):

| # | Strategy | Wire type | Mechanism | Copies | Constraints |
|---|----------|-----------|-----------|--------|-------------|
| 1 | CUDA IPC | `CudaIPC` | `reduce_tensor()` / `rebuild_cuda_tensor()` | zero-copy GPU | Linux only; broken under `cudaMallocAsync` |
| 2 | Pool IPC | `PoolIPC` | `cudaMemPoolExportPointer` + FD passing | zero-copy GPU | in progress; the `cudaMallocAsync` fix |
| 3 | Torch shared memory | `TensorRef` | `file_system` strategy (/dev/shm) | zero-copy CPU | |
| 4 | NumPy | -- | converted to torch tensor, then #3 | zero-copy CPU | |
| 5 | Trimesh / pickle | -- | pickled into a `SharedMemory` block | 1 copy | anything picklable |
| 6 | Primitives | -- | inline in the JSON message | -- | small values |

!!! warning "Known caveat"
    ComfyUI sets `PYTORCH_CUDA_ALLOC_CONF=backend:cudaMallocAsync`, which
    breaks legacy CUDA IPC (`reduce_tensor()` raises). The `_probe_cuda_ipc()`
    checks on both sides test `Event` + allocation but not `reduce_tensor()`,
    so they can report IPC as available when it is not. Pool IPC (strategy 2)
    is the in-progress fix; until then the ladder falls back to CPU shared
    memory.

## Where to go next

- [Module inventory](modules.md) -- what every file under `src/comfy_env/` does
- [Decision records](adr/index.md) -- the "why" behind each of these choices
