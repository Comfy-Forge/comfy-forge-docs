# Custom wire types (serializer registry)

Your nodes exchange domain objects -- meshes, point clouds, skeletons --
that comfy-env's transport does not know. By default an unknown type
crosses the worker boundary via **pickle**: three copies, fragile across
envs with different numpy/library versions, and slow for bulk data. The
serializer registry ([ADR-0014](adr/0014-pack-extensible-serializer-registry.md))
lets your pack teach the transport its own types, decomposing them into
**schema + arrays** so the bulk rides the shared-memory tensor path.

Worked example: [ComfyUI-GeometryPack](https://github.com/PozzettiAndrea/ComfyUI-GeometryPack)
moves `trimesh.Trimesh` (the type behind its 305 `TRIMESH` sockets) as
shared-memory arrays.

## The recipe

**1. A wire-types module at your pack root** with a unique name
(`geometrypack_wire_types.py` -- *not* `nodes/wire_types.py`: the module is
imported by name from your pack root, and every pack has a `nodes`
package). It must import the registry on both sides of the boundary:

```python
try:    # parent process (comfy-env installed)
    from comfy_env.isolation.workers._ipc_shared import register_serializer
except ImportError:  # worker process (standalone copied module)
    from _ipc_shared import register_serializer
```

**2. A serialize/deserialize pair.** `serialize(obj, recurse)` returns a
JSON-safe dict; anything you pass through `recurse` re-enters the
transport -- arrays and tensors take the shared-memory path.
`deserialize(payload, recurse)` gets the payload raw and calls `recurse`
on the parts it wants reconstructed:

```python
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

register_serializer(
    "Trimesh", _serialize_trimesh, _deserialize_trimesh,
    tag="geompack.Trimesh",   # ALWAYS prefix your tags (global namespace)
)
```

Registration matches by class name, then by MRO -- registering a base
class covers its subclasses.

**3. Declare it** in your env's `comfy-env.toml`:

```toml
[serializers]
modules = ["geometrypack_wire_types"]
```

comfy-env imports the module parent-side at `register_nodes()` and
worker-side at startup. Done -- your type now crosses the boundary as
arrays.

## What happens when a side can't import your module

Nothing breaks. That side handles your frames as `OpaquePayload`: the
value passes through verbatim and re-serializes byte-identical, so the
parent can hand your objects between workers **without ever understanding
them**. Only a side that actually needs to *use* the object requires your
module (and its deps) importable.

## Practical rules (learned the hard way)

- **Only `recurse` long-lived arrays.** Pass `mesh.vertices` directly --
  do **not** wrap in `np.asarray(...)` or otherwise create temporaries.
  The transport's dedup map is keyed by `id()`; a temporary that gets
  garbage-collected mid-walk can hand its id to your next array, which
  then receives the *wrong frame* (observed in the wild: faces
  deserialized as vertices). Accessors that synthesize arrays per call
  (e.g. trimesh's `vertex_colors`) are unsafe to recurse for the same
  reason.
- **Verify fallback frames before embedding them.** If `recurse` cannot
  encode an object it currently returns the raw object instead of
  raising; embed it and the control message stops being JSON-safe, with
  the error surfacing far from the cause. Check
  `isinstance(frame, (dict, list, str, int, float, bool, type(None)))`
  before keeping optional parts (see the material handling in the real
  module).
- **Never serialize objects with back-references to your bulk data.**
  A trimesh `visual` holds a reference to its mesh -- recursing it whole
  would pickle the entire geometry again. Decompose by field instead.
- **Prefix your tags** (`geompack.Trimesh`, not `Trimesh`): the tag
  namespace is global and last-registration-wins.
- **Degrade, don't crash**: wrap optional-fidelity parts (materials,
  metadata) in try/except so a missing dependency costs fidelity, not the
  call.

The full production module, with all of the above applied:
[`geometrypack_wire_types.py`](https://github.com/PozzettiAndrea/ComfyUI-GeometryPack/blob/dev/geometrypack_wire_types.py).
