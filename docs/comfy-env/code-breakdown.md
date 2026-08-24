# Code breakdown -- named and shamed

Where the lines actually go. **12,797 lines of Python across 38 files** under
`src/comfy_env/` (raw `wc -l`, blanks and comments included).

Snapshot at **v0.4.28 (2026-08-24)**. This page is a photograph and it *will*
drift; regenerate with:

```
find src/comfy_env -name '*.py' | xargs wc -l | sort -rn
```

!!! note "This page used to lie in three places"
    The previous snapshot (v0.4.20, 13,733 lines) inflated the transport
    duplication, counted four whole files as "monkey-patch surface", and
    claimed a file was big because its problem was big when half of it was
    dead code. Each is corrected below, with the measurement.

## By subsystem

| Subsystem | Lines | % |
|---|--:|--:|
| Transport / worker IPC | 4,662 | 36% |
| Environment build / install / wheels | 3,525 | 28% |
| Node registration / proxy / ComfyUI glue | 2,874 | 22% |
| Config / CLI / misc | 1,169 | 9% |
| Hardware detection | 567 | 4% |
| **Total** | **12,797** | 100% |

Two subsystems -- the transport and the env builder -- are **64% of the
project**. That is the honest shape of comfy-env: a serialization stack and a
manifest compiler, with a ComfyUI adapter bolted on.

## What shrank, and why

0.4.27 and 0.4.28 removed **936 lines**. A four-reviewer panel proposed ~2,600;
two adversarial reviewers then attacked every claim against the ~30-pack fleet,
comfy-test, cuda-wheels and live PyPI, and **roughly a third did not survive**.
The survivors, in order of size:

- **`toml_generator.py`, 870 -> 451.** The entire v0.3 workspace-wide path
  survived ADR-0007's supersession and was never deleted. Two tells that nobody
  had called it in a long time: both builders are annotated `-> Dict[str, Any]`
  / `-> Path` and actually return 2-tuples, and `parse_comfyui_requirements`
  had zero callers *even inside its own module*.
- **The worker's by-reference object cache (~103).** `_serialize_result` was
  the only emitter of a `__comfy_ref__` frame and its only callers were its own
  three recursive calls -- so no frame was ever produced, which left
  `_deserialize_input` as an identity tree-walk over every input of every call.
- **Parent->worker Pool IPC (~89).** The sending half went in 0.4.21; the
  receiving half stayed. Removed as one coordinated edit -- deleting the
  parent's send *without* the worker's matching recv would have added a
  five-second stall to every worker start.
- **`lint.py` (130) moved, not fixed.** It guessed import names with
  `name.replace("-", "_")`, so a top-level `import faithcontour` (from dist
  `faithc-aot`) matched nothing and passed silently. Install is the one moment
  the answer is knowable, so `write_env_stamp` now records `accel_imports`
  resolved by asking the env's own interpreter. The check lives in comfy-test
  now, where it is exact.
- **`os.fsync` in `wlog` (1 line, the cheapest win here).** 2.78 ms vs 0.02 ms
  per log line on ext4, across 92 call sites, 13 of them inside `_from_shm`'s
  per-node recursion -- 50-100 ms of pure fsync on a typical call, more than
  the transport it was instrumenting.

**The reason it accumulated:** ruff was pinned to `E9/F63/F7/F82`, so `F401`
was invisible. It is on now, with `F841`, and it immediately surfaced 20 dead
imports -- **six of them hidden behind `# noqa: F401` comments that were false
in four separate files**, one claiming "re-exported names used below" for a
block where five of thirteen names were untouched.

## Transport / worker IPC -- 4,662 lines (the biggest sink)

| File | Lines | What it is |
|---|--:|---|
| `isolation/workers/_persistent_worker.py` | 1,739 | The entire worker program, shipped to the far env as source text ([ADR-0006](adr/0006-worker-crosses-the-boundary-as-source-text.md)). Main loop, transport, faulthandler/watchdog, the `print`/logger hijack. Still the largest file. **But see the footnote -- 415 of these lines are not transport at all.** |
| `isolation/workers/subprocess.py` | 1,092 | Parent-side `SubprocessWorker`: spawn, authkey handshake, health, call/echo, consumed-ack, the canary + device-identity checks. |
| `isolation/workers/_ipc_shared.py` | 845 | The shared serialization core both sides import: the `_to_shm` walker, the registry, `OpaquePayload`, and the MESH/VOXEL/SPLAT codecs added in 0.4.28. The one comfy_env-import-free leaf. |
| `isolation/workers/_ipc_parent.py` | 716 | Parent transport internals: `SocketTransport`, tensor strategies, `_from_shm`. |
| `isolation/tensor_utils.py` | 174 | `TensorKeeper`, madvise reclaim. ~85 of these lines have **zero callers** and survive only as public re-exports. |
| `isolation/workers/base.py` | 82 | `Worker` ABC + `WorkerError`. |
| `isolation/workers/__init__.py` | 14 | Re-exports. |

!!! warning "415 of `_persistent_worker.py` is filed here and shouldn't be"
    24% of the worker is ComfyUI co-management, not transport: **252 lines
    replace or mutate globals ComfyUI owns** -- `torch.nn.Module.to`/`.cuda`
    (`:1118-1119`), `comfy.model_management.load_models_gpu` (`:1434`),
    `comfy.cli_args.args` (`:1322`, `:1328`, `:1345`) -- and another 163 are
    the far half of the eviction protocol. Re-filing them would make Transport
    4,247 (33%) and Node/glue 3,289 (26%).

### The duplication, measured

The previous snapshot said "~600-800 lines that exist twice". Measured with
`SequenceMatcher` over the eight forked pairs:

| Pair | parent | worker | byte-identical |
|---|--:|--:|--:|
| `SocketTransport` | 62 | 52 | 29 |
| `_from_shm` | 85 | 96 | 37 |
| `_serialize_cuda_ipc` | 53 | 43 | 34 |
| `_deserialize_cuda_ipc` | 37 | 32 | 27 |
| `_probe_cuda_ipc` | 23 | 34 | 20 |
| `_serialize_tensor_native*` | 56 | 44 | 35 |
| `_deserialize_tensor_*` | 52 | 63 | 36 |
| `TensorKeeper` | 13 | 32 | 7 |
| **Total** | **381** | **396** | **225** |

**~388 lines per copy, 777 in total, 225 byte-identical.** Read as
"per copy", the old figure was roughly double the truth.

**And it is not forced.** `_ipc_parent.py` already parses clean under Python
3.9 and its only `comfy_env` import is the debug flag. `_ipc_shared.py` is
*already* copied next to the worker (`subprocess.py`) and imported by it, so
the read-as-text delivery demonstrably satisfies cross-module imports today.
What is genuinely side-specific is ~115 lines of **policy** -- who owns the shm
payload, which keeper, thread-local vs global pool state -- and one real
semantic difference: the two `recv()` implementations disagree on EOF vs
timeout, and `subprocess.py` branches on that difference to tell a crash from a
hang. Merge them carelessly and a segfault reports as a ten-minute stall.

## Environment build / install / wheels -- 3,525 lines

| File | Lines | What it is |
|---|--:|---|
| `install/workspace.py` | 1,017 | Workspace materialization: discover configs, resolve torch pin, pick wheel combo, hash for change detection, `pixi install` per env, stamp. |
| `environment/cache.py` | 620 | Env identity, ABI tags, workspace layout, the Windows LOCALAPPDATA guard. |
| `packages/toml_generator.py` | 451 | The manifest compiler: each `comfy-env.toml` -> a per-env `pixi.toml` ([ADR-0013](adr/0013-env-file-passthrough-contract.md)). |
| `packages/cuda_wheels.py` | 431 | CUDA wheel index resolution ([ADR-0004](adr/0004-prebuilt-cuda-wheel-index.md)). |
| `packages/node_packs.py` | 188 | `[node_packs]` peer-pack install ([ADR-0016](adr/0016-node-pack-dependencies.md)). |
| `install/helpers.py` | 139 | Install-time helpers. |
| `environment/libomp.py` | 137 | macOS libomp dedupe, and a result record so a pass that fixed nothing says so. |
| `install/plugin.py` | 117 | Plugin half + the sibling-pin warning ([ADR-0022](adr/0022-comfy-env-placement-in-host-env.md)). |
| `pixi.py` | 111 | Pinned, sha256-verified pixi-binary provisioning. |
| `install/__init__.py` | 85 | `install()` entry. |
| `environment/runtime.py` | 84 | The `RuntimeEnv` contract behind `comfy-env info --json`. |
| `environment/setup.py` | 73 | `setup_env()`: faulthandler, libomp dedupe. |
| `packages/__init__.py` / `environment/__init__.py` / `install/verify.py` | 27 / 27 / 18 | Small. |

!!! danger "The old claim about this section was wrong"
    The previous snapshot said the two 1,000-line files were "large because the
    input space (conda + PyPI + CUDA combos x platforms) genuinely is."

    `toml_generator.py` is now **451**, and **412 of the 419 lines deleted were
    a dead v0.3 code path** -- so 47% of its old size was never input-space
    complexity at all. And `workspace.py` is large mostly because
    `install_workspace` is a **single 422-line function**: a linear twelve-phase
    pipeline nobody has cut into its named phases.

    The honest version: *the wheel-combo resolver (138 lines) and the per-node
    feature builder (105) are large because the input space is. The rest is
    large because it hasn't been split.*

## Node registration / proxy / ComfyUI glue -- 2,874 lines

| File | Lines | What it is |
|---|--:|---|
| `isolation/metadata.py` | 1,265 | The scan subprocess + proxy synthesis ([ADR-0023](adr/0023-metadata-scan-and-proxy-synthesis.md)) -- the subsystem most exposed to ComfyUI schema churn (V1/V3 duality, DynamicCombo, hidden inputs). |
| `isolation/wrap.py` | 585 | `register_nodes()` orchestration. |
| `isolation/pool.py` | 581 | The worker pool: lifecycle, restart+generations, VRAM/progress callbacks, route proxying, the `_STALE_PATCHERS` invariant ([ADR-0019](adr/0019-worker-lifecycle.md)). |
| `isolation/model_patcher.py` | 277 | `SubprocessModelPatcher` -- resident models obey ComfyUI's VRAM manager. |
| `isolation/subenv.py` | 128 | Launch-env construction for the worker subprocess. |
| `isolation/__init__.py` | 38 | Re-exports. |

### The monkey-patch surface, recounted

The previous snapshot claimed **2,719 lines the upstream RFC would let
comfy-env delete**, and said every ComfyUI internal comfy-env touches lives in
`wrap.py`, `pool.py`, `metadata.py` and `model_patcher.py`. That number was
those four files' sizes added together. Both halves are wrong.

| File | Total | Actually touches ComfyUI | Share |
|---|--:|--:|--:|
| `metadata.py` | 1,265 | ~904 | 71% |
| `_persistent_worker.py` | 1,739 | 415 | 24% |
| `pool.py` | 581 | ~292 | 50% |
| `model_patcher.py` | 277 | 277 | 100% |
| `environment/cache.py` | 620 | ~91 | 15% |
| `workers/subprocess.py` | 1,092 | ~59 | 5% |
| `_ipc_shared.py` / `_ipc_parent.py` | 1,561 | ~54 | 3% |
| **`wrap.py`** | **585** | **4** | **0.7%** |

**~2,100 lines across nine files, not 2,719 across four.** `wrap.py` --
which contributed 590 to the old claim -- reaches into ComfyUI in exactly one
place: `folder_paths.base_path` at `:327-330`. Meanwhile the transport files
and `environment/cache.py` contribute ~200 lines the four-file framing missed
entirely.

And the three-hook RFC would retire far less than even 2,100: roughly
**1,150-1,400**. `metadata.py`'s 276-line embedded scan script and its 215-line
`fetch_metadata` survive any upstream hook, because scanning a pack's nodes
*in the far env* is comfy-env's job, not ComfyUI's.

## Config / CLI / misc -- 1,169 lines

| File | Lines | What it is |
|---|--:|---|
| `cli.py` | 687 | The `comfy-env` CLI. The only file whose size is user-facing surface rather than internal machinery -- though 183 of it is a settings TUI containing a 123-line nested `draw`. |
| `config/__init__.py` | 186 | The TOML config layer ([ADR-0003](adr/0003-two-config-files-with-two-roles.md), [ADR-0015](adr/0015-declared-wire-types.md)). |
| `settings.py` | 137 | The env-var control plane. |
| `__init__.py` | 98 | Package surface + the three-call contract re-exports. |
| `debug.py` | 61 | Debug categories. |

## Hardware detection -- 567 lines

| File | Lines | What it is |
|---|--:|---|
| `detection/gpu.py` | 270 | NVML -> nvidia-smi -> sysfs fallback chain. |
| `detection/cuda.py` | 125 | CUDA version probing. |
| `detection/__init__.py` | 84 | Platform helpers + the (os, machine) -> pixi platform table. |
| `detection/backend.py` | 70 | Backend selection. |
| `detection/arch.py` | 18 | CPU architecture, which the tier-2 wheel fallback is keyed on. |

The smallest subsystem, and the one that best matches its job size.

## The current shame

Ordered by severity, not size.

**1. The RPC envelope exists in triplicate -- and it has already cost a bug.**
`call_method` (96 lines), `call_module` (50) and `echo` (30) in
`subprocess.py` all run the identical sequence: lock, `_ensure_started`,
`_to_shm`, send, error-check, `_from_shm`, consumed-ack, cleanup. In 0.4.28 a
leak was fixed where **`echo()` omitted the `_cleanup_ipc_cache()` its two
siblings call**, leaving CUDA-IPC entries unevicted on every worker start.
Three copies of one function is why nobody noticed.

**2. `main()` in the worker is one 863-line function** (`:874-1736`),
containing 16 nested definitions totalling 356 lines. ADR-0006 justifies the
*module* being one program shipped as source text. It does not justify the
*function*. Nothing can move to another file while it closes over `main`'s
locals -- so this is the gate on every other worker-side cleanup.

**3. Two parallel wire formats, and a guaranteed-identity walk over one.**
Beside `_to_shm`/`_from_shm` there is a second serializer emitting
`__isolated_object__`/`__attrs__`/`__path__`, whose only producer is a single
call passing `self_state`. Yet the worker runs its decoder over **every input
of every call** -- structurally the same dead walk 0.4.27 deleted for
`__comfy_ref__`, still present for a different tag family. The two formats also
disagree about `Path`: one emits a bare string, the other `{"__path__": ...}`.

**4. The wire format carries four tag conventions at once** -- a `__type__`
discriminator, sentinel keys that *are* the tag, ride-along flags on a
neighbouring frame, and a registry namespace inside `__shm_custom__`. One key
carries two value types: `__shm_np__` is `True` on the fd path and the block
*name* on the copy path, so a reader must check `"fd" in obj` first or hand
`True` to `SharedMemory(name=...)`.

**5. Three `TensorKeeper` classes, three lifetimes, and only one implements the
ack protocol.** The worker's honours ADR-0032's consumed-ack release; **the
parent's is still pure TTL**, so parent->worker input tensors are pinned for a
fixed 60 s regardless of when the worker finishes reading. A third, in
`tensor_utils.py`, has a 30 s TTL and no callers at all.

**6. Both registration paths in `wrap.py` are copy-pasted**, and so are both
proxy call bodies in `metadata.py` -- the V1 and V3 bodies share **50 identical
lines**, the largest single-file clone site in the tree.

**7. A fourth NVML -> nvidia-smi ladder** lives in `pool.py`, re-implementing
what `detection/gpu.py` already does. There is no layering excuse: the
`package-layers` contract makes `isolation -> detection` a legal edge.

**8. Nine functions of 150+ lines account for ~2,000 lines, 16% of the
codebase** -- including `_ensure_started` (288), `install_workspace` (422) and
`register_nodes` (435).

## What is correctly big

- **`_persistent_worker.py`'s non-ComfyUI ~1,320 lines.** It ships to the far
  interpreter as source text and must parse under the oldest worker Python
  (3.9). A whole program in one *module* is the right shape. A whole program in
  one *function* is not -- see shame #2.
- **`_to_shm_generic` (164).** One walker for both sides, twelve type rungs,
  cycle detection, pluggable tensor strategy. This is the de-duplication that
  already worked; the residual fork above is what's left.
- **`_resolve_wheel_combo` (138).** conda x PyPI x CUDA x torch x CPU-arch with
  a per-arch tier-2 fallback validated against a live index. The input space
  genuinely is this shape.
- **`model_patcher.py` (277), all of it.** Every one of its methods exists
  because `comfy.model_management` calls it. Deliberately *not* a
  `ModelPatcher` subclass ([ADR-0035](adr/0035-duck-typed-model-proxy.md)) --
  duck-typing is why it is honest rather than fragile.
- **`detection/gpu.py` (270).** Four detection methods with a
  platform-dependent order, because pynvml can crash the host process on
  Windows. Graceful degradation ([ADR-0008](adr/0008-graceful-degradation-everywhere.md))
  costs lines by design.
- **`environment/cache.py`'s identity core.** Two envs that hash the same and
  shouldn't is a silent wrong-torch bind.

## What the shape says

- **Delete-shaped work still exists, but it is smaller and better located than
  this page used to claim.** ~777 doubled transport lines (~388 per copy) and
  ~2,100 lines of host coupling, of which an upstream RFC would retire perhaps
  1,150-1,400.
- **The compiler and transport are the problem domain**, but that sentence was
  laundering two different things. The wheel resolver and the serialization
  ladder are irreducible. A 422-line install function and a 863-line worker
  `main()` are not.
- **The gate that lets this regrow is CI configuration, not architecture.**
  936 lines of dead code accumulated behind a linter that could not see unused
  imports, and four `# noqa` comments that asserted a use which did not exist.
  That gate is closed now. The next 936 will need a different excuse.
