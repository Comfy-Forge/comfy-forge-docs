# Code breakdown -- named and shamed

Where the lines actually go. **19,264 lines of Python across 46 files** under
`src/comfy_env/` (raw `wc -l`, blanks and comments included).

Snapshot at **fe9ff74 (2026-09-12)**. This page is a photograph and it *will*
drift, and it has: the snapshot before this one said 18,342 across 44 at
v0.4.38, and the one before that said 12,797 across 38 and was left standing
for 96 commits while the memory floor added most of the difference. Function
lengths below are `ast` spans (`end_lineno - lineno + 1`), file lengths are
`wc -l`. Regenerate with:

```
find src/comfy_env -name '*.py' | xargs wc -l | sort -rn
```

## By subsystem

| Subsystem | Lines | % |
|---|--:|--:|
| Transport / worker IPC (`isolation/workers/`) | 5,689 | 29.5% |
| Node registration / proxy / ComfyUI glue (`isolation/` excl. workers, plus `server_stub.py`) | 5,306 | 27.5% |
| Memory floor, shared and worker side (`memory_manager`, `state_sync`, `reserve`, `contract`, `mirrored_args`) | 2,621 | 13.6% |
| Environment build / install / wheels (`install/`, `packages/`, `environment/`, `pixi.py`) | 4,062 | 21.1% |
| Hardware detection | 574 | 3.0% |
| Config / CLI / misc | 1,012 | 5.3% |
| **Total** | **19,264** | 100% |

Two subsystems -- the transport and the ComfyUI glue -- are **57% of the
project**, and the memory floor is now roughly half the size of either. That is
the honest shape of comfy-env: a serialization stack, a ComfyUI adapter, and a
manifest compiler, with a memory floor that did not exist a version ago and is
already the fastest growing part.

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

## Transport / worker IPC -- 5,689 lines (the biggest sink)

`isolation/workers/` only. `tensor_utils.py` used to be listed here; it lives
one level up and is counted under the ComfyUI glue below, which is why these
rows now sum to the subsystem table's figure exactly.

| File | Lines | What it is |
|---|--:|---|
| `isolation/workers/_persistent_worker.py` | 2,762 | The entire worker program, shipped to the far env as source text ([ADR-0006](adr/0006-worker-crosses-the-boundary-as-source-text.md)). Main loop, transport, faulthandler/watchdog, the `print`/logger hijack. Still the largest file, and it has grown by more than a thousand lines since the memory floor started landing. **But see the footnote -- a large slice of it is not transport at all.** |
| `isolation/workers/subprocess.py` | 1,371 | Parent-side `SubprocessWorker`: spawn, authkey handshake, health, call/echo, consumed-ack, the canary + device-identity checks. |
| `isolation/workers/_ipc_shared.py` | 943 | The shared serialization core both sides import: the `_to_shm` walker, the registry, `OpaquePayload`, and the MESH/VOXEL/SPLAT codecs added in 0.4.28. The one comfy_env-import-free leaf. |
| `isolation/workers/_ipc_parent.py` | 526 | Parent transport internals: `SocketTransport`, tensor strategies, `_from_shm`. |
| `isolation/workers/base.py` | 73 | `Worker` ABC + `WorkerError` + `InterruptRequested`. |
| `isolation/workers/__init__.py` | 14 | Re-exports. |

!!! warning "Much of `_persistent_worker.py` is filed here and shouldn't be"
    A large part of the worker is ComfyUI co-management, not transport: lines
    that replace or mutate globals ComfyUI owns -- `torch.nn.Module.to`/`.cuda`,
    `comfy.model_management.load_models_gpu`, `comfy.cli_args.args` -- plus the
    far half of the eviction protocol. Re-filing them would move real weight
    from Transport to Node/glue.

    **The exact split is not restated here because it is a hand count and the
    file has grown 1,023 lines since it was made** (1,739 -> 2,762). The old
    figures were 252 lines of global mutation and 163 of eviction protocol,
    24% of the file, measured at the smaller size with line citations that no
    longer point where they did. Re-deriving them means reading the file, not
    scaling the old number.

### The duplication, measured

The previous snapshot said "~600-800 lines that exist twice". Measured with
`SequenceMatcher` over the eight forked pairs (measured at the v0.4.38
snapshot; not re-run for this one):

| Pair | parent | worker | byte-identical |
|---|--:|--:|--:|
| `SocketTransport` | 62 | 52 | 29 |
| `_from_shm` | 85 | 96 | 37 |
| `_serialize_cuda_ipc` | 53 | 43 | 34 |
| `_deserialize_cuda_ipc` | 37 | 32 | 27 |
| `_probe_cuda_ipc` | 23 | 34 | 20 |
| `_serialize_tensor_native*` | 56 | 44 | 35 |
| `_deserialize_tensor_*` | 52 | 63 | 36 |
| `TensorKeeper` (parent / worker) | — | 32 | 7 |
| **Total** | **381** | **396** | **225** |

**~388 lines per copy, 777 in total, 225 byte-identical.** Read as
"per copy", the old figure was roughly double the truth.

**And it is not forced.** `_ipc_parent.py` already parses clean at the worker
floor (3.10) and its only `comfy_env` import is the debug flag. `_ipc_shared.py` is
*already* copied next to the worker (`subprocess.py`) and imported by it, so
the read-as-text delivery demonstrably satisfies cross-module imports today.
What is genuinely side-specific is ~115 lines of **policy** -- who owns the shm
payload, which keeper, thread-local vs global pool state -- and one real
semantic difference: the two `recv()` implementations disagree on EOF vs
timeout, and `subprocess.py` branches on that difference to tell a crash from a
hang. Merge them carelessly and a segfault reports as a ten-minute stall.

## Environment build / install / wheels -- 4,062 lines

| File | Lines | What it is |
|---|--:|---|
| `install/workspace.py` | 1,053 | Workspace materialization: discover configs, resolve torch pin, pick wheel combo, hash for change detection, `pixi install` per env, stamp. |
| `environment/cache.py` | 794 | Env identity, ABI tags, workspace layout, the Windows LOCALAPPDATA guard. |
| `packages/toml_generator.py` | 683 | The manifest compiler: each `comfy-env.toml` -> a per-env `pixi.toml` ([ADR-0013](adr/0013-env-file-passthrough-contract.md)). |
| `packages/cuda_wheels.py` | 433 | CUDA wheel index resolution ([ADR-0004](adr/0004-prebuilt-cuda-wheel-index.md)). |
| `packages/node_packs.py` | 159 | `[node_packs]` peer-pack install ([ADR-0016](adr/0016-node-pack-dependencies.md)). |
| `install/helpers.py` | 126 | Install-time helpers. |
| `environment/libomp.py` | 151 | macOS libomp dedupe, and a result record so a pass that fixed nothing says so. |
| `install/plugin.py` | 26 | The `[node_packs]` peer-pack step: one function that calls `install_node_packs`. |
| `pixi.py` | 111 | Pinned, sha256-verified pixi-binary provisioning. |
| `install/__init__.py` | 73 | `install()` entry. |
| `environment/runtime.py` | 84 | The `RuntimeEnv` contract behind `comfy-env info --json`. |
| `environment/setup.py` | 73 | `setup_env()`: faulthandler, libomp dedupe. |
| `packages/__init__.py` / `environment/__init__.py` | 25 / 27 | Small. (`install/verify.py`, listed here at 18 lines in the previous snapshot, no longer exists.) |

!!! danger "The old claim about this section was wrong"
    The previous snapshot said the two 1,000-line files were "large because the
    input space (conda + PyPI + CUDA combos x platforms) genuinely is."

    `toml_generator.py` fell to **451** when **412 of the 419 lines deleted
    were a dead v0.3 code path** -- so 47% of its size at the time was never
    input-space complexity at all. It has since grown back to **683** on real
    work (wheel inlining, the torch-family rewrite), which does not undo the
    point: nearly half of it was once dead. And `workspace.py` is large mostly
    because `install_workspace` is **a single 430-line function**: a linear
    twelve-phase pipeline nobody has cut into its named phases.

    The honest version: *the wheel-combo resolver (132 lines) and the per-node
    feature builder (141) are large because the input space is. The rest is
    large because it hasn't been split.*

## Node registration / proxy / ComfyUI glue -- 5,306 lines

| File | Lines | What it is |
|---|--:|---|
| `isolation/metadata.py` | 1,891 | The scan subprocess + proxy synthesis ([ADR-0023](adr/0023-metadata-scan-and-proxy-synthesis.md)) -- the subsystem most exposed to ComfyUI schema churn (V1/V3 duality, DynamicCombo, hidden inputs). |
| `isolation/pool.py` | 1,936 | The worker pool: lifecycle, restart+generations, VRAM/progress callbacks, route proxying, the `_STALE_PATCHERS` invariant ([ADR-0019](adr/0019-worker-lifecycle.md)), and the host half of the memory floor -- which is where nearly all of its 1,355-line growth since the 581-line hand count went. |
| `isolation/wrap.py` | 576 | `register_nodes()` orchestration. |
| `isolation/model_patcher.py` | 401 | `SubprocessModelPatcher` -- resident models obey ComfyUI's VRAM manager. |
| `isolation/subenv.py` | 121 | Launch-env construction for the worker subprocess. |
| `isolation/errors.py` | 88 | Error translation across the boundary: a closed vocabulary, never a pickled exception class. |
| `isolation/tensor_utils.py` | 64 | clone-on-foreign-storage for CUDA re-export, madvise reclaim. |
| `server_stub.py` | 110 | The stand-in `server` module staged beside the worker and the scan: `PromptServer.instance` forwards `send_sync` / `send_progress_text` / `client_id`, everything else raises with a pointer to the docs. Top-level, stdlib-only, so it parses on the oldest worker Python. |
| `isolation/procgroup.py` | 66 | Run a child in its own process group so a timeout kills its whole tree (`killpg` on POSIX, `taskkill /T` on Windows), for the scan under `pixi run`. |
| `isolation/__init__.py` | 34 | Re-exports. |

### The monkey-patch surface, recounted

The previous snapshot claimed **2,719 lines the upstream RFC would let
comfy-env delete**, and said every ComfyUI internal comfy-env touches lives in
`wrap.py`, `pool.py`, `metadata.py` and `model_patcher.py`. That number was
those four files' sizes added together. Both halves are wrong.

!!! warning "The right-hand columns are a hand count from an older tree"
    "Actually touches ComfyUI" was measured by reading each file, so it cannot
    be regenerated by `wc`. The **Total** column below is refreshed; the
    measured column and its share are **as of the v0.4.31 snapshot** and are
    shown against the totals they were taken from. Do not read a share as
    current -- `pool.py` alone has more than tripled since. The shape of the
    finding survives (the file named as the monkey-patch surface, `wrap.py`,
    touches almost nothing); the percentages do not.

| File | Total now | Total when measured | Touched ComfyUI (at that size) | Share then |
|---|--:|--:|--:|--:|
| `metadata.py` | 1,891 | 1,265 | ~904 | 71% |
| `_persistent_worker.py` | 2,762 | 1,739 | 415 | 24% |
| `pool.py` | 1,936 | 581 | ~292 | 50% |
| `model_patcher.py` | 401 | 277 | 277 | 100% |
| `environment/cache.py` | 794 | 620 | ~91 | 15% |
| `workers/subprocess.py` | 1,371 | 1,092 | ~59 | 5% |
| `_ipc_shared.py` / `_ipc_parent.py` | 1,469 | 1,561 | ~54 | 3% |
| **`wrap.py`** | **576** | **585** | **4** | **0.7%** |

**~2,100 lines across nine files, not 2,719 across four.** `wrap.py` --
which contributed 590 to the old claim -- reaches into ComfyUI in exactly one
place: it reads `folder_paths.base_path` to find the Desktop app's user data
directory. Meanwhile the transport files
and `environment/cache.py` contribute ~200 lines the four-file framing missed
entirely.

And the three-hook RFC would retire far less than even 2,100: roughly
**1,150-1,400**. `metadata.py`'s 419-line embedded scan script
(`_METADATA_SCRIPT`) and its 264-line `fetch_metadata` survive any upstream
hook, because scanning a pack's nodes
*in the far env* is comfy-env's job, not ComfyUI's.

## Config / CLI / misc -- 1,012 lines

| File | Lines | What it is |
|---|--:|---|
| `cli.py` | 578 | The `comfy-env` CLI. The only file whose size is user-facing surface rather than internal machinery -- though ~240 of it is the debug-settings TUI (curses plus a plain-text fallback) containing a 100-line nested `draw`, and `cmd_gc` is another 114. |
| `config/__init__.py` | 189 | The TOML config layer ([ADR-0003](adr/0003-two-config-files-with-two-roles.md), [ADR-0015](adr/0015-declared-wire-types.md)). |
| `settings.py` | 79 | Tombstones for the settings removed in 0.4.25, and nothing else. The live settings are env vars read at their point of use; there is no settings file and no resolution layer. |
| `__init__.py` | 101 | Package surface + the three-call contract re-exports. |
| `debug.py` | 65 | Debug categories. |

## Hardware detection -- 574 lines

| File | Lines | What it is |
|---|--:|---|
| `detection/gpu.py` | 270 | The NVML -> nvidia-smi -> PyTorch -> sysfs fallback chain (nvidia-smi first on Windows, where a DLL failure in pynvml or torch can take the process down). |
| `detection/cuda.py` | 132 | CUDA version probing. |
| `detection/__init__.py` | 84 | Platform helpers + the (os, machine) -> pixi platform table. |
| `detection/backend.py` | 70 | Backend selection. |
| `detection/arch.py` | 18 | CPU architecture, which the tier-2 wheel fallback is keyed on. |

The smallest subsystem, and the one that best matches its job size.

## The current shame

Ordered by severity, not size.

**1. The RPC envelope exists in triplicate -- and it has already cost a bug.**
`call_method` (103 lines), `call_module` (49) and `echo` (32) in
`subprocess.py` all run the identical sequence: lock, `_ensure_started`,
`_to_shm`, send, error-check, `_from_shm`, consumed-ack, cleanup. In 0.4.28 a
leak was fixed where **`echo()` omitted the `_cleanup_ipc_cache()` its two
siblings call**, leaving CUDA-IPC entries unevicted on every worker start.
Three copies of one function is why nobody noticed.

**2. `main()` in the worker is one 1,957-line function**,
containing 36 nested `def`s (and two classes) totalling about 910 lines. It
was 1,608 lines at the previous snapshot and 863 at the one before, and it has
not been split; it has more than doubled in two snapshots. ADR-0006
justifies the *module* being one program shipped as source text. It does not
justify the *function*. Nothing can move to another file while it closes over
`main`'s locals -- so this is the gate on every other worker-side cleanup, and
it is getting worse rather than holding.

**3. A dead decoder for a wire format nothing emits.** Beside
`_to_shm`/`_from_shm` the worker still carries `_deserialize_isolated_objects`,
a decoder for `__isolated_object__`/`__attrs__`/`__path__` frames. Its last
producer was the `self_state` call, and `self_state` now rides the
`state_sync` wire; **no file under `src/` emits any of those three keys**.
Yet the worker runs the decoder over **every input of every call** -- the
same guaranteed-identity walk 0.4.27 deleted for `__comfy_ref__`, still
present for a different tag family, and now with nothing on the other end at
all. The one residual disagreement it encodes is about `Path`: `_to_shm`
emits a bare string, the dead decoder expects `{"__path__": ...}`.

**4. The wire format carries four tag conventions at once** -- a `__type__`
discriminator, sentinel keys that *are* the tag, ride-along flags on a
neighbouring frame, and a registry namespace inside `__shm_custom__`. One key
carries two value types: `__shm_np__` is `True` on the fd path and the block
*name* on the copy path, so a reader must check `"fd" in obj` first or hand
`True` to `SharedMemory(name=...)`.

**5. Two `TensorKeeper` classes, two lifetimes.** The worker's honours
ADR-0032's consumed-ack release. The parent's keeps shm inputs and CUDA
re-export clones from their serialization until `end_call()` empties it,
with the 60 s TTL as the crash fallback. A third, in `tensor_utils.py`, was
deleted on 2026-09-12: it held every call's inputs *and results* and pruned
only on the next keep, so the last call's tensors stayed pinned while
ComfyUI idled, protecting nothing (torch's CUDA IPC has its own
cross-process refcount).

**6. Both registration paths in `wrap.py` are copy-pasted** -- the
root-level `nodes/comfy-env.toml` branch and the per-subdir `_scan_isolation`
closure each call `fetch_metadata` and `build_proxy_class` with their own
copy of the surrounding bookkeeping. The `metadata.py` half of this item is
gone: the V1 and V3 proxy call bodies, which shared 50 identical lines, now
go through one keyword-only `_call_in_worker`.

**7. A fourth NVML probe** lives in `pool.py` (`_device_total_bytes`, a
pynvml-only read of the torch device's total VRAM), re-implementing what
`detection/gpu.py` already does. There is no layering excuse: the
`package-layers` contract makes `isolation -> detection` a legal edge, and
`metadata.py` already uses it.

**8. Ten functions of 150+ lines account for 4,400 lines, 23% of the
codebase** -- `main` (1,957), `register_nodes` (430), `install_workspace`
(430), `_ensure_started` (405), `fetch_metadata` (264), `build_proxy_class`
(204), `_build_v3_proxy_class` (204), `_to_shm_generic` (184),
`_handle_vram_budget` (172) and `maybe_enable_aimdo` (150). Without `main`
the other nine are still 2,443 lines, 13%.

## What is correctly big

- **`_persistent_worker.py`'s non-ComfyUI majority** (~1,320 lines at the last
  hand count, more now). It ships to the far
  interpreter as source text and must parse under the oldest worker Python
  (3.10). A whole program in one *module* is the right shape. A whole program in
  one *function* is not -- see shame #2.
- **`_to_shm_generic` (184).** One walker for both sides, twelve type rungs,
  cycle detection, pluggable tensor strategy. This is the de-duplication that
  already worked; the residual fork above is what's left.
- **`_resolve_wheel_combo` (132).** conda x PyPI x CUDA x torch x CPU-arch with
  a per-arch tier-2 fallback validated against a live index. The input space
  genuinely is this shape.
- **`model_patcher.py` (401), all of it.** Every one of its methods exists
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
  ladder are irreducible. A 430-line install function and a 1,957-line worker
  `main()` are not.
- **The gate that lets this regrow is CI configuration, not architecture.**
  936 lines of dead code accumulated behind a linter that could not see unused
  imports, and four `# noqa` comments that asserted a use which did not exist.
  That gate is closed now. The next 936 will need a different excuse.
