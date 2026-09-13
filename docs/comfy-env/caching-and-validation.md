# comfy-env, caching and validation

*The fingerprint is asked of a warm worker and defaults otherwise. The
validate keeps its signature on the host and runs its body in the worker.*
{: .subtitle }

## ComfyUI background

What `IS_CHANGED` and `VALIDATE_INPUTS` do upstream is the subject of its
own page. **[Read it first](comfyui-caching-validation.md)**.

Both are asked before execution, for every node in the prompt, on the
server's event loop. Forwarding either by starting a pack's process would
cold-spawn every isolated environment the prompt references before a single
node ran, so **neither ever spawns a worker**. They differ in what to do
when the worker is not there: a fingerprint has a safe miss answer
("changed", so the node re-runs) and a validate has none, so the two are
handled differently.

## `VALIDATE_INPUTS`: the signature stays on the host, the body runs in the worker

The host builds a stand-in classmethod whose parameters are every combo
input on the node, then the original validate's own parameter names, each
defaulted to `None`, plus `**kwargs` only if the original had it
(`_combo_input_names`, `_make_named_validate` in `isolation/metadata.py`).

The signature is the point. ComfyUI exempts an input from its built-in
min/max and dropdown checks when the validate names it or takes
`**kwargs`, so the stand-in reproduces the exemptions the author declared.
The combos are added because of [live dropdowns](live-dropdowns.md): a
refresh can put a value into a combo's options that the scan never saw,
and naming the combo is what lets it past the built-in check. A node with
any combo therefore gets a stand-in even without a validate of its own.
Both combo spec shapes are recognised, the legacy list-first entry and the
canonical `("COMBO", {"options": [...]})` that V3 `Combo` inputs become.

The body runs in the worker because it can need the pack's own libraries.
It runs in one of two places:

**At submit, when the worker is warm.** The stand-in records what it was
handed, then asks the worker on the [side lane](worker-lifecycle.md) from
an executor thread. The worker runs the author's validate and answers
accepted, or the author's message, which upstream shows in the submit
dialog as a native `custom_validation_failed`. This path is reject-only:
a cold worker, an unimported pack or a slow lane means "not rejected
here", never "accepted".

**At execution, otherwise.** Upstream calls the stand-in inside the same
`CurrentNodeContext` it later wraps around the node's function, so the
stand-in records its arguments under that key (widget literals, `None` for
linked inputs, `input_types` if asked for), bounded to the four most
recent prompts. When the function is called the proxy ships the record
with it; the worker resolves the real validate (`first_real_override` on
the locked clone for V3, `VALIDATE_INPUTS` for V1), filters the arguments
to its parameters as upstream does, awaits it if `async`, and runs it
immediately before the function. `True` passes; `False` or a string
raises with the string as the message; an `ExecutionBlocker` passes, as
at validation upstream.

Either way the user sees upstream's wording, `Custom validation failed
for node: <your message>`: at execution it is a plain `ValueError` on the
node (the worker stamps `error_kind: "validation"`, the host's registry
maps it), with the worker traceback kept on `__cause__` only.

Two differences from native, both on the cold path only: the rejection
lands when the node runs, after the nodes ahead of it; and a cache-hit
node's validate does not run, since the body runs only when the function
does. The rule for the user: the first, cold run of a workflow may fail
late on a bad value; a warm one rejects at the click.

## `IS_CHANGED` runs in the worker, or answers "changed"

A node that defines `IS_CHANGED` (V1) or `fingerprint_inputs` (V3) gets a
plain `(cls, **kwargs)` classmethod under the same name on its proxy;
ComfyUI never inspects a fingerprint's signature. It walks the same ladder
as live dropdowns (`_forward_fingerprint`, `_handle_fingerprint`):

| Rung | Worker for this env | Answer |
|---|---|---|
| 0 | any input or hidden value is not JSON data (a tensor; a multiselect list or the `PROMPT` dict is fine) | *changed*, nothing is sent |
| 1 | alive, idle or mid-call, pack imported by a real call | the pack's own fingerprint, answered on the side lane |
| 2 | alive but pack not imported, or no side reply within a second | *changed* |
| 3 | dead or never started | *changed* |

"Changed" is `float("nan")`, which ComfyUI treats as "always re-run" and
answers itself when a fingerprint raises or returns something unhashable.
So an isolated node is never staler than its native self; the one cost is
a recompute after a restart or an idle exit. A fingerprint that returns a
non-primitive, raises, or is declared `async` also answers *changed*.

## See also

- [Live dropdowns](live-dropdowns.md), the ladder the fingerprint shares
- [ComfyUI custom nodepack background](comfyui-custom-nodepack-background.md)
- [The process boundary](process-boundary.md), what crosses and when
