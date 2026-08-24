# ADR-0008: Graceful degradation everywhere

**Status:** accepted; amended 2026-08 -- "everywhere" now means the
**environment plane** only. The data plane fails loudly by decision
([ADR-0015](0015-declared-wire-types.md)): an unserializable value
raises a named error instead of leaking, a corrupted CPU-tier canary
refuses the worker outright ([ADR-0005](0005-tiered-tensor-serialization.md)).
The live doctrine in one sentence: **degrade on availability
(missing env, no GPU, no CUDA IPC -- a correct slower path exists),
fail loudly on correctness (undeliverable payloads, corrupted
transport, invalid contracts -- no correct fallback exists).**

## Decision

> **Every *availability* failure path ends in "ComfyUI still boots".**
> Not fail-fast
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
| CUDA IPC unavailable or broken | CPU shared memory (down the [ADR-0005](0005-tiered-tensor-serialization.md) ladder) |
| cuda-wheels Pages index unreachable | Retry with real User-Agent, then GitHub Releases API ([ADR-0004](0004-prebuilt-cuda-wheel-index.md)) |
| GPU detection: NVML missing | torch -> nvidia-smi -> sysfs, in order (`detection/gpu.py`) |
| curses unavailable (`comfy-env settings`) | Plain-text settings UI |
| Worker crash mid-session | Worker pool auto-restarts it (`isolation/wrap.py`) |
| No GPU at install time | CPU-only torch build is pinned instead of failing |

Feature flags followed the same philosophy: risky capabilities default off
and overridable per env var or per user file (`~/.comfy-env/settings.env`).
Only `COMFY_ENV_POOL_IPC` remains -- and it is documented as known-unsound,
so the pattern now has exactly one instance and no longer carries a general
claim. Per-node `[settings]` was removed in 0.4.25.

## Context

comfy-env sits in ComfyUI's startup path on end-user machines: hobbyist
Windows boxes behind corporate proxies, headless Linux servers, Macs without
NVIDIA GPUs. Any hard failure in comfy-env is indistinguishable, to the user,
from "ComfyUI is broken". The blast radius of an exception at import or
prestartup time is the entire application.

## Consequences

- A broken or half-installed comfy-env degrades to "isolation off", not to a
  broken ComfyUI.

    !!! note "Amended 2026-08: the fallback is per-env and automatic -- no flag"
        The global off-switches (`COMFY_ENV_ISOLATE`,
        `COMFY_ENV_INSTALL_ISOLATED`) were removed in 0.4.25 (ADR-0017,
        pre-1.0): nobody set them, their off-states never composed into a
        working mode, and `isolate=0` collaterally disabled the macOS
        libomp fix. The terminal fallback is the **per-env automatic**
        in-process import (missing env or stamp refusal), which is
        evidence-triggered rather than flag-triggered;
        and a failed in-process import now fails loudly (full traceback; all-sources-
        failed raises so ComfyUI marks the pack IMPORT FAILED) instead of
        silently registering zero nodes.
- Failures can hide: a node silently running in-process, or tensors silently
  taking the slow CPU path, look like success. Counterweights: the startup
  banner prints per-env `[OK]` / `[MISSING -- run install.py]`, and
  `comfy-env doctor` / the `debug` categories expose what path is active.
- *2026-08 amendment:* this hiding cost is accepted only where the
  fallback is **correct** (slower, but right). Where no correct fallback
  exists -- a payload that cannot be serialized, a transport tier that
  corrupts bytes -- degradation would convert a loud bug into silent
  data damage, so those paths raise named errors instead (see Status).
  The original "worst case is extra copies, not a crash" claim holds for
  the availability plane only.
- Fallback chains are more code to maintain than fail-fast would be; this is
  accepted as the cost of shipping to non-developers.
- **The in-process fallback decays as the host-env principle succeeds.**
  It was written when packs still installed their deps into the host env,
  where an in-process import genuinely worked. On a bare host
  (requirements.txt = comfy-env only) the bottom rung is empty by
  construction: fallback means the pack's nodes are missing, not running
  unisolated.
- *2026-08 amendment (0.4.25): there is no safety net below that rung.*
  `COMFY_ENV_AUTO_INSTALL` was previously named here as the answer --
  materialize the env on first load. It was removed, because it was a
  **second builder** that no seal could hold in agreement with
  `install_workspace`: it skipped the macOS libomp dedupe and uv's
  python-preference pinning, and because those leave the manifest identity
  unchanged, every later `comfy-env install` **skipped the resulting env as
  up to date**. A recovery hatch that silently produces a permanently-wrong
  env is not resilience. The honest position is now: a missing env means
  missing nodes until `install()` is run again, and the mitigations are
  diagnostic rather than automatic -- the startup banner's
  `[MISSING -- run install.py]`, and the log line at the bind site naming
  the exact command. **This weakens the headline claim above**: "every
  availability failure ends in ComfyUI still boots" protects the
  *application*, not the failing pack, and on a bare host that distinction
  is the whole outcome.
