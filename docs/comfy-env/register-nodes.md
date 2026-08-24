# `register_nodes()`

```python
# __init__.py
from comfy_env import register_nodes
NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS = register_nodes()
```

The **runtime** entry point. ComfyUI imports the pack's `__init__.py` at
startup and reads `NODE_CLASS_MAPPINGS`; `register_nodes()` produces those
mappings -- importing normal nodes in-process and synthesizing **proxy
classes** for isolated ones, so ComfyUI cannot tell the difference.

Source: `register_nodes()` in `src/comfy_env/isolation/wrap.py`. Signature:
`register_nodes(nodes_package="nodes") -> (mappings, display_names)` -- the
only knob is the name of the nodes subpackage, and the caller's package is
again inferred from the stack.

## What it does

```mermaid
flowchart TD
    subgraph parent["ComfyUI main process (host env)"]
        reg["register_nodes()<br/>isolation/wrap.py"]
        meta["Metadata scan<br/>isolation/metadata.py<br/>(short-lived subprocess in the env)"]
        proxy["Proxy node classes<br/>(synthesized in the parent)"]
        reg -->|"1. scan the env"| meta
        meta -->|"2. build proxies"| proxy
    end

    subgraph worker["Persistent worker (isolated pixi env)"]
        node["Real node code + resident models"]
    end

    proxy -. "each proxy forwards its FUNCTION over the socket" .-> node
```

The parent holds **only proxies** -- it never imports node code. Proxies
are built once at `register_nodes()` by the short-lived metadata scan;
the persistent worker is a separate, long-lived process (one per env,
auto-restarted on crash).

Step by step:

1. **Reap stale workers** left over from a previous crashed run.
2. **Discover isolation dirs**: `<nodes_package>/comfy-env.toml` and `<nodes_package>/<subdir>/` -- the two
   shapes the runtime binder can bind. **Deliberately not a recursive glob**:
   a config anywhere else could be scanned but never bound. Each needs *and* a materialized env in the workspace. Per-env
   `[env_vars]` from the TOML are collected, plus `COMFYUI_BASE` (and
   `COMFYUI_USER_DIR` on the Desktop app) so workers can find ComfyUI.
3. **Isolation dirs** get a **metadata scan**: a short-lived subprocess runs
   *inside the isolation env*, imports the node modules there, and pickles
   out their metadata -- `INPUT_TYPES`, `RETURN_TYPES`, `FUNCTION`,
   v3 schemas, dynamic option lists (model dropdowns). The parent never
   imports node code
   ([ADR-0001](adr/0001-process-isolation-via-persistent-subprocess-workers.md)).
   Scans are cached keyed by content hash, so unchanged packs skip the
   subprocess on later launches.
4. **Proxy classes are synthesized** from that metadata with the standard
   node shape. When ComfyUI executes one, the call is forwarded to a
   **persistent worker** for that env -- spawned on first use, kept alive
   across executions (models stay resident), auto-restarted on crash, torn
   down at exit. Tensors cross the boundary via the
   [serialization ladder](process-boundary.md#tensor-serialization-ladder).
5. **Everything else** -- directories without a config, or with a config but
   no materialized env -- is imported normally in-process, and their
   mappings merged into the same return value.

!!! warning "A proxy's `INPUT_TYPES` is a snapshot, and core's is not"
    Vanilla ComfyUI re-evaluates `INPUT_TYPES()` on every `/object_info`
    request, which is why file-listing dropdowns stay live. A proxy replays a
    payload captured once at scan time and cached on disk, so a combo built
    from a directory listing never refreshes -- newly uploaded files do not
    appear, even after a restart. A node can opt one combo back into live
    refresh with `comfy_env_dynamic_dir`; see
    [Dynamic combos](dynamic-combos.md).

## Degradation and flags

- **Missing env** -> in-process import, with a log line naming the exact
  command that builds it; ComfyUI still boots
  ([ADR-0008](adr/0008-graceful-degradation-everywhere.md)). Envs are built
  **only** by [`install()`](install.md) -- `register_nodes()` never
  materializes one. A lazy path behind `COMFY_ENV_AUTO_INSTALL` existed until
  0.4.25 and was removed: it was a second builder that no seal could keep in
  agreement with the first.
- Worker-resident GPU models are bridged into ComfyUI's VRAM manager via
  `SubprocessModelPatcher`, and workers call back into the parent for
  progress reporting and VRAM budget negotiation.
