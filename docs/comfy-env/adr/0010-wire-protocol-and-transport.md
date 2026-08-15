# ADR-0010: Wire protocol and transport

**Status:** accepted as-built (v1), with an agreed v2 direction (2026-08
adversarial review). This ADR owns the transport layer that ADR-0001 (the
process boundary), ADR-0005 (which serialization strategies exist), and
ADR-0006 (how worker code crosses) each border but none owned.

## Context

Every proxied node execution crosses the parent/worker boundary. The
transport as built:

- **Framing:** 4-byte length prefix + JSON over an AF_UNIX socket (TCP
  loopback on Windows), `SocketTransport` on both sides. JSON is the control
  plane only; bulk bytes travel out-of-band (shared memory, fd passing, CUDA
  handles -- the ADR-0005 ladder).
- **Concurrency model:** synchronous, one call in flight per worker,
  enforced by a per-worker reentrant lock. Interleaved `log` and `callback`
  frames are consumed inline by the request loop.
- **Tensor endpoints:** the GPU and CPU zero-copy paths serialize
  *torch's private multiprocessing reduction* -- `reduce_tensor()`'s tuple
  on one side, `rebuild_cuda_tensor()` / `rebuild_storage_fd|filename` on
  the other.
- **Versioning:** none on the wire. The invariant "parent and worker agree
  on torch's reduction ABI" is enforced *at install time* by the env stamp
  (ABI tag + torch-family pin replication, ADR-0007) -- not by the protocol.

## Decision (v1, as built -- and what we keep)

> **Keep the hand-rolled framing and the synchronous core; fix the
> invariants, not the transport family.** Not gRPC/Cap'n Proto (worker
> envs must stay stdlib-minimal); not the sibling project's asyncio
> engine (event-loop lifecycle scar tissue); a versioned handshake,
> call-id correlation, and one serialization stack on the existing wire.

1. **Keep the hand-rolled framing.** The disqualifier for gRPC/Cap'n Proto
   is the worker-side dependency: ADR-0006's constraint that isolated envs
   need nothing beyond stdlib(+torch) is load-bearing, and a ~100-line
   length-prefixed-JSON framing is debuggable with strace and a hex dump.
   *Steal the discipline of those systems, not the dependency*: a written
   spec, one schema module, conformance tests.
2. **Keep the synchronous core.** The sibling project's asyncio RPC engine
   demonstrably grows event-loop-lifecycle scar tissue ("loop closed,
   retrying with fresh loop") that a synchronous design never has. Canonize
   correctness *invariants*, not the async implementation.
3. **Keep JSON control plane + out-of-band bulk.** Data-structures-first:
   metadata is human-readable; bytes never transit the socket.

## Known defects of v1 (verified in review; drive the v2 items)

- **Unversioned payload over a private ABI.** Parent and worker torches are
  different builds *by design*, yet the tensor endpoints ride torch's
  private reduction tuple with no runtime handshake -- "enforced at install,
  hoped at protocol, no defense in depth." Drift = segfault, not error.
- **`call_id` is decorative.** *(Partially fixed 2026-08: the parent now
  drops stale frames whose `call_id` mismatches instead of consuming
  them; full pending-map correlation -- v2 item 2 -- still pending.)*
  Originally: generated, logged, never matched; the
  response is "the first non-log/non-callback frame." Safe only under the
  single-in-flight lock -- and the aiohttp route path already lets a second
  thread touch a worker, so the latent desync has a real trigger.
- **The serialization stack exists three times** *(largely fixed
  2026-08: the worker's `_to_shm` now delegates to the shared walker in
  the copied `_ipc_shared.py`; the worker's `SocketTransport` gained the
  `MAX_MESSAGE_SIZE` check and send/recv locks in 0.4.18, so the two
  transports are behavior-equivalent -- the only residual fork is the
  side-specific `_from_shm` halves, which is dedup work, not a
  correctness gap.)* Originally: (parent, `_ipc_shared.py`,
  worker). The shared module is copied next to the worker precisely so it
  can import it -- and the worker never does; the parent uses the shared
  walker while the worker re-implements it. Duplication by neglect, not
  necessity (this corrects ADR-0006's "standing tax" framing).
- **Type dispatch by class-name string** (`Tensor`/`Trimesh`/etc.) hardcodes
  domain types into the generic layer; every new type is a synchronized
  multi-file edit.
- **Global mutable deserialization context** (`_active_worker_pool`,
  `_gpu_zero_copy_demoted`) raced when two workers deserialized
  concurrently. *(Fixed 0.4.18: moved to a thread-local `_call_state`;
  the module globals are gone.)*
- **Cross-Python-version pickle** for meshes/arbitrary objects between envs
  that may run different Python versions and native-lib builds.
- **A health-check ping round-trip was paid on every call** *(fixed
  0.4.18: gated behind a 60 s idle window, so warm calls do zero health
  round-trips)*. The per-call overhead figure in the docstrings
  ("~50-100ms") was folklore until 2026-08: a first real measurement (see
  ADR-0001's spawn-vs-persistent table) put the warm per-call floor at
  ~30 ms *including* the then-present ping; a later isolated echo
  measurement put the true floor at 2.4 ms -- still ~8x pyisolate's
  measured 0.31 ms, the gap being redundant tree-walks. A standing
  benchmark harness is still missing.

## Direction (v2 -- agreed by both reviewers)

1. **Versioned ready-handshake**: `{protocol_version, torch.__version__,
   python_version, reduction-ABI hash, capability flags}`; refuse on
   mismatch with a named reason. Converts the install-stamp invariant into
   an enforced wire invariant.
2. **`call_id`-keyed pending map** -- order-independent correlation for all
   frame types; delete the interleave special-casing.
3. **One serialization stack**: the worker imports the copied
   `_ipc_shared.py` (verified stdlib-only, safe under the worker's DLL
   ordering constraints); the third copy is deleted, not shrunk.
4. **Serializer registry** with a ComfyUI adapter layer; retire string-name
   dispatch.
5. **Replace the private torch endpoints** with `__cuda_array_interface__`
   plus raw driver-level IPC/pool handles (the `_PoolPtr` + `as_tensor`
   pattern already in the code proves it): kills the private-ABI hazard and
   enables GPU zero-copy *between different torch versions*. Note dlpack was
   evaluated and rejected as the wire contract -- it is an in-process
   exchange; the cross-process artifact is still an IPC/pool handle.
6. **Memory plane on torch's official extension point**: evaluate
   `torch.cuda.CUDAPluggableAllocator` routing worker allocations through a
   parent-owned shareable pool -- zero-copy becomes structural (offsets, not
   per-tensor exports), the ctypes cudart layer retires, and the pool is the
   cross-process VRAM ledger. This is the concrete memory plane of the
   "tensor daemon" direction.
7. **Windows GPU zero-copy** via `CU_MEM_HANDLE_TYPE_WIN32` shareable pool
   handles (the POSIX-fd constant is a parameter, not an architecture).
8. **Replace cross-version pickle** for structured non-tensor payloads with
   an explicit-field schema (e.g. meshes as `{vertices, faces, ...}` arrays
   through the existing tensor path); conformance tests detect breakage,
   only a schema prevents it.
9. **Heartbeat liveness instead of wall-clock silence timeouts** (the worker
   watchdog thread already exists); kill only on missed heartbeats or user
   cancel; make any remaining timeout per-node configurable.
10. **Benchmark harness + golden-transcript conformance tests** across a
    torch x python matrix: settle the per-call floor, idle VRAM per worker
    (CUDA context tax; also fix the eager context creation in workers),
    Windows tensor throughput. Measurements over vibes.

## Alternatives considered for the transport (rejected, with reasons)

- **gRPC** -- protobuf codegen + HTTP/2 + native wheels in every isolated
  env; violates the minimal-worker-env invariant. Wrong tool.
- **Cap'n Proto** -- same env-pollution problem; zero-copy wire format is
  redundant with the existing out-of-band bulk design.
- **Arrow (pyarrow)** -- rejected as a dependency; the *idea* survives as
  item 8 (explicit schemas for structured payloads; Arrow C Data Interface
  is acceptable if ever needed, the pyarrow wheel is not).
- **dlpack as the wire contract** -- in-process only; see item 5.
- **Adopting the sibling project's asyncio RPC engine** -- its correctness
  properties (pending map, no-pickle transport, registry) are adopted as
  invariants; its event-loop implementation is not.

## Security posture

Deferred by explicit decision -- see
[ADR-0011](0011-isolation-before-sandboxing.md). Item 8 above
(replace cross-version pickle with explicit schemas) doubles as the
transport-hardening prerequisite for that later work.

## Consequences

- Until v2 items 1-2 land, the transport's safety rests on the install-time
  stamp and the single-in-flight lock; treat any new concurrent path to a
  worker (routes, background threads) as a protocol hazard.
- The v2 list is incremental; nothing in it breaks the three-call contract
  or ADR-0006's source-text worker delivery.
- ADR-0005's strategy ladder is unchanged by this ADR; items 5-7 change the
  *endpoints* of strategies 1-3, not the ladder's shape.
