# Module inventory

Everything lives under `src/comfy_env/`. Line counts are approximate
(v0.4.12). Layering, low to high: `config` / `settings` / `debug` ->
`detection` -> `packages` -> `environment` -> `install` + `isolation` ->
`cli` / `__init__`.

## Top level

| File | ~LoC | Responsibility |
|------|-----:|----------------|
| `__init__.py` | 199 | Public facade. Re-exports the whole API grouped by layer; defines `__version__` from installed metadata; runs `_mock_cuda_packages()` at import (stub modules from `COMFY_TEST_MOCK_PACKAGES` so CPU-only machines can import). |
| `cli.py` | 682 | The `comfy-env` console entrypoint: `init`, `generate`, `install`, `info`, `doctor`, `settings`, `debug`, `gc`. Settings/debug are curses TUIs with a plain-text fallback. |
| `settings.py` | 108 | Feature-flag resolution, 3-tier priority: env var > `~/.comfy-env/settings.env` > default. Maps short TOML keys (`isolate`, `pool_ipc`) to `COMFY_ENV_*` vars for per-node override. |
| `debug.py` | 61 | Granular debug-category switches (`SERIALIZE`, `IPC`, `WORKER`, `VRAM`, ...), same 3-tier priority via `~/.comfy-env/debug.env`. Workers cannot import it (different venv) and parse env vars directly. |

## `config/`

| File | ~LoC | Responsibility |
|------|-----:|----------------|
| `config/__init__.py` | 104 | The entire config layer. Loads `comfy-env.toml` / `comfy-env-root.toml` via `tomli` into `ComfyEnvConfig` (dict subclass with dot access). Unknown TOML keys are not errors -- they land in `pixi_passthrough`; the generator then copies only an allowlist of them into the generated `pixi.toml` (see the config reference). |

## `detection/` -- pure functions, no side effects

| File | ~LoC | Responsibility |
|------|-----:|----------------|
| `detection/__init__.py` | 101 | Re-exports; platform helpers (`is_windows()` etc.) and the (os, machine) -> pixi platform table. |
| `detection/backend.py` | 73 | Which accelerator torch actually uses; ground truth is torch's local version label (`2.5.0+cu128` -> cuda). Fixes a ROCm misdetection where `torch.cuda.is_available()` is True but `torch.version.cuda` is None. |
| `detection/cuda.py` | 126 | CUDA version probing (`pixi info --json` virtual packages, then torch metadata) and bootstrap-interpreter probing: host torch / torchvision / torchaudio versions, macOS min version. |
| `detection/gpu.py` | 256 | GPU enumeration with a 4-method fallback chain (NVML -> PyTorch -> nvidia-smi -> sysfs), 60s TTL cache; compute capability -> architecture name -> recommended CUDA version. |

## `packages/` -- dependency sourcing and manifest generation

| File | ~LoC | Responsibility |
|------|-----:|----------------|
| `packages/pixi.py` | 51 | Bootstraps the pixi binary itself to `~/.pixi/bin/pixi[.exe]` from GitHub latest-release URLs if missing. |
| `packages/cuda_wheels.py` | 371 | Resolves prebuilt CUDA wheel URLs from the cuda-wheels GitHub Pages simple index; retries TCP resets with a real User-Agent; falls back to the GitHub Releases API. Derives torch family pins and platform tags. |
| `packages/toml_generator.py` | 802 | The manifest compiler: ComfyUI `requirements.txt` + each `comfy-env.toml` -> per-env `pixi.toml`. One self-contained `[feature.<env_name>]` per env with `no-default-feature = true`; torch pin replicated verbatim into every feature; CUDA wheels inlined as URL pypi-dependencies. |
| `packages/node_dependencies.py` | 188 | Installs other ComfyUI node packs declared in `[node_reqs]`: git clone or zip, or Comfy Registry (`api.comfy.org`), then their `requirements.txt` and `install.py`. |

## `environment/` -- paths and platform workarounds

| File | ~LoC | Responsibility |
|------|-----:|----------------|
| `environment/cache.py` | 604 | Workspace layout authority (v0.4 per-env manifests; no v0.3 back-compat). Env naming, workspace root resolution (Windows LocalAppData vs `~/.ce`, `COMFY_ENV_ROOT` override), env stamping/validation (ABI + version + torch pin), ComfyUI dir discovery incl. the Desktop app. |
| `environment/setup.py` | 178 | The prestartup hook `setup_env()`: faulthandler, workspace banner, libomp dedupe, optional shareable-pool hook, `base_directory` patch. |
| `environment/libomp.py` | 71 | macOS-only: symlinks redundant bundled `libomp.dylib` copies to torch's canonical one (multiple loaded copies corrupt OMP state and SIGSEGV). |

## `install/` -- build time

| File | ~LoC | Responsibility |
|------|-----:|----------------|
| `install/__init__.py` | 95 | `install()` entrypoint; infers the caller's directory via `inspect.stack()`; orchestrates node_reqs -> main-env pip -> workspace install (gated on `COMFY_ENV_INSTALL_ISOLATED`). |
| `install/plugin.py` | 71 | Plugin half: clone `[node_reqs]` peers, re-run the plugin's own `requirements.txt` in the main env. |
| `install/workspace.py` | 850 | Workspace half: discover configs, resolve bootstrap torch pin (CPU-only without GPU), pick wheel combo, hash configs for change detection, write per-env `pixi.toml`, run `pixi install` per env, stamp. |
| `install/helpers.py` | 164 | Cross-platform utilities: `_rmtree` via robocopy-mirror-from-empty-dir (defeats Windows long-path/read-only deletes), uv discovery and platform patch, tee logging, streaming subprocess runner. |
| `install/verify.py` | 18 | Post-install `__import__` check per package. |

## `isolation/` -- runtime

| File | ~LoC | Responsibility |
|------|-----:|----------------|
| `isolation/wrap.py` | 1015 | Runtime orchestrator: `register_nodes()`, persistent worker pool (one per env, auto-restart), per-platform isolation env construction, proxy registration, parent-side callbacks (progress, VRAM budget), atexit/signal cleanup, stale-worker reaping. |
| `isolation/metadata.py` | 995 | Spawns a short-lived subprocess in the isolation env to pickle out node metadata (`INPUT_TYPES`, ...), then synthesizes proxy classes in the parent. Handles ComfyUI v3 schema, dynamic option lists (model dropdowns), hash-keyed caching. |
| `isolation/model_patcher.py` | 213 | `SubprocessModelPatcher`: bridges worker-resident GPU models into ComfyUI's VRAM manager; eviction IPCs the worker to move the model to CPU. Only module importing ComfyUI at module scope. |
| `isolation/tensor_utils.py` | 186 | `TensorKeeper` (prevents GC races on shared tensors), IPC preparation, `release_tensor()` via `madvise(MADV_DONTNEED)`. |
| `isolation/auto_install.py` | 285 | Opt-in (`COMFY_ENV_AUTO_INSTALL`, default off) lazy env materialization at `register_nodes` time, with a file lock against concurrent startups; on failure returns None so ComfyUI still boots. |

## `isolation/workers/`

| File | ~LoC | Responsibility |
|------|-----:|----------------|
| `workers/base.py` | 82 | `Worker` ABC (`call()`, `shutdown()`), `WorkerError`. |
| `workers/subprocess.py` | 851 | Parent-side driver: spawns the isolated interpreter, materializes `_persistent_worker.py` + a copy of `_ipc_shared.py` into a temp dir, handshake, health checks, request/response with timeouts, bidirectional callbacks, exit diagnostics. |
| `workers/_ipc_parent.py` | 789 | Parent-side IPC internals: socket creation (AF_UNIX, TCP fallback), `SocketTransport` (thread-safe length-prefixed JSON), all tensor serialization strategies, shm helpers. |
| `workers/_ipc_shared.py` | 467 | Deliberately stdlib-only so it can be *copied* beside the worker script and imported in the isolated venv: CUDA mem-pool ctypes bindings, `SCM_RIGHTS` FD passing, memfd helpers, generic shm walker. |
| `workers/_persistent_worker.py` | 1851 | The worker program. Never imported by the parent -- read as text (`subprocess.py:106-109`) and run by the isolated interpreter. Own copy of the serialization stack, faulthandler + watchdog, object-reference cache, main loop. |
