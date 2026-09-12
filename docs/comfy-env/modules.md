# Module inventory

Everything lives under `src/comfy_env/`. Line counts are `wc -l` at
fe9ff74 (2026-09-12): 46 files, 19,264 lines. Layering, low to high:
`config` / `settings` / `debug` / `pixi` and the five memory-floor leaves ->
`detection` -> `packages` -> `environment` -> `install` + `isolation` ->
`cli` / `__init__` (the full edge list is on the
[import layering](import-layering.md) page).

## Top level

| File | ~LoC | Responsibility |
|------|-----:|----------------|
| `__init__.py` | 101 | Public facade. Exports the five-name public API (`install`, `setup_env`, `register_nodes`, `copy_files`, `register_serializer`); defines `__version__` from installed metadata; imports `settings` for its tombstone side effect. Internals are deliberately NOT re-exported -- `__getattr__` turns reaching for one into a signpost naming where it lives. |
| `cli.py` | 578 | The `comfy-env` console entrypoint: `init`, `install`, `info`, `settings`, `gc` (`cleanup` is a deprecated alias). Settings is a single-tab (Debug) curses TUI with a plain-text fallback, persisting to `~/.comfy-env/debug.env`. |
| `settings.py` | 79 | Tombstones for settings removed in 0.4.25: a falsy `COMFY_ENV_ISOLATE` / `COMFY_ENV_INSTALL_ISOLATED`, or a truthy `COMFY_ENV_AUTO_INSTALL`, raises at import with a self-locating message. No settings file, no TOML key mapping: the live settings are plain `COMFY_ENV_*` env vars read at their point of use. |
| `debug.py` | 65 | Granular debug-category switches (`SERIALIZE`, `IPC`, `WORKER`, `VRAM`, ...), env var or `~/.comfy-env/debug.env` (env var wins). Workers cannot import it (different venv) and parse env vars directly. |
| `pixi.py` | 111 | Provisions the **pinned** pixi binary (version + sha256 vendored in the file) into the comfy-env-owned `~/.comfy-env/pixi/<version>/` -- deliberately not `~/.pixi`, which belongs to the user's own install. Checksum mismatch refuses to install. |

## `config/`

| File | ~LoC | Responsibility |
|------|-----:|----------------|
| `config/__init__.py` | 189 | The entire config layer. Loads `comfy-env.toml` / `comfy-env-root.toml` via `tomli` into `ComfyEnvConfig` (dict subclass with dot access). In an env file, unknown tables are not errors -- they land in `pixi_passthrough` and the generator forwards them verbatim into the generated `pixi.toml`, refusing only the compiler-owned keys (ADR-0013). The root file is the opposite: a closed schema that rejects anything outside `[node_packs]`/`[types]` (see the config reference). |

## `detection/` -- pure functions, no side effects

| File | ~LoC | Responsibility |
|------|-----:|----------------|
| `detection/__init__.py` | 84 | Re-exports; platform helpers (`is_windows()` etc.) and the (os, machine) -> pixi platform table. |
| `detection/backend.py` | 70 | Which accelerator torch actually uses; ground truth is torch's local version label (`2.5.0+cu128` -> cuda). Fixes a ROCm misdetection where `torch.cuda.is_available()` is True but `torch.version.cuda` is None. |
| `detection/cuda.py` | 132 | CUDA version probing (`pixi info --json` virtual packages, then torch metadata) and bootstrap-interpreter probing: host torch / torchvision / torchaudio versions, macOS min version. |
| `detection/gpu.py` | 270 | GPU enumeration with a 4-method fallback chain (NVML -> nvidia-smi -> PyTorch -> sysfs; on Windows nvidia-smi -> NVML -> PyTorch, because a pynvml or torch DLL failure can crash the host), 60s TTL cache; compute capability -> architecture name -> recommended CUDA version. |

## `packages/` -- dependency sourcing and manifest generation

| File | ~LoC | Responsibility |
|------|-----:|----------------|
| `packages/cuda_wheels.py` | 433 | Resolves prebuilt CUDA wheel URLs from the cuda-wheels GitHub Pages simple index; retries TCP resets with a real User-Agent; falls back to the GitHub Releases API. Derives torch family pins and platform tags. |
| `packages/toml_generator.py` | 683 | The manifest compiler: each `comfy-env.toml` -> a per-env `pixi.toml`. One self-contained feature per env, always named `node` (pixi reserves `default`), in a single environment `default` with `no-default-feature = true`; torch-family pins replicated into every feature; the host's `comfy-aimdo` / `comfy-kitchen` pins read from the running host (`read_host_pin`) and substituted for, or injected beside, the pack's own; CUDA wheels inlined as URL pypi-dependencies. |
| `packages/node_packs.py` | 159 | Installs other ComfyUI nodepacks declared in `[node_packs]`: git clone (tag / branch / commit), or a Comfy Registry zip (`api.comfy.org`), then runs the peer's `install.py` if it has one. No `requirements.txt` step. Recurses into the peer's own `[node_packs]`. |

## `environment/` -- paths and platform workarounds

| File | ~LoC | Responsibility |
|------|-----:|----------------|
| `environment/cache.py` | 794 | Workspace layout authority (v0.4 per-env manifests; no v0.3 back-compat). Env naming and the ABI-tagged directory name (`<name>_<abi-tag>`), workspace root resolution (Windows LocalAppData vs `~/.ce`, `COMFY_ENV_ROOT` override), env stamping and validation -- `validate_env_stamp` refuses on a mismatched `abi_tag` or `source` only; the comfy-env version, torch pin, ComfyUI version and host pins are recorded in the stamp for diagnostics and drift notes, never validated -- and ComfyUI dir discovery incl. the Desktop app. |
| `environment/setup.py` | 73 | The prestartup hook `setup_env()`: faulthandler, workspace banner, libomp dedupe. (The parent-side shareable-pool hook was removed in 0.4.22; the `base_directory` fill-in in 0.4.27.) |
| `environment/runtime.py` | 84 | The `RuntimeEnv` record behind `comfy-env info --json`. |
| `environment/libomp.py` | 151 | macOS-only: symlinks redundant bundled `libomp.dylib` copies to torch's canonical one (multiple loaded copies corrupt OMP state and SIGSEGV). |

## `install/` -- build time

| File | ~LoC | Responsibility |
|------|-----:|----------------|
| `install/__init__.py` | 73 | `install()` entrypoint; infers the caller's directory via `inspect.stack()`; runs the `[node_packs]` step, then `install_workspace`. No pip step. Importing it also sets `PYTHONUNBUFFERED=1` and switches `sys.stdout` / `sys.stderr` to line buffering for the whole process. |
| `install/plugin.py` | 26 | Plugin half: one function, `_install_node_packs`, which lists what it would clone under `--dry-run` and otherwise calls `install_node_packs`. Nothing re-runs the plugin's `requirements.txt`. |
| `install/workspace.py` | 1,053 | Workspace half: discover configs, resolve bootstrap torch pin (CPU-only without GPU), pick wheel combo, hash configs for change detection, write per-env `pixi.toml`, run `pixi install` per env, stamp. |
| `install/progress.py` | 244 | Live `pixi install` progress from the filesystem: counts `conda-meta/*.json` and `site-packages/*.dist-info` against what `pixi.lock` declares, because pixi suppresses its own bar when stderr is piped. |
| `install/helpers.py` | 126 | Cross-platform utilities: `_rmtree` via robocopy-mirror-from-empty-dir (defeats Windows long-path/read-only deletes), uv discovery and platform patch, tee logging, streaming subprocess runner. |

## `isolation/` -- runtime

| File | ~LoC | Responsibility |
|------|-----:|----------------|
| `isolation/wrap.py` | 576 | Runtime entry point: `register_nodes()`, config discovery, per-pack env resolution, proxy synthesis, and the in-process fallback when an env is absent. The worker pool moved to `pool.py`. |
| `isolation/pool.py` | 1,936 | The worker pool and the whole host side of the memory floor: one worker per env with a generation counter, the VRAM budget callback workers call back into, the reserve publish, the stand-in registration into ComfyUI's ledger, the idle sweep and the pressure hook, `atexit` and signal cleanup, stale-worker reaping. |
| `memory_manager.py` | 1,038 | The worker side of the floor. Enables comfy-aimdo, the release ladders (`full_release`, `partial_release`, `release_pins`), the per-node and per-prompt cast boundaries, prompt marks, and the pin census the host reads. Staged into the worker, so it must parse on the oldest worker Python. |
| `state_sync.py` | 892 | Pure arithmetic and policy, imports neither torch nor comfy: the residency census and its sequence protocol, the admission ceiling, the idle and pressure release planners, the prompt-epoch marks, and the node `self` state wire in both directions. |
| `contract.py` | 326 | The upstream coupling contract as data. Thirty entries with per-entry severity and tier (`floor` / `paged` / `shared`) and the version each appeared in, checked against the real tree at startup; a fatal gap refuses to start. Imports nothing; staged into workers. |
| `mirrored_args.py` | 197 | The host-to-worker CLI flag mirror, as an allowlist. Decides a worker's dtype, attention backend and pinning behaviour, with a global kill switch and a per-flag escape hatch. |
| `reserve.py` | 168 | The reserve arithmetic, pure: what a worker is charged, what gets published, what is forwarded into the pager's headroom, and the discount that stops a requester being charged for its own load twice. |
| `isolation/errors.py` | 88 | The closed error vocabulary that crosses the wire, including translating a worker OOM back into the host's real exception class. |
| `isolation/subenv.py` | 121 | Per-platform isolation env construction. |
| `isolation/procgroup.py` | 66 | Runs a child in its own process group so a timeout kills the whole tree (`killpg` on POSIX, `taskkill /T` on Windows); the metadata scan under `pixi run` uses it. Stdlib only. |
| `server_stub.py` | 110 | Top-level, stdlib-only stand-in for ComfyUI's `server` module, installed into `sys.modules` in workers and in the scan. `PromptServer.instance` forwards `send_sync`, `send_progress_text` and carries the host's `client_id`; any other attribute raises an `AttributeError` naming what is and is not forwarded. |
| `isolation/metadata.py` | 1,891 | Spawns a short-lived subprocess in the isolation env to write out node metadata as JSON (`INPUT_TYPES`, ...), then synthesizes proxy classes in the parent. Handles ComfyUI v3 schema, dynamic combo providers (live model/input-dir dropdowns), synthesized validation, and a scan cache keyed on every `.py` file's `st_mtime_ns`. Both proxy shapes call the worker through one `_call_in_worker`. |
| `isolation/model_patcher.py` | 401 | `SubprocessModelPatcher`: bridges worker-resident GPU models into ComfyUI's VRAM manager; eviction IPCs the worker to move the model to CPU. Only module importing ComfyUI at module scope. |
| `isolation/tensor_utils.py` | 83 | `TensorKeeper` (prevents GC races on shared tensors), IPC preparation, `release_tensor()` via `madvise(MADV_DONTNEED)`. |

## `isolation/workers/`

| File | ~LoC | Responsibility |
|------|-----:|----------------|
| `workers/base.py` | 73 | `Worker` ABC (`call()`, `shutdown()`), `WorkerError`, and `InterruptRequested` (raised parent-side, in the progress-callback handler, when the user cancels the run, so the callback reply carries a typed `error_kind` the worker can act on). |
| `workers/subprocess.py` | 1,371 | Parent-side driver: spawns the isolated interpreter, materializes `_persistent_worker.py` into a temp dir with copies of `_ipc_shared.py` and the staged leaves (`memory_manager.py`, `state_sync.py`, `mirrored_args.py`, `contract.py`, `server_stub.py`), handshake, health checks, request/response with timeouts, bidirectional callbacks, exit diagnostics. |
| `workers/_ipc_parent.py` | 526 | Parent-side IPC internals: socket creation (AF_UNIX, TCP fallback), `SocketTransport` (thread-safe length-prefixed JSON), all tensor serialization strategies, shm helpers. |
| `workers/_ipc_shared.py` | 943 | Deliberately stdlib-only so it can be *copied* beside the worker script and imported in the isolated venv: CUDA mem-pool ctypes bindings, `SCM_RIGHTS` FD passing, memfd helpers, generic shm walker. |
| `workers/_persistent_worker.py` | 2,762 | The worker program. Never imported by the parent -- read as text (`subprocess.py`) and run by the isolated interpreter with `_ipc_shared.py` and the staged leaves copied alongside (the shared serialization core; the worker keeps only thin side-specific wrappers). Faulthandler + watchdog, the `print`/logger hijack, the worker half of the memory floor, and the main loop -- one 1,957-line `main()`. |
