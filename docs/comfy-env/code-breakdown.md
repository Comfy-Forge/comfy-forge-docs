# Code breakdown -- named and shamed

Where the lines actually go. **13,349 lines of Python across 38 files**
under `src/comfy_env/` (raw `wc -l`, blanks and comments included -- the
same basis as the round "~12k" the docs used to quote; it has since grown
~11%, almost all of it this-August correctness/security work with very
little deleted in return).

Snapshot at **v0.4.20 (2026-08-15)**. This page is a photograph and it
*will* drift; regenerate with:

```
find src/comfy_env -name '*.py' | xargs wc -l | sort -rn
```

## By subsystem

| Subsystem | Lines | % |
|---|--:|--:|
| Transport / worker IPC | 4,687 | 35% |
| Environment build / install / wheels | 3,867 | 29% |
| Node registration / proxy / ComfyUI glue | 2,847 | 21% |
| Config / CLI / misc | 1,392 | 10% |
| Hardware detection | 556 | 4% |
| **Total** | **13,349** | 100% |

Two subsystems -- the transport and the env builder -- are **64% of the
project**. That is the honest shape of comfy-env: a serialization stack
and a manifest compiler, with a ComfyUI adapter bolted on.

## Transport / worker IPC -- 4,674 lines (the biggest sink)

| File | Lines | What it is |
|---|--:|---|
| `isolation/workers/_persistent_worker.py` | 1,752 | The entire worker program, shipped to the far env as source text ([ADR-0006](adr/0006-worker-crosses-the-boundary-as-source-text.md)). Main loop, transport, faulthandler/watchdog, the by-reference object cache, the `print`/logger hijack. The single largest file, and it earns the shame: it is a whole program in one module. |
| `isolation/workers/subprocess.py` | 1,088 | Parent-side `SubprocessWorker`: spawn, authkey handshake, health, call/echo, consumed-ack, the canary + device-identity checks. |
| `isolation/workers/_ipc_parent.py` | 804 | Parent transport internals: `SocketTransport`, tensor strategies, `_from_shm`. |
| `isolation/workers/_ipc_shared.py` | 761 | The shared serialization core both sides import (the `_to_shm` walker, registry, `OpaquePayload`, and -- since 0.4.20 -- the CUDA-IPC forwarding cache, the one comfy_env-import-free leaf). |
| `isolation/tensor_utils.py` | 186 | `TensorKeeper`, madvise reclaim. |
| `isolation/workers/base.py` | 82 | `Worker` ABC + `WorkerError`. |
| `isolation/workers/__init__.py` | 14 | Re-exports. |

**The shame here is structural duplication by design.** `_ipc_parent.py`
and the worker each carry a `SocketTransport` and a `_from_shm`
([ADR-0006](adr/0006-worker-crosses-the-boundary-as-source-text.md)
ships the worker as source text, so both sides need their own copy).
The walker was de-duplicated into `_ipc_shared.py`; the transport class
and `_from_shm` halves are the residual fork
([ADR-0010](adr/0010-wire-protocol-and-transport.md) v2 item 3). Call it
~600-800 lines that exist twice.

## Environment build / install / wheels -- 3,867 lines

| File | Lines | What it is |
|---|--:|---|
| `install/workspace.py` | 926 | Workspace materialization: discover configs, resolve torch pin, pick wheel combo, hash for change detection, `pixi install` per env, stamp. |
| `packages/toml_generator.py` | 870 | The manifest compiler: `requirements.txt` + each `comfy-env.toml` -> per-env `pixi.toml` ([ADR-0013](adr/0013-env-file-passthrough-contract.md)). |
| `environment/cache.py` | 604 | Env identity, ABI tags, workspace layout, the Windows LOCALAPPDATA guard. |
| `packages/cuda_wheels.py` | 389 | CUDA wheel index resolution ([ADR-0004](adr/0004-prebuilt-cuda-wheel-index.md)). |
| `environment/setup.py` | 88 | `setup_env()`: faulthandler, libomp dedupe, `base_directory` fill-in. (Shrank from 214 in 0.4.22 when the parent-side shareable-pool hook was deleted.) |
| `packages/node_packs.py` | 188 | `[node_packs]` peer-pack install ([ADR-0016](adr/0016-node-pack-dependencies.md)). |
| `install/helpers.py` | 164 | Install-time helpers. |
| `install/plugin.py` | 145 | Plugin half + the sibling-pin warning ([ADR-0022](adr/0022-comfy-env-placement-in-host-env.md)). |
| `pixi.py` | 111 | Pinned, sha256-verified pixi-binary provisioning. A top-level **leaf** (0.4.21) -- moved out of `packages/` so `detection` can import the `PIXI` path without a cycle. |
| `install/__init__.py` | 101 | `install()` entry. |
| `environment/libomp.py` | 71 | macOS libomp dedupe. |
| `packages/__init__.py` / `environment/__init__.py` / `install/verify.py` | 36 / 30 / 18 | Small. |

This is the moat ([ADR-0022](adr/0022-comfy-env-placement-in-host-env.md)):
the part that survives even if ComfyUI ships its own isolation. The two
1,000-ish-line files (`workspace.py`, `toml_generator.py`) are the
compiler; they are large because the input space (conda + PyPI + CUDA
combos x platforms) genuinely is.

## Node registration / proxy / ComfyUI glue -- 2,847 lines

| File | Lines | What it is |
|---|--:|---|
| `isolation/metadata.py` | 1,114 | The scan subprocess + proxy synthesis ([ADR-0023](adr/0023-metadata-scan-and-proxy-synthesis.md)) -- the subsystem most exposed to ComfyUI schema churn (V1/V3 duality, DynamicCombo, hidden inputs). |
| `isolation/wrap.py` | 546 | `register_nodes()` orchestration -- was 1,112 until 0.4.20, when the worker pool and env builder were extracted (see below); now it reads like the one thing it is. |
| `isolation/pool.py` | 496 | The worker pool (0.4.20): lifecycle, restart+generations, VRAM/progress callbacks, route proxying, the `_STALE_PATCHERS` invariant ([ADR-0019](adr/0019-worker-lifecycle.md)). Extracted from `wrap.py` to break the `wrap`↔`metadata` cycle. |
| `isolation/model_patcher.py` | 213 | `SubprocessModelPatcher` -- resident models obey ComfyUI's VRAM manager. |
| `isolation/subenv.py` | 125 | Launch-env construction (0.4.20): platform PATH/libomp/activation for the worker subprocess. A stdlib-only leaf, extracted from `wrap.py`. |
| `isolation/__init__.py` | 40 | Re-exports. |

**The shame here is churn exposure, not size** -- and 0.4.20 fixed the
one size-and-tangle problem: `wrap.py` was a 1,112-line file doing three
jobs (registration + worker pool + env construction) that were
*mutually circular* with `metadata.py`. Extracting `pool.py` and
`subenv.py` made the import graph acyclic (now CI-enforced by
`lint-imports`) and left `wrap.py` as pure orchestration. What remains is
the monkey-patch surface ([ADR-0024](adr/0024-upstream-interface-contract.md)):
every ComfyUI internal comfy-env reaches into lives in `wrap.py`,
`pool.py`, `metadata.py`, and `model_patcher.py` -- the lines the
three-hook upstream RFC would let comfy-env *delete*.

## Config / CLI / misc -- 1,392 lines

| File | Lines | What it is |
|---|--:|---|
| `cli.py` | 694 | The `comfy-env` CLI (doctor, gc, generate, settings...). Large for a CLI; the biggest single non-core file. |
| `__init__.py` | 199 | Package surface + the three-call contract re-exports. |
| `config/__init__.py` | 195 | The TOML config layer ([ADR-0003](adr/0003-two-config-files-with-two-roles.md), [ADR-0015](adr/0015-declared-wire-types.md)). |
| `lint.py` | 130 | Accelerator-rule lint. |
| `settings.py` | 113 | The env-var control plane. |
| `debug.py` | 61 | Debug categories. |

## Hardware detection -- 556 lines

| File | Lines | What it is |
|---|--:|---|
| `detection/gpu.py` | 256 | NVML -> nvidia-smi -> sysfs fallback chain. |
| `detection/cuda.py` | 126 | CUDA version probing. |
| `detection/__init__.py` | 101 | Platform helpers + the (os, machine) -> pixi platform table. |
| `detection/backend.py` | 73 | Backend selection. |

The smallest subsystem, and the one that best matches its job size.

## What the shape says

- **Delete-shaped work exists.** The transport duplication (~600-800
  doubled lines) and the monkey-patch surface (2,786 lines the upstream
  RFC targets) are the two places where the honest win is *fewer* lines,
  not more -- the recurring note from the 2026-08 reviews.
- **The compiler and transport are not bloat**, they are the problem
  domain: a conda x PyPI x CUDA combo compiler and a
  best-strategy-per-platform serialization ladder. 64% of the code doing
  the two genuinely hard things is a defensible split.
- **`_persistent_worker.py` at 1,752 is the largest file, and it stays
  that way on purpose.** It is a whole program in one module because it
  ships to the far interpreter as source text ([ADR-0006](adr/0006-worker-crosses-the-boundary-as-source-text.md))
  and must stay parseable by the oldest worker-env Python (3.9); the
  2026-08 layering review was explicit that splitting it would trade a
  cohesive program for cross-module imports the read-as-text delivery
  can't satisfy. The extraction discipline that fixed `wrap.py` in 0.4.20
  belongs on the *parent* side, where import order actually runs.
