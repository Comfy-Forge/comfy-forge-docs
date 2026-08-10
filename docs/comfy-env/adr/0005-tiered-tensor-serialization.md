# ADR-0005: Tiered tensor serialization

**Status:** accepted (strategy 2 in progress)

## Context

Node inputs and outputs -- often multi-gigabyte image/video tensors and
meshes -- must cross the process boundary between ComfyUI and workers
([ADR-0001](0001-process-isolation-via-persistent-subprocess-workers.md)).
Naive pickling over the socket would copy every tensor twice and destroy
throughput. The optimal mechanism differs by data type, device, and platform,
and some mechanisms fail at runtime for environmental reasons.

## Decision

Serialize via a **priority ladder** -- try the best strategy the data and
platform allow, fall through otherwise. JSON messages over the socket carry
metadata only; bulk bytes travel through shared memory or GPU handles.

1. **CUDA IPC** (`CudaIPC`): `reduce_tensor()` / `rebuild_cuda_tensor()`,
   zero-copy GPU. Linux only.
2. **Pool IPC** (`PoolIPC`, in progress): shareable CUDA memory pool +
   `cudaMemPoolExportPointer`, pool FD exchanged over the socket via
   `SCM_RIGHTS`. Zero-copy GPU.
3. **Torch shared memory** (`TensorRef`): `file_system` sharing strategy
   (/dev/shm). Zero-copy CPU.
4. **NumPy**: converted to a torch tensor, then strategy 3.
5. **Trimesh / pickle**: pickled into a `SharedMemory` block.
6. **Primitives**: inline in the JSON message.

Supporting machinery: `TensorKeeper` (`isolation/tensor_utils.py`) holds
references for a retention window so shared tensors are not GC'd while the
peer still maps them; `release_tensor()` reclaims mappings with
`madvise(MADV_DONTNEED)`; an IPC-handle cache enables zero-copy
worker A -> parent -> worker B forwarding.

### Why strategy 2 exists

ComfyUI sets `PYTORCH_CUDA_ALLOC_CONF=backend:cudaMallocAsync`, which
propagates to workers and **breaks legacy CUDA IPC**: `reduce_tensor()`
raises `cudaMallocAsync does not yet support shareIpcHandle`. *Historical
defect, fixed:* the `_probe_cuda_ipc()` checks originally tested only
`Event` + allocation and mis-reported IPC as usable under `cudaMallocAsync`;
both probes now exercise `reduce_tensor()` and fail closed (verified
independently three times in the 2026-08 reviews). Pool-based IPC
(worker-side shareable pools) is the zero-copy path under `cudaMallocAsync`
-- implemented end-to-end but untested and default-off; its parent-side
half, behind `COMFY_ENV_PATCH_SHAREABLE_POOL`, DOES patch
`comfy.model_management`'s memory accounting, so the original
"no monkey-patching ComfyUI" scope applies to the worker->parent direction
only (see [setup_env()](../setup-env.md)).

### Runtime verification: the canary handshake

An honesty clause first: strategies 1-3 ride **torch's private,
unversioned multiprocessing reduction protocol** (`reduce_tensor()` /
`reduce_storage()` positional tuples). That is a pragmatic sin -- torch
makes no cross-version compatibility promise for it, and reimplementing
CUDA IPC handle exchange ourselves is not reasonable. Having committed the
sin, we compensate the only defensible way: **probe reality instead of
predicting it**.

At worker creation, the parent round-trips a canary tensor through the
**production serialization path** (`_to_shm`/`_from_shm` via a dedicated
`echo` request -- deliberately NOT a parallel test serializer, which would
validate nothing) and compares bytes:

- **CPU tier fails** -> hard error; that is broken IPC, not version skew,
  and the worker is refused.
- **GPU zero-copy tier fails or corrupts** -> that worker is *demoted* to
  CPU transport, loudly, and keeps working.
- **Parent/worker torch families differ** (e.g. a fallback-combo env under
  a newer host torch) -> a warning, plus whatever the canaries prove.
  Pickle-based tiers (5-6) are genuinely cross-version safe and are never
  gated.

There is deliberately **no hand-maintained compatibility matrix** ("torch
2.8 talks to 2.10 but not..."). Version-pair tables rot; the probe is the
single source of truth and never needs updating when torch changes -- when
torch breaks the protocol, the probe is what reports it. Opt-out:
`COMFY_ENV_TRANSPORT_PROBE=0`.

## Consequences

- Common cases (large CPU tensors, Linux GPU tensors) are zero-copy.
- Cross-version transport is verified empirically per worker at startup,
  not assumed from version numbers; mismatched-but-compatible pairs keep
  zero-copy, broken pairs degrade loudly.
- Every strategy needs an implementation **on both sides** of the boundary,
  which forces the deliberate code duplication described in
  [ADR-0006](0006-worker-crosses-the-boundary-as-source-text.md).
- Failures degrade down the ladder instead of erroring
  ([ADR-0008](0008-graceful-degradation-everywhere.md)) -- worst case is
  extra copies, not a crash; the cost is that misconfigurations can hide as
  silent slowdowns.
- Windows never gets GPU zero-copy (strategies 1-2 are POSIX-bound); it uses
  strategy 3 and below.
