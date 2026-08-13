# ADR-0014: Pack-extensible serializer registry

**Status:** accepted (as-built, 2026-08) -- partially superseded by
[ADR-0015](0015-declared-wire-types.md): the `[serializers].modules`
declaration moved to `[types]` in `comfy-env-root.toml` (path-based
loading under mangled names), the pack-prefixed-tag guidance flipped to
type-identity tags for shared library types, and `OpaquePayload` now
materializes (owns) its frames on receipt instead of holding them
verbatim (comfy-env 0.4.15). The registry mechanism itself
(`register_serializer`, MRO lookup, wire framing) is unchanged.

## Context

Node packs ship domain types the transport cannot know about -- meshes,
point clouds, TRELLIS-style sparse tensors, pack-internal result objects.
Before the registry these took one of two bad paths: the **pickle rung**
(three copies through a `SharedMemory` block, numpy/pickle version skew
across deliberately-different envs, and the security debt named in
[ADR-0010](0010-wire-protocol-and-transport.md) item 8), or a **hardcoded
branch in the generic walker** (`SparseTensor` -- a domain type living
inside the transport layer, flagged in the 0010 review as a defect).
comfy-env needs packs to move their own types efficiently without the
transport learning them one by one.

## Decision

A **per-process serializer registry** (`workers/_ipc_shared.py`), with pack
modules loaded on both sides of the boundary:

- `SerializerRegistry` maps type `__name__` -> `(tag, serialize)` and
  `tag -> deserialize`. Lookup matches the exact class name first, then
  walks the MRO -- registering a base class covers its subclasses.
- Public API:

  ```python
  register_serializer(type_name, serialize, deserialize=None, tag=None)
  ```

  `serialize(obj, recurse) -> JSON-safe payload`. The `recurse` callback
  routes nested values back through the transport, so tensors and arrays
  inside a custom payload take the real shared-memory path: **custom types
  decompose into schema + tensors, never pickle.** `deserialize(payload,
  recurse) -> obj` receives the payload raw and decides which nested parts
  to reconstruct.
- Wire frame: `{"__shm_custom__": <tag>, "payload": ...}` inside the
  ordinary metadata tree ([ADR-0010](0010-wire-protocol-and-transport.md)
  owns the framing around it).
- **Pack declaration** (`comfy-env.toml`):

  ```toml
  [serializers]
  modules = ["my_pack.wire_types"]
  ```

  The listed modules are imported for their registration side effects:
  parent-side at `register_nodes()` (`wrap.py`), worker-side at startup via
  the `COMFY_ENV_SERIALIZER_MODULES` env var (`_persistent_worker.py`).
  `[serializers]` never reaches the generated pixi manifest.
- **`OpaquePayload`**: a side that cannot reconstruct a tag (e.g. the
  parent env lacks the pack's deps, or the module failed to import) holds
  the frame verbatim; re-serializing emits the identical frame. So
  parent-mediated worker-to-worker forwarding works **without the parent
  ever understanding the type**, and module import failures degrade to
  opaque pass-through instead of crashing
  ([ADR-0008](0008-graceful-degradation-everywhere.md) posture).

## Consequences

- The pickle surface shrinks: this registry is the delivery mechanism for
  ADR-0010 item 8 (schema-not-pickle). First planned payloads: trimesh
  (as `{vertices, faces, ...}` tensors, deleting `__shm_trimesh__` and the
  attribute-stripping pickle prep) and `SparseTensor` (out of the generic
  walker, into a 20-line pack serializer).
- Custom payloads inherit the tensor path's performance: a 2 GB mesh moves
  as two shared-memory arrays instead of a 3-copy pickle blob.
- **Trust**: deserializers execute pack-provided code at message-decode
  time. This adds no new boundary -- the same pack already executes
  arbitrary code as its nodes -- but it means the transport's safety story
  remains coupled to [ADR-0011](0011-isolation-before-sandboxing.md)'s
  sandboxing scope, and a future sandbox must include registry modules.
- **Tag namespace is global, last-registration-wins.** Two packs
  registering the same tag silently override each other; packs should
  prefix tags (`geompack.Mesh`, not `Mesh`). A collision check is a
  possible later hardening.
- The registry is per-process with no cross-version negotiation of
  payload schemas; the transport-version handshake planned in ADR-0010
  covers the frame format, while payload compatibility across pack
  versions is the pack's own responsibility (same status as its node
  I/O contract).
