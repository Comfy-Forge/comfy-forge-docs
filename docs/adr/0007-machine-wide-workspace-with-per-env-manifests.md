# ADR-0007: Machine-wide workspace with per-env manifests

**Status:** accepted (supersedes the v0.3.x single-file layout)

## Context

Materialized envs are multi-gigabyte (torch + CUDA + conda stacks). Users
often run several ComfyUI installs (portable, Desktop, git clone) that
declare the same node packs. v0.3.x used one workspace-wide `pixi.toml` with
an `[environments.<name>]` entry per env -- so one malformed env definition
poisoned the single manifest and broke **every** env's scan and install.

## Decision

- **One machine-wide workspace root**: `%LOCALAPPDATA%\Programs\comfy-env` on
  Windows (next to the ComfyUI Desktop install; no admin needed), `~/.ce` on
  Unix; override with `COMFY_ENV_ROOT` (`environment/cache.py`).
- **Env names are global identifiers**: `<plugin>-<subdir>`, `ComfyUI-`
  prefix stripped, lowercased. Identical names from different ComfyUI
  installs resolve to the **same materialized env** (cross-install sharing of
  the multi-GB payload).
- **Per-env manifests** (v0.4 layout): each env owns
  `envs/<name>/pixi.toml` + `pixi.lock` + `.pixi/envs/default/`. A parse
  error in one env cannot poison another; installs run per manifest.
- **No backward compatibility** with the v0.3 layout -- old workspaces are
  invisible to v0.4+ and get re-materialized; a legacy `C:\ce` is detected
  and answered with a one-line reinstall nudge at startup.
- **Torch pin replication**: the host's torch family pin is written verbatim
  into every generated feature so parent and all workers share an identical
  torch family (tensor wire compatibility for
  [ADR-0005](0005-tiered-tensor-serialization.md)).
- **Env stamps**: `write_env_stamp` records Python ABI tag + comfy-env
  version + torch pin; `validate_env_stamp` rejects envs built under a
  different ABI or version, and `_compute_env_hash` skips reinstalls when the
  config is unchanged.

## Consequences

- Disk usage scales with distinct envs, not with ComfyUI installs.
- Deleting one env or breaking one manifest leaves the others untouched.
- The intentional compatibility break means one-time re-materialization for
  v0.3 users (documented, nudged at startup).
- Name-as-identifier means two *different* packs that resolve to the same
  env name would collide; the naming scheme makes this unlikely but it is a
  known tradeoff.
- Host torch upgrades invalidate stamps and trigger env rebuilds -- correct,
  but occasionally surprising.
