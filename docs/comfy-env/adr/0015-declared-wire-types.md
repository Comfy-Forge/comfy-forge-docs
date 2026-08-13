# ADR-0015: Declared wire types

**Status:** accepted (2026-08-13; implementation landing with comfy-env 0.4.16)

## Decision

> **A pack names its wire types in one place, comfy-env owns their
> lifetime everywhere.** Declaration is a flat `[types]` table in
> `comfy-env-root.toml`; code exists only for the rare type that needs
> decomposing; everything else is automatic -- and a failure to
> serialize is a loud, named error, never a silent leak.

Three parts.

### 1. The declaration: `[types]` in `comfy-env-root.toml`

```toml
[types]
TRIMESH    = "custom"     # two functions in serialization.py
SKELETON   = "builtin"    # dict of arrays -- automatic transport
INTRINSICS = "builtin"
EXTRINSICS = "builtin"
```

- One line per socket type the pack puts on the wire. Two values only:
  `"builtin"` (the transport's automatic handling -- tensors, arrays,
  primitives, and containers of them) or `"custom"` (this pack ships
  serialize/deserialize functions).
- The table is *documentation with teeth*: humans read it as the pack's
  type inventory; comfy-env validates it at scan time (a `"custom"`
  socket with no matching registration is a startup error naming both
  sides); comfy-test can diff it against what actually crossed the wire.
- A pack with no custom types may omit the table -- or list its sockets
  as `"builtin"` purely as documentation. Declaring nothing changes
  nothing: defaults apply either way.

### 2. The code: `serialization.py`, loaded by path on both sides

`"custom"` entries resolve to `<pack>/serialization.py`, which calls
`register_serializer()` ([ADR-0014](0014-pack-extensible-serializer-registry.md))
for each custom type. Rules:

- **Self-contained**: top-level imports limited to stdlib / numpy /
  comfy_env; heavy libraries imported *inside* the functions. This lets
  every process read the file -- including a bare host that has none of
  the pack's dependencies.
- **Loaded by file path** under a mangled per-pack module name
  (`_comfy_env_serializers.<pack>.serialization`), parent side and
  worker side. Plain-module-name loading is removed (no backcompat):
  it keyed every pack's module into the parent's shared `sys.modules`,
  where two packs shipping the same filename silently collided --
  first import won, the second pack's serializers never registered.
  The path-based loader kills the collision class and frees packs to
  use the obvious short filename.
- **Capability-conditional registration**: register `deserialize` only
  where the library imports. A side without it registers
  `deserialize=None`, and the transport holds a *materialized*
  OpaquePayload (comfy-env 0.4.15: receiver-owned bytes, safe across
  worker restarts). The same file therefore does the right thing on
  every machine: a bare host holds receipts; a host where some native
  pack installed the library reconstructs real objects, and
  native-node interop works with zero configuration.
- Supersedes `[serializers].modules` in `nodes/comfy-env.toml`
  (removed, no backcompat).

### 3. The conventions: tags, names, and loud failure

- **Wire tags follow type identity, not pack identity.** A shared
  library type tags as the library's name for it (`trimesh.Trimesh`);
  only pack-private types take a pack prefix (`trellis2.ShapeSLAT`).
  Two packs that both declare `trimesh.Trimesh` interoperate by
  construction -- each side rebuilds with its *own* registered
  functions, and no pack ever executes another pack's code. This
  partially supersedes ADR-0014's pack-prefixed-tag guidance, which
  prevented collisions by also preventing interop.
- **Payload ground rules**: arrays, JSON primitives, and bytes -- never
  nested pickles. Raw arrays are what make version-skewed envs
  (py3.11/trimesh 7.8 <-> py3.13/trimesh 8.0) interoperate: the library
  version never touches the wire. Bytes cover types with their own
  canonical exchange form (OCC B-rep).
- **Socket names cannot be centrally guaranteed** -- they are strings
  any pack can mint, and connectable *requires* collidable (ComfyUI
  links sockets by string equality). Policy instead: private types are
  pack-prefixed (collision impossible; the ecosystem already does this
  unprompted -- see census below); shared types are unprefixed and
  their payload contract is written down in the shared-socket-types
  reference page, with comfy-test able to detect shape divergence
  mechanically. Convention plus detection, not prevention.
- **Serialization failure is an error, not a leak.** The transport's
  last-resort rung previously did `except Exception: return obj`,
  leaking the raw object into the JSON message and crashing two layers
  away with `Object of type X is not JSON serializable`. It now raises
  a named error carrying the type, the underlying cause, and the two
  fixes: register a serializer, or install the missing dependency in
  the producing env. (Demonstrated live: `pickle.dumps` on a textured
  `Trimesh` imports PIL *at serialize time*; on a PIL-less env the
  bytes are never produced, so no receiving-side machinery can save
  the call. Graceful degradation ([ADR-0008](0008-graceful-degradation-everywhere.md))
  ends where the payload stops being deliverable at all.)

## Context

The design was driven by measurement and a census, not speculation:

- **Transport cost is 1-2% of workflow wall-clock.** Measured through
  the production `echo` path: 5.6 ms per edge for a 20k-vertex mesh
  (1.8 MB), ~30 ms at 17.6 MB, ~135 ms at 88 MB; call floor 2.4 ms.
  `alpha_wrap.json`'s 17 boundary crossings cost ~50 ms against seconds
  of CGAL compute. Performance therefore justifies almost nothing;
  correctness and ergonomics decide everything below.
- **A 53-pack census** (all of the author's packs, 139 custom socket
  types, every producer's return statement verified) found: every
  `*_MODEL`/`*_CONFIG`/`*_PIPELINE` type but one is already a JSON-safe
  config dict with a worker-side model cache; the big payloads are
  dicts of tensors that ride the builtin path; exactly one widely
  shared live-object type exists (`trimesh.Trimesh`, 15 packs); CAD
  packs already pass B-rep file paths; private types are already
  pack-prefixed. The declaration language is small because the
  ecosystem's real needs are small.
- **The host-env principle is non-negotiable**: the ComfyUI host env
  installs comfy-env and nothing else. comfy-env 0.4.15 made the
  parent a pure switchboard (materialize-on-receipt for both registry
  and pickle-rung values, consumed-ack replacing TTL-as-correctness),
  which this ADR's conditional registration relies on.
- **One real semantic collision exists today**: `INTRINSICS` means
  `{"K": tensor}` in one pack and a bare `[N,3,3]` tensor in five
  others. It predates and is orthogonal to serialization -- a
  vocabulary problem, handled by the reference page + detection.

## Rejected alternatives

- **`pass_by = "reference"` (worker-resident handles).** Twice
  promoted, twice demoted by evidence: the census showed zero live
  model objects crossing (config-dict pattern already universal), and
  the two suspected mutable-state types (`SAM3_VIDEO_STATE`,
  `MEMORY_BANK`) are plain data. References would add generation
  tracking, cascading cache invalidation on worker crash, and
  author-facing lifetime semantics -- for a measured 1-2% win.
  Revisit only with a concrete pack that needs it.
- **Published wire-format implementations** (comfy-env shipping
  canonical trimesh/gaussian serializers). Puts domain knowledge back
  into the transport forever -- the exact creep the builtin trimesh
  branch deletion removed. The tag convention + reference page gets
  the interop without the maintenance.
- **A separate `types.yaml` / `serialization.yaml`.** Same data,
  second file, second config language. The root toml already exists
  and the parent already reads it.
- **A richer declaration** (`python:` type paths, format integers,
  per-type options). The socket name is for humans and tests; the
  transport matches Python classes via `register_serializer`. Fields
  that duplicate what the code declares are drift waiting to happen.
- **Per-env registry scoping in the parent** (the earlier plan for tag
  collisions). Mostly obsoleted: identity tags make same-type
  registrations cooperate instead of collide, and path-based loading
  removes the module-name collision. Kept on the task list only for
  the residual same-tag-different-payload case, which now surfaces as
  a loud deserialize error naming both packs.

## Consequences

- A pack author's entire serialization surface is: one toml table,
  usually nothing else; at most one small `serialization.py` for a
  hero type. Everything about lifetime, holding, forwarding, and
  bare-host behavior is comfy-env's problem, invisibly.
- Cross-pack, cross-env, cross-version interop for shared types works
  through the tag convention with no shared code and no central
  registry -- at the price that shared-name payload contracts live in
  documentation and tests rather than in an enforced schema.
- The bare host runs any pack, registered or not (pickle-rung values
  are held as owned bytes) -- but an *unregistered* type with
  serialize-time optional deps (the PIL trap) now fails loudly in the
  producing env instead of leaking raw objects; the error text points
  at the registry.
- `[serializers].modules` users must move to `[types]` in the root
  toml (one-line change; no backcompat shim, per house rules).
