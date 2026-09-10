# comfy-env, caching and validation

*Neither can be forwarded to a worker. What the parent synthesizes
instead, and exactly what that costs.*
{: .subtitle }

## ComfyUI background

What `IS_CHANGED` and `VALIDATE_INPUTS` do upstream is the
subject of its own page, and none of this makes sense without it.

**[Read it first](comfyui-caching-validation.md)**.

**Neither is forwarded, and that is deliberate.**

`VALIDATE_INPUTS` runs once per node and `IS_CHANGED` once per node per
prompt, both before execution. Forwarding either would **cold-spawn every
isolated environment on the machine before a single node ran**
(`isolation/metadata.py:430-434`). On a box with twenty packs that turns
every prompt submission into a multi-minute stall.

So the parent **synthesizes replacements from the argument names**, which are
captured during the metadata scan. Argument names, not code — the bodies stay
in the worker and are never called.

## `VALIDATE_INPUTS` becomes `return True`

comfy-env builds a classmethod with *exactly* the original parameter names
(`isolation/metadata.py:966-984`):

```python
exec(f"def _cev_validate(cls, {params}):\n    return True\n", ns)
```

The signature is the point: it reproduces the exemptions the original
declared — with one gap. `_named_args` reads `inspect.getfullargspec(fn).args`
and never `.varkw`, so a `**kwargs` form is captured as `[]` and **no validate
is attached at all**. Upstream would have set `validate_has_kwargs=True` and
exempted every input; instead ComfyUI re-imposes every built-in check, and a
workflow that submits fine natively is rejected once the pack is isolated. The
mixed `(cls, named, **kwargs)` form — shipped twice in ComfyUI's own nodes —
loses the `**kwargs` half of its exemption the same way. A `**kwargs` form is deliberately **not** used, because that would
exempt every input on the node including the numeric clamps users rely on.
Names that are not valid identifiers are skipped, since they could not have
been exempted this way anyway.

The consequence, stated plainly: **the pack's validation body never runs.** A
node that rejected a bad combination now accepts it, and fails later inside
the worker with a traceback from its own code instead of a red node and a
readable message.

## `IS_CHANGED` is usually absent entirely

The replacement is narrower still. A parent-side mtime fingerprint is
attached **only** when the node has dynamic combos *and* its fingerprint
arguments are a subset of the marked inputs
(`isolation/metadata.py:1017-1052`).

Outside that case an isolated node carries **no `IS_CHANGED` at all**, so
upstream's `not has_is_changed` branch applies and the node is treated as
never changing.

The `float("nan")` idiom therefore stops working: the node caches on its
inputs and does not re-run. The user reports *"it's stuck on the old
result"*, restarting ComfyUI clears it, and that symptom is the one least
likely to point an author at caching.

## Neither decision has an ADR

Both rationales are sound and both live only in code comments. They are the
load-bearing explanation for two of comfy-env's most surprising behaviours,
and a reader looking for *why* currently has to find the right docstring.

## See also

- [Dynamic combos](dynamic-combos.md) — the one case where a fingerprint
  *is* synthesized, and why
- [ComfyUI custom nodepack background](comfyui-custom-nodepack-background.md)
  — the rest of the node contract
- [The process boundary](process-boundary.md) — what crosses, and when
