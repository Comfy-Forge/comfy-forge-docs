# comfy-env, caching and validation

*One is forwarded over a ladder, the other is synthesized from its
signature. What each costs.*
{: .subtitle }

## ComfyUI background

What `IS_CHANGED` and `VALIDATE_INPUTS` do upstream is the
subject of its own page, and none of this makes sense without it.

**[Read it first](comfyui-caching-validation.md)**.

**Neither is forwarded by spawning a worker, and that is deliberate.**

`VALIDATE_INPUTS` runs once per node and `IS_CHANGED` once per node per
prompt, both before execution. Forwarding either by starting the pack's
process would **cold-spawn every isolated environment on the machine before
a single node ran**. On a box with twenty packs that turns every prompt
submission into a multi-minute stall.

The two get different treatment because they fail in different directions.
`IS_CHANGED` has a safe answer when the worker is not there ("changed", so
the node re-runs), so it is forwarded to a worker that already exists and
answered locally otherwise. `VALIDATE_INPUTS` has no safe answer, so the
parent **synthesizes a replacement from the argument names** captured during
the metadata scan. Names, not code: the validate body stays in the worker and
is never called.

## `VALIDATE_INPUTS` becomes `return True`

comfy-env builds a classmethod with *exactly* the original parameter names,
each defaulted to `None`, plus `**kwargs` only if the original had it
(`_make_named_validate` in `isolation/metadata.py`):

```python
exec(f"def _cev_validate(cls, {sig}):\n    return True\n", ns)
```

The signature is the point: it reproduces the exemptions the original
declared. `_named_args` captures both the named parameters and whether the
original had a `**kwargs` catch-all, because ComfyUI reads both: an input is
exempted from the built-in checks if it is named in the argspec *or* the
function takes `**kwargs`. A pack that wrote `(cls, **kwargs)` therefore keeps
the blanket exemption it asked for, and a mixed `(cls, named, **kwargs)` form
keeps both halves. Names that are not valid identifiers are skipped, since
they could not have been exempted this way anyway.

The consequence, stated plainly: **the pack's validation body never runs.** A
node that rejected a bad combination now accepts it, and fails later inside
the worker with a traceback from its own code instead of a red node and a
readable message.

## `IS_CHANGED` runs in the worker, or answers "changed"

When a pack node defines `IS_CHANGED` (V1) or `fingerprint_inputs` (V3), the
proxy carries a fingerprint of the same name. It is a plain `(cls, **kwargs)`
classmethod, because ComfyUI never inspects a fingerprint's signature; it
calls it with every declared input as keyword arguments and hands linked
inputs in as `None`. The proxy walks the same ladder as
[live dropdowns](live-dropdowns.md) (`_forward_fingerprint` in
`isolation/metadata.py`, `_handle_fingerprint` in the worker):

| Rung | Worker for this env | Answer |
|---|---|---|
| 0 | any input or hidden value is not a JSON primitive | *changed*, nothing is sent |
| 1 | alive and idle, lock won within 0.25 s | the pack's own fingerprint, as a primitive |
| 2 | alive but mid-call | *changed* |
| 3 | dead or never started | *changed* |

"Changed" is `float("nan")`, which ComfyUI already treats as "always re-run";
it is also what ComfyUI itself does natively when a fingerprint raises or
returns something unhashable. So the miss answer is not a comfy-env
invention, and a fingerprint can never make an isolated node *staler* than
its native self. The one cost is a recompute after a ComfyUI restart or after
the idle sweep has exited the worker, for nodes that define a fingerprint at
all. Nodes without one never trigger a socket round trip.

Three things are refused rather than guessed: a fingerprint that returns a
non-primitive, one that raises, and one declared `async`. All answer
*changed*.

## Why the two differ

Both rationales are the same until the miss case. A fingerprint that cannot
be computed has a correct conservative answer; a validation that cannot be
run does not. That asymmetry, not the cold-spawn cost, is why one is
forwarded and the other synthesized.

## See also

- [Live dropdowns](live-dropdowns.md) — the ladder the fingerprint shares,
  and the rules for never spawning and never raising
- [ComfyUI custom nodepack background](comfyui-custom-nodepack-background.md)
  — the rest of the node contract
- [The process boundary](process-boundary.md) — what crosses, and when
