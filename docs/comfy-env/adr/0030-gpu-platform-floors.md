# ADR-0030: GPU platform floors and the zero-copy contract

**Status:** accepted (2026-08-14) -- from the NVIDIA-lens external
review. Records three decisions and one demotion.
**Amended 2026-08-24:** decision 2's precondition is met -- the
ownership contract now exists and the mechanism it stands on is
measured. See [The contract, and what met it](#the-contract-and-what-met-it)
at the foot of this record.

## Decision

> **The majority path gets the cheap fix first; zero-copy GPU waits
> for a lifetime contract it can actually keep.** Pinned memory on the
> shm segment is the next GPU investment; pool IPC is demoted to
> experimental until its free/sync protocol exists; every GPU
> capability is probed against a stated floor, never assumed.

### 1. Pinned-memory strategy (the Windows fix nobody had named)

The accepted Windows GPU edge is currently a **triple** copy, not
double: `tensor.cpu()` lands in *pageable* memory, `share_memory_()`
copies again into the shm segment, then the peer pays H2D. Decision:
`cudaHostRegister` the shared-memory mapping in both processes and
copy D2H directly into it -- the DMA engine writes straight into
shared memory, the intermediate CPU copy disappears, and it works on
WDDM today with zero handle-passing. This is the highest-value GPU
change available to the majority platform and it precedes any Win32
handle work. (Win32 pool handles stay gated per
[ADR-0009](0009-platform-strategy.md); note from the review: the
gating is right but the ask is smaller than 0009 implies --
`DuplicateHandle` + NULL-DACL same-user duplication replaces
`SCM_RIGHTS`; the real cost is the per-device probe and a WDDM soak
matrix we have no CI for.)

### 2. Pool IPC: demoted to experimental until the contract exists

The 2026-08 review found the shipped pool-IPC path unsound as a
lifetime protocol: imported pointers are never freed
(`cudaMemPoolImportPointer` requires importer-frees-before-exporter);
exporter-side pinning rides a bounded metadata cache whose eviction
can free memory under a live parent alias; there is no cross-process
sync beyond a full stream sync at export; and the created pool has no
release threshold. (The parent-side accounting patch that these hazards
also touched was **removed entirely in 0.4.22** -- it was experimental,
default-off, and the cause of an `environment -> isolation` import cycle;
the worker-side pool path below stands on its own.) Decisions:

- Pool IPC **stays default-off and is labeled experimental** wherever
  it is surfaced. The enable-time log lines shout EXPERIMENTAL and cite
  ADR-0005/ADR-0010 today; the settings reference must be updated to
  match (it still reads "implemented, untested"). No further plumbing
  investment in the current shape.
- Before any zero-copy GPU path defaults on, a written **cross-process
  ownership contract** must exist: who frees, in what order, with
  what sync primitive (CUDA event IPC is the intended one -- it works
  under `cudaMallocAsync`), verified by the canary battery
  ([ADR-0027](0027-testing-and-verification.md)).
- The intended future is the **pluggable-allocator design**
  ([ADR-0010](0010-wire-protocol-and-transport.md) v2 item 6): worker
  allocations natively in a shareable pool, no accounting lies, no
  patch-chasing torch's allocator. The parent-side meta-path patch that
  chased the allocator was a dead end and was **deleted in 0.4.22**
  rather than carried or offered upstream
  ([ADR-0024](0024-upstream-interface-contract.md) entry 8).

### 3. Floors, probed not assumed

- **Driver/runtime floor**: pool-handle support **must be** probed per
  device via `cudaDevAttrMemoryPoolSupportedHandleTypes` before pool IPC
  leaves experimental -- this probe does **not** exist yet (the current
  code calls `cudaMemPoolCreate` directly and relies on a try/except
  around the handshake); a stated minimum driver goes in the docs the
  same day. The ctypes structs pin their layout
  to the runtime version they were written against (the current
  `cudaMemPoolProps` works on 11.x by zeroed-trailing-bytes accident
  -- comment the invariant, verify on version bumps).
- **Which libcudart answers**: parent-side pool calls resolve against
  the parent torch's runtime, worker-side against the worker's -- they
  differ by design ([ADR-0007](0007-machine-wide-workspace-with-per-env-manifests.md)
  pins families, not builds). Recorded rule: pool operations on a
  memory region are made only by the side that allocated it; the
  other side holds imports. The contract in (2) is what makes that
  rule checkable.
- **Wheel-farm ABI assumption, named** (cuda-wheels ADR series owns
  the mechanics): combo wheels assume *patch-level libtorch ABI
  stability within a torch family* -- practice, not contract. The
  farm's qualification smoke ([ADR-0026](0026-trust-and-supply-chain.md))
  runs against each new patch release of a pinned family so the break
  arrives as a red farm build, not a user segfault.

## Context

External GPU review verdict, adopted: the forward bets (mempool IPC,
pluggable allocator) are correct, but the current pool plumbing bolts
export-pointer semantics onto an ack protocol designed for copies, and
the platform with the majority of users was paying an unexamined extra
copy while the roadmap chased a Linux-only path that is off by
default. This ADR reorders the investment accordingly.

## Consequences

- Windows GPU users get the first real transport improvement
  (pinned-memory D2H) without any new IPC machinery or security
  surface.
- Nobody can flip pool IPC on and ship it by accident: experimental
  labeling plus the contract precondition are now in the record its
  enable path points to.
- Multi-GPU device-identity rules live in
  [ADR-0025](0025-vram-co-management.md); this ADR's floors apply
  per-device when that day comes.

## The contract, and what met it

*Amendment, 2026-08-24.* Decision 2 demoted pool IPC until a written
cross-process ownership contract existed -- "who frees, in what order,
with what sync primitive". It now does, and the mechanism it rests on
has been verified end-to-end on an RTX 3090 (driver 580.126.20, torch
2.8.0+cu128). The full write-up, with the scripts that produced it, is
[Zero-copy CUDA transfer](../zero-copy-ipc.md); `research/pool-ipc/`
in the comfy-env repo holds the code.

The contract:

```
EXPORT -> READY-event -> IMPORT -> WRITE -> DONE-event -> UNIMPORT -> ACK -> RELEASE
```

Three things about it answer this ADR's objections directly.

**Who frees, resolved by inverting the direction.** The unsoundness
recorded above -- a parent left holding an imported pointer it cannot
safely free -- came from the worker exporting results. Instead the
parent allocates the result tensor and the worker writes into it in
place, so the parent's result is an ordinary refcounted
`torch.Tensor` and the importer (the worker) always releases before
the exporter (the parent) does. The requirement stops being a rule to
enforce and becomes a consequence of the layout.

**What sync primitive: interprocess CUDA events**, as this ADR
predicted, and they do work under `cudaMallocAsync` -- now measured
rather than assumed. Two gates: the worker waits on a ready-event
before touching imported memory, and the parent's consumer streams
wait on a write-done event before reading. The ack gates on GPU work
completion, never CPU receipt, because `cudaFreeAsync` returns bytes
to the pool where the next allocation can immediately reissue them.

**Exporter-side pinning is no longer a bounded cache.** The parent
holds an ordinary Python reference for the transfer's lifetime; there
is no metadata cache whose eviction can free memory under a live
alias, and the pool carries an explicit release threshold set by us
rather than inherited.

The remaining gap is not the contract but the mechanism's reach. The
shipped `PoolIPC` rung stays experimental and default-off; what
replaces it is a **parent-side pool swap** (`cuDeviceSetMemPool` to a
`POSIX_FD`-shareable pool before torch imports), which makes ComfyUI's
own tensors exportable without patching torch's allocator -- the
pluggable-allocator ambition of ADR-0010 v2 item 6, reached from the
other end and at a fraction of the cost.

Two measured constraints bind any implementation:

- `cuMemPoolExportPointer` fails until the pool has been exported to a
  shareable handle. Undocumented; export the FD once at startup.
- `cuMemPoolImportPointer` segfaults the importer above **5248 MiB per
  allocation** -- a NULL dereference inside `libcuda`, with no prior
  public report anywhere. The limit is per allocation and not
  cumulative, so multi-tensor payloads of any total size are
  unaffected; a single larger tensor must take the copy path. A size
  guard and its fallback are therefore part of the admission
  arithmetic ([ADR-0034](0034-admission-by-arithmetic.md)), not an
  afterthought.

**Decision 1 is unchanged.** Pinned host memory on the shm segment
remains the floor and still precedes this work everywhere, because it
is the only improvement that needs no handles, no probes and no
platform gating -- and because zero-copy's unique win is *fitting*, not
latency. A copy path needs source and destination resident at once;
that is what puts a 16 GiB tensor out of reach on a 24 GiB card, and no
amount of chunked staging fixes it. Latency was never the argument.

Windows remains unprobed and therefore unchanged: `research/pool-ipc/
wddm_pool_probe.py` decides whether the same architecture works on WDDM
or whether the worker-side VMM arena is required, and until it runs on
real hardware this ADR's platform gating stands exactly as written.
