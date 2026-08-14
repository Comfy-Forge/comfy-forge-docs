# ADR-0009: Platform strategy

**Status:** accepted

## Decision

> **Target each platform's best mechanism behind common interfaces.** Not
> lowest-common-denominator (that would forfeit Linux CUDA IPC to appease
> Windows); dozens of platform branches, each earning its keep with a
> documented reason.

Target each platform's best available mechanism behind common interfaces
(~49 platform branches across the tree), rather than restricting to the
intersection:

**Windows**

- TCP loopback sockets where AF_UNIX is unavailable (`_has_af_unix()` probe).
- Workspace at `%LOCALAPPDATA%\Programs\comfy-env` -- next to the ComfyUI
  Desktop install, creatable without admin. Guard against SYSTEM/service
  shells resolving LOCALAPPDATA to the systemprofile
  (`environment/cache.py:_windows_local_appdata`).
- `_rmtree` via `robocopy /MIR` from an empty directory -- defeats long-path
  and read-only deletion failures that break `shutil.rmtree`.
- `pixi.exe`; DLL/PATH setup in `_build_isolation_env_win32`.
- **ASCII-only source policy**, enforced by a pre-commit hook, because
  cp1252 consoles corrupt non-ASCII output.

**macOS**

- `dedupe_libomp` (`environment/libomp.py`): symlink redundant bundled
  `libomp.dylib` copies to torch's canonical one -- multiple loaded copies
  corrupt OMP runtime state and SIGSEGV inside native filters.
- Host torch's macOS deployment minimum is probed and pinned into generated
  envs; `osx-64` / `osx-arm64` pixi platforms.

**Linux**

- The full zero-copy stack: /dev/shm shared memory, memfd, `SCM_RIGHTS` FD
  passing, CUDA IPC (Linux-only, per
  [ADR-0005](0005-tiered-tensor-serialization.md)); glibc pin in generated
  features.

### Platform support table (what each platform actually gets)

| Capability | Linux | Windows | macOS |
|---|---|---|---|
| Process isolation, persistent workers | yes | yes | yes |
| CPU tensor transport | zero-copy (torch shm) | zero-copy (torch shm) | zero-copy (torch shm) |
| GPU tensor transport | zero-copy (CUDA IPC; Pool IPC under `cudaMallocAsync`, default-off) | **CPU round-trip: GPU -> CPU -> shm -> CPU -> GPU on every CUDA edge** | n/a (no CUDA) |
| Socket transport | AF_UNIX | AF_UNIX where available, else TCP loopback | AF_UNIX |
| FD passing (`SCM_RIGHTS`) | yes | no | yes (unused for GPU) |

Stated loudly because Windows is the majority platform: **Windows tensor
edges pay a double copy today.** Measured context: transport is 1-2% of
real workflow wall-clock even so ([ADR-0015](0015-declared-wire-types.md)
context), which is why native Win32 zero-copy (`CU_MEM_HANDLE_TYPE_WIN32`
pool handles -- security descriptors, handle inheritance, allocator
interplay; substantially more than a constant swap) stays on the roadmap
**gated on a profiled workload where the copy demonstrably matters**,
rather than being built on principle.

### WSL2: ruled out as an isolation primitive

Considered and rejected for the transport reason, not the UX one:
comfy-env's parent is ComfyUI itself, and Windows users run ComfyUI
natively. Workers inside WSL2 would put a **VM boundary** through the
transport -- no shared `/dev/shm` between an NT process and a WSL2
process, no `SCM_RIGHTS`, and no CUDA IPC across the guest/host driver
split (GPU-PV in the guest is a separate driver stack; a
`cudaIpcMemHandle` minted there means nothing to a native Windows
torch). Every tensor edge would cross a virtio-class boundary --
strictly worse than the current CPU double-copy. "Run all of ComfyUI
inside WSL2" remains a valid *user* choice (it is simply the Linux
column above), but it is a deployment recommendation, not an
architecture.

## Context

ComfyUI's user base spans Windows (including the Desktop app), Linux
(including headless servers), and macOS (Apple Silicon without CUDA). The
IPC, filesystem, and GPU layers differ enough that a
lowest-common-denominator implementation would forfeit the best mechanism on
every platform (e.g. giving up CUDA IPC because Windows lacks it).

## Consequences

- Each platform gets its best transport: Linux keeps GPU zero-copy; Windows
  and macOS remain fully functional on the CPU shared-memory path.
- Platform-conditional code paths mean bugs can be platform-specific.
  *Updated 2026-08:* a real test suite now exists (`tests/`, ~23 files)
  and CI runs a 3-OS (Linux/Windows/macOS) x 2-Python matrix, so the
  cross-platform branches are exercised on every push. The residual risk
  narrows to what hosted CI structurally cannot cover: GPU transport
  tiers (verified per-machine by the ADR-0005 canary instead) and
  worker-env interpreters older than the host matrix.
- The ASCII-only rule is unusual but cheap, and explains the `--` and `->`
  typography throughout the codebase and these docs.
