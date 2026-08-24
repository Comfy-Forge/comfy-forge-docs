# Custom wire types (`[types]` + `serialization.py`)

Nodes in ComfyUI exchange objects along edges. A socket type is
[just a string](comfyui-background.md#data-types-what-a-socket-type-actually-is),
and vanilla ComfyUI never looks inside the object — the same instance is handed
from one node to the next, in one process.

comfy-env has to move that object **across a process boundary**, and a string
does not say how to move bytes. For the built-in types it already knows:
`IMAGE` and `MASK` are tensors, `LATENT` is a dict of them. For `TRIMESH`,
`POINTCLOUD` or `SKELETON` — names a pack invented — it does not.

## What happens without a serializer

The object still crosses. It falls to the transport's last rung and is
**pickled into shared memory** (`_ipc_shared.py`, the fallback after every
tensor path). That works, and for a small object it is fine. The costs:

- **The whole object graph is serialized and rebuilt**, then copied into shm
  and back out — four passes over the data, none of them zero-copy.
- **Both sides need the class importable.** Pickle stores a module path. If the
  receiving env lacks the library, or has a version where the name moved, the
  value degrades to an opaque receipt (see below) instead of a real object.
- **Version skew is a coin flip.** trimesh 7.x pickling into a trimesh 8.x env
  is a compatibility question you did not intend to ask.

If pickling fails outright, the transport raises a `TypeError` naming the type
and the cause. It never silently drops the value.

Declaring your wire types ([ADR-0015](adr/0015-declared-wire-types.md),
mechanism in [ADR-0014](adr/0014-pack-extensible-serializer-registry.md)) lets
your pack teach the transport its own types, decomposing them into
**schema + arrays** so the bulk rides the shared-memory tensor path and no
pickle is involved.

!!! note "`[types]` does not route anything"
    Routing is decided by the **serializer registry**, looked up by Python type
    at wire time. `[types]` is a *declaration*: comfy-env reads it once at
    startup and, for every socket marked `"custom"`, refuses to start the pack
    unless `serialization.py` exists, imports, and registers something. A
    socket marked `"builtin"` changes no behaviour at all.

    So declaring a type does not make it fast — **registering a serializer
    does**. The declaration is what stops you shipping a pack whose serializer
    file quietly went missing.

Worked example: [ComfyUI-GeometryPack](https://github.com/PozzettiAndrea/ComfyUI-GeometryPack)
moves `trimesh.Trimesh` (the type behind its `TRIMESH` sockets) as
shared-memory arrays.

## The recipe

**1. Declare your sockets** in the pack root `comfy-env-root.toml`:

```toml
[types]
TRIMESH    = "custom"     # serialize/deserialize code in ./serialization.py
SKELETON   = "builtin"    # dict of arrays -- automatic transport
INTRINSICS = "builtin"
```

`"builtin"` entries are documentation with teeth (comfy-test can diff
declared vs observed); `"custom"` entries require step 2. A pack whose
types are all dicts/arrays/tensors needs **no** `[types]` table at all.
Typos fail at parse time; a `"custom"` socket with no matching
registration is a loud startup error.

**2. `serialization.py` at your pack root** (that exact name -- it is
loaded by *file path* under a per-pack mangled module name, so every
pack can use it without collisions). Top-level imports must be
stdlib/numpy/comfy_env only; heavy libraries are imported **inside**
the functions, so every process -- including a bare host with none of
your deps -- can read the file:

```python
try:    # parent process (comfy-env installed)
    from comfy_env.isolation.workers._ipc_shared import register_serializer
except ImportError:  # worker process (standalone copied module)
    from _ipc_shared import register_serializer

def _serialize_trimesh(mesh, recurse):
    payload = {
        "vertices": recurse(mesh.vertices),   # shared-memory arrays
        "faces": recurse(mesh.faces),
    }
    visual = getattr(mesh, "visual", None)
    if type(visual).__name__ == "TextureVisuals":
        uv = getattr(visual, "uv", None)
        if uv is not None and len(uv):
            payload["uv"] = recurse(uv)
    return payload

def _deserialize_trimesh(payload, recurse):
    import trimesh
    mesh = trimesh.Trimesh(
        vertices=recurse(payload["vertices"]),
        faces=recurse(payload["faces"]),
        process=False,                        # exact round-trip, no merging
    )
    if payload.get("uv") is not None:
        from trimesh.visual import TextureVisuals
        mesh.visual = TextureVisuals(uv=recurse(payload["uv"]))
    return mesh

# Register deserialize only where the library exists: a side without it
# holds the value as a materialized OpaquePayload receipt instead.
try:
    import trimesh  # noqa: F401
    _DESER = _deserialize_trimesh
except ImportError:
    _DESER = None

register_serializer(
    "Trimesh", _serialize_trimesh, _DESER,
    tag="trimesh.Trimesh",   # type identity -- see tag rules below
)
```

`serialize(obj, recurse)` returns a JSON-safe dict; anything passed
through `recurse` re-enters the transport, so arrays and tensors take
the shared-memory path. `deserialize(payload, recurse)` gets the
payload raw and calls `recurse` on the parts it reconstructs.
Registration matches by class name, then by MRO -- registering a base
class covers its subclasses.

That's it. comfy-env loads the file parent-side at `register_nodes()`
and worker-side at startup (via `COMFY_ENV_SERIALIZER_FILES`).

## Tag rules (ADR-0015)

- **Shared library types tag by type identity**: `trimesh.Trimesh`,
  not `geompack.Trimesh`. Two packs that both declare
  `trimesh.Trimesh` interoperate by construction -- each side rebuilds
  with its *own* registered functions; nobody executes another pack's
  code.
- **Pack-private types take a pack prefix** (`trellis2.ShapeSLAT`),
  where collision is impossible by construction.
- Payload ground rules: arrays, JSON primitives, and bytes -- never
  nested pickles. Raw arrays are what make version-skewed envs
  (py3.11/trimesh 7.x <-> py3.13/trimesh 8.x) interoperate: the
  library version never touches the wire.

## What happens when a side can't reconstruct your type

Nothing breaks. That side holds the value as a **materialized
`OpaquePayload`** -- every frame is copied into receiver-owned memory
on receipt, so the receipt survives worker restarts and TTL expiry,
and re-serializing emits fresh frames for the next hop. The bare
ComfyUI host (which installs only comfy-env, per the host-env
principle) forwards your objects between workers **without ever
understanding them**. If some *other* pack installs your library into
the host, the conditional registration above picks it up and the host
reconstructs real objects instead -- native-node interop with zero
configuration.

## Practical rules (learned the hard way)

- **Only `recurse` long-lived arrays.** Pass `mesh.vertices` directly --
  do **not** wrap in `np.asarray(...)` or otherwise create temporaries.
  The transport's dedup map is keyed by `id()`; a temporary that gets
  garbage-collected mid-walk can hand its id to your next array, which
  then receives the *wrong frame* (observed in the wild: faces
  deserialized as vertices). Accessors that synthesize arrays per call
  (e.g. trimesh's `vertex_colors`) are unsafe to recurse for the same
  reason.
- **Unserializable values raise loudly.** If `recurse` cannot encode an
  object, the transport raises a `TypeError` naming the type and the
  underlying cause (since 0.4.16; it previously leaked the raw object
  into the JSON message and crashed two layers away). Wrap
  optional-fidelity parts in try/except if you'd rather drop them than
  fail the call.
- **Never serialize objects with back-references to your bulk data.**
  A trimesh `visual` holds a reference to its mesh -- recursing it whole
  would re-serialize the entire geometry. Decompose by field instead.
- **Degrade on fidelity, not on geometry**: materials and metadata are
  try/except candidates (a missing dependency costs fidelity); the
  core arrays are not.

The full production module, with all of the above applied:
[`serialization.py`](https://github.com/PozzettiAndrea/ComfyUI-GeometryPack/blob/dev/serialization.py).
