# ADR-0009: Platform strategy

**Status:** accepted

## Context

ComfyUI's user base spans Windows (including the Desktop app), Linux
(including headless servers), and macOS (Apple Silicon without CUDA). The
IPC, filesystem, and GPU layers differ enough that a
lowest-common-denominator implementation would forfeit the best mechanism on
every platform (e.g. giving up CUDA IPC because Windows lacks it).

## Decision

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

## Consequences

- Each platform gets its best transport: Linux keeps GPU zero-copy; Windows
  and macOS remain fully functional on the CPU shared-memory path.
- Platform-conditional code paths mean bugs can be platform-specific and CI
  currently exercises none of them (there is no test suite) -- the main
  standing risk of this ADR.
- The ASCII-only rule is unusual but cheap, and explains the `--` and `->`
  typography throughout the codebase and these docs.
