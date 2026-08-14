# ADR-0008: Graceful degradation everywhere

**Status:** accepted

## Decision

> **Every failure path ends in "ComfyUI still boots".** Not fail-fast
> (comfy-env sits in the startup path of end-user machines, where a hard
> failure reads as "ComfyUI is broken"); every subsystem has an explicit
> fallback, and the terminal fallback is ComfyUI booting with isolation
> off -- which means the failing pack's nodes register **only if the
> host env can import them**. On a host honoring the host-env principle
> (comfy-env and nothing else installed), it usually can't: the pack's
> nodes are then absent with a logged "Failed to import", and workflows
> using them show missing nodes. "Still boots" protects the
> *application*, not the failing pack.

Every subsystem has an explicit fallback, and the terminal fallback is always
**ComfyUI still boots**:

| Failure | Fallback |
|---------|----------|
| Isolation env not materialized | In-process import attempt; on a bare host this fails per-module and those nodes are skipped (logged, never fatal) |
| Auto-install fails (`isolation/auto_install.py`) | Returns None; caller falls back to in-process import |
| CUDA IPC unavailable or broken | CPU shared memory (down the [ADR-0005](0005-tiered-tensor-serialization.md) ladder) |
| cuda-wheels Pages index unreachable | Retry with real User-Agent, then GitHub Releases API ([ADR-0004](0004-prebuilt-cuda-wheel-index.md)) |
| GPU detection: NVML missing | torch -> nvidia-smi -> sysfs, in order (`detection/gpu.py`) |
| curses unavailable (`comfy-env settings`) | Plain-text settings UI |
| Worker crash mid-session | Worker pool auto-restarts it (`isolation/wrap.py`) |
| No GPU at install time | CPU-only torch build is pinned instead of failing |

Feature flags follow the same philosophy: risky capabilities default off
(`COMFY_ENV_AUTO_INSTALL`, `COMFY_ENV_POOL_IPC`) and everything is
overridable per env var, per user file (`~/.comfy-env/settings.env`), or per
node (`[settings]` in the TOML).

## Context

comfy-env sits in ComfyUI's startup path on end-user machines: hobbyist
Windows boxes behind corporate proxies, headless Linux servers, Macs without
NVIDIA GPUs. Any hard failure in comfy-env is indistinguishable, to the user,
from "ComfyUI is broken". The blast radius of an exception at import or
prestartup time is the entire application.

## Consequences

- A broken or half-installed comfy-env degrades to "isolation off", not to a
  broken ComfyUI.
- Failures can hide: a node silently running in-process, or tensors silently
  taking the slow CPU path, look like success. Counterweights: the startup
  banner prints per-env `[OK]` / `[MISSING -- run install.py]`, and
  `comfy-env doctor` / the `debug` categories expose what path is active.
- Fallback chains are more code to maintain than fail-fast would be; this is
  accepted as the cost of shipping to non-developers.
- **The in-process fallback decays as the host-env principle succeeds.**
  It was written when packs still installed their deps into the host env,
  where an in-process import genuinely worked. On a bare host
  (requirements.txt = comfy-env only) the bottom rung is empty by
  construction: fallback means the pack's nodes are missing, not running
  unisolated. The real safety net there is `COMFY_ENV_AUTO_INSTALL`
  (materialize the env on first load), which stays default-off because a
  synchronous pixi install can block startup for minutes -- a deliberate
  trade of resilience against startup latency that should be revisited if
  missing-nodes-after-fallback becomes the common failure users hit.
