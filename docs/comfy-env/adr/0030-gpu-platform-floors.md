# ADR-0030: GPU platform floors and the zero-copy contract

**Status:** accepted (2026-08-14) -- from the NVIDIA-lens external
review. Records three decisions and one demotion.

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
release threshold. (A separate defect -- the parent-side accounting
patch using the `find_module` API dead since Python 3.12 -- was fixed
in 0.4.18: the hook is now `find_spec`/`exec_module`. The demotion
below stands on the lifetime hazards, not that fixed bug.) Decisions:

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
  patch-chasing torch's allocator. The current parent-side meta-path
  patch is a dead end and is not offered upstream
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
