# Containers, CI & air-gapped

*The supported deployment story for immutable environments: everything
installs at image-build time, and a booting container touches nothing.
This page replaces the removed `COMFY_ENV_INSTALL_ISOLATED` /
`COMFY_ENV_ISOLATE` switches as the answer to "how do I run comfy-env
packs in Docker?".*

## The model

**Bake at build, freeze at run.** Run each pack's `install.py` while
building the image; the pixi envs materialize into the workspace root and
ship inside the image. At container start, nothing needs installing:

- **Warm start is zero-network.** `install()` re-entry (e.g. ComfyUI
  Manager updating a pack) checks every env's **fast key** first
  (`install/workspace.py`) -- config bytes, host ABI, GPU presence -- and
  when everything matches and is materialized, the whole run exits before
  torch is even resolved.
- **Server boot is read-only.** `register_nodes()` checks env existence
  plus the `env.stamp.json` ABI -- no pixi, no network, no writes.

```dockerfile
# ---- build stage: connected builder materializes envs once ----
ENV COMFY_ENV_ROOT=/opt/comfy-env
RUN cd $COMFY_PATH/custom_nodes/ComfyUI-MyPack && python install.py

# ---- runtime: freeze ----
RUN chown -R app:app /opt/comfy-env
USER app
ENV COMFY_ENV_ROOT=/opt/comfy-env   # SAME root for the runtime user
# entrypoint: boot ComfyUI directly; do NOT re-run install.py
```

!!! tip "When the build stage fails, read `<workspace>/install.log`"
    Every workspace install tees its full output there (`workspace.py:680`) --
    discovery, the resolved combo, and each `pixi install` with its output. In a
    container the console output is often truncated or interleaved; the log file
    is not. Copy it out of the build stage before the layer is discarded.

## The traps, in order of how often they bite

### 1. The workspace root is `$HOME`-dependent

On Linux the workspace root defaults to **`~/.ce`**
(`environment/cache.py`). Bake as root and it lands in `/root/.ce`; run
the container as uid 1000 and comfy-env looks in `/home/app/.ce`, finds
nothing, and **silently degrades to in-process import** -- nodes vanish
with only an "isolation env not found" log line, because a missing env is
indistinguishable from a never-installed one.

**Fix: set `COMFY_ENV_ROOT` to a fixed path, identically at build and
runtime** (as in the snippet above). This is the single most important
line in a comfy-env Dockerfile.

Also `$HOME`-dependent: `~/.comfy-env/settings.env` and the pinned pixi
binary at `~/.comfy-env/pixi/<version>/` -- a re-run of `install.py`
under a different HOME re-downloads pixi, which breaks air-gapped
builds. Keep HOME stable across build steps, or accept that only the
workspace root is relocatable today.

### 2. GPU presence is part of the change-detection key

The fast key hashes `has_nvidia_gpu()` -- which is satisfied by pixi's
`__cuda` virtual package **or a CUDA-build torch**. Build on a CPU-only
builder with CPU torch, run on a GPU host, and the key flips: the first
`install()` re-entry does a full derivation (network) and possibly a
rebuild.

**Fix: install a CUDA-build torch in the image** (the `+cu*` build), so
`gpu:1` holds at build and runtime and the key is stable. Alternatively,
accept one derivation on first boot.

### 3. `install.hash` is the skip token -- never delete it

Some published images prune `install.hash` files to sequence Docker
layers. That file *is* the two-level skip
([The three seals](seals.md)): deleting it forces a full derivation on
every future `install()` re-entry -- in an air-gapped image, a guaranteed
failure.

### 4. Fallback-combo envs re-derive every run, by design

An env stamped `:fallback` (its exact wheel cell wasn't published at
install time) re-checks the index on every run so it can upgrade itself.
For air-gapped images, verify at build time that no env is on a fallback
combo (grep the install log, or the stamp's `provenance` field) -- or
that env will attempt network access forever.

### 5. Nothing self-heals at runtime

There is no lazy env materialization: if the build stage did not produce an
env, the runtime will not create one. The pack falls back to in-process
import and its nodes misbehave. Build every env in the build stage and verify
before freezing. (`COMFY_ENV_AUTO_INSTALL` used to do this and was removed in
0.4.25 -- setting it now fails the import loudly rather than silently doing
nothing.)

## What about running without isolation?

Not supported. The removed `COMFY_ENV_ISOLATE=0` never composed into a
working mode: pack dependencies live only in the isolated envs (the
[host-environment principle](config.md)), and most packs carry conda-only
dependencies (`mesalib`, CGAL, `bpy`) and host-state-resolved CUDA wheels
that have no pip spelling -- an in-process import cannot satisfy them.
The automatic per-env fallback
([ADR-0008](adr/0008-graceful-degradation-everywhere.md)) still exists
for *degradation* (a missing or ABI-mismatched env falls back to
in-process import rather than blocking boot), but it is an accident
recovery, not a mode. Full rationale:
[ADR-0037](adr/0037-no-non-isolated-paths.md).
