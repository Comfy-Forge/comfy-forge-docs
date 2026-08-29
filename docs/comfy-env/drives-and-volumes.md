# Drives and volumes

*What happens when ComfyUI lives on one drive and the workspace on another --
`D:\ComfyUI` with envs on `C:`, or `/data/ComfyUI` with envs under `~/.ce`.
Short answer: nothing breaks, but it costs, and the obvious fix has a trap.
Findings from a two-platform code audit (2026-08-29).*

## The short answer

comfy-env never creates a link between the workspace and the ComfyUI tree --
the only traffic across that boundary is reads, stats, and plain copies. So
a drive split causes **no hard failure**. What it does is forfeit sharing
(disk and RAM) and pile gigabytes onto the wrong drive. One configuration
is genuinely fatal, on any layout: a workspace volume mounted `noexec` or
read-only -- the env's own python is exec'd and its libraries are dlopen'd
from there, so workers cannot start.

## What lands where

| Artifact | Volume | Notes |
|---|---|---|
| Workspace root (envs, manifests, seals, `install.log`, metadata caches) | `%LOCALAPPDATA%\Programs\comfy-env` / `~/.ce` -- **`COMFY_ENV_ROOT` moves this** | 4-12 GB per env; ABI-stranded copies accumulate by design until [`comfy-env gc`](commands.md#comfy-env-gc) ([ADR-0028](adr/0028-workspace-disk-lifecycle.md)) |
| pixi/rattler/uv package caches | `%LOCALAPPDATA%\rattler\cache` / `~/.cache/rattler` -- **`COMFY_ENV_ROOT` does NOT move this** | owned by pixi; ~42 GB observed; relocatable only via `PIXI_CACHE_DIR` / `XDG_CACHE_HOME`, which comfy-env never sets |
| pixi binary, `settings.env`, `debug.env` | `~/.comfy-env` (`Path.home()`-anchored) | tens of MB; also unaffected by `COMFY_ENV_ROOT` |
| Worker temp files and crash logs | `%TEMP%` / `$TMPDIR` | ~113 KB per live worker; tmpfs `/tmp` is fine (rewritten every spawn) |
| Tensor transport | RAM (memfd / POSIX shm), Linux IPC sockets are abstract-namespace | no volume involved at all |
| ComfyUI, custom_nodes, models, input/output | wherever you put them | comfy-env writes only node-pack payloads and copied assets there, always as plain copies |

## Where the sharing actually happens

Two mechanisms, both keyed on **files being the same inode**:

1. **Disk dedup**: pixi and its embedded uv install by hardlinking from the
   package cache into each env. Verified live: a 430 MB `libtorch_cpu.so`
   with `st_nlink=16` -- one physical copy serving 16 envs. (Exception:
   the handful of files carrying a conda `prefix_placeholder`, libpython
   chief among them, are rewritten per env and legitimately cannot link --
   roughly 32 MB per env, unavoidable on any layout.)
2. **RAM sharing**: processes that mmap the same physical file share its
   read-only pages. Same-env workers always share; different envs with the
   same resolved torch share exactly where the hardlinks above exist.

Host ComfyUI vs workers share **nothing today on any layout** -- the host's
pip-installed torch and the envs' uv-installed torch are different files.
The roadmap's [same-volume placement item](../roadmap.md) (measured on
Windows: ~157 MB of torch shareable per same-build CUDA worker) is about
making that possible; a drive split forecloses it permanently, because
hardlinks cannot cross volumes.

## The `COMFY_ENV_ROOT` trap

Moving the workspace to the big drive (`COMFY_ENV_ROOT=D:\comfy-env` or
`/data/comfy-env`) moves the gigabytes -- and silently breaks the dedup
that works today. The package cache stays in its default location on the
old volume, and **hardlinks cannot cross volumes**: pixi and uv quietly
fall back to copying every file into every env. Disk goes from one shared
torch to one per env plus the cache copy, and cross-env page sharing drops
to zero. Nothing warns; the install just works, bigger and slower.

!!! tip "Move the workspace and the cache as one unit"
    ```
    COMFY_ENV_ROOT=/data/comfy-env
    PIXI_CACHE_DIR=/data/comfy-env-cache
    ```
    Both can live in `~/.comfy-env/settings.env`, which comfy-env loads
    into the environment at import and pixi inherits. Keep both on the
    same large, local, exec-permitted filesystem. ComfyUI and the models
    can stay wherever they are -- nothing links across that boundary.

## Platform notes

**Windows.** The default puts everything on `C:` -- usually the small SSD
-- where Defender's real-time scanning also taxes every env
materialization and each worker's first `import torch`. Very deep
site-packages paths under the ~115-character workspace prefix can approach
`MAX_PATH` on systems without long-path support (no observed failure, but
nothing enables long paths either). Worker IPC is TCP on Windows, so no
filesystem dependency there.

**Linux.** `noexec`/`ro` mounts are the one fatal config (above) -- and it
applies to `~/.comfy-env` too, since the pixi binary lives there. An NFS
workspace mostly *works* (the seals are content-hashed, not mtime-based;
metadata cache writes are atomic) but every worker start streams hundreds
of MB of libraries over the wire, and coarse NFS mtimes widen the
metadata-cache staleness window.

**macOS.** Case-insensitive APFS is a non-issue for the workspace: env
names are already forced to lowercase `[a-z0-9-]`. The filesystem socket
dir and rattler cache follow `$TMPDIR` and `~/Library/Caches` conventions.

## See also

- [System footprint](system-footprint.md) -- the complete inventory of
  what comfy-env puts on your machine.
- [ADR-0007](adr/0007-machine-wide-workspace-with-per-env-manifests.md) --
  why the workspace is machine-wide in the first place.
- [ADR-0028](adr/0028-workspace-disk-lifecycle.md) -- why disk only grows
  until `gc`.
