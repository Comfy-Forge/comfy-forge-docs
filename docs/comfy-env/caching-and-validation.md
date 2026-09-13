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
prompt, both before execution. Both only visit the nodes in the submitted
prompt, so forwarding either by starting the pack's process would
**cold-spawn every isolated environment that prompt references before a
single node ran**. On a workflow that spans a handful of packs that turns
every submission into a multi-minute stall.

The two get different treatment because they fail in different directions.
`IS_CHANGED` has a safe answer when the worker is not there ("changed", so
the node re-runs), so it is forwarded to a worker that already exists and
answered locally otherwise. `VALIDATE_INPUTS` has no safe answer, so the
parent **synthesizes a replacement from the argument names** captured during
the metadata scan. Names, not code: the validate body stays in the worker and
is never called.

## `VALIDATE_INPUTS`: the signature stays on the host, the body runs in the worker

comfy-env builds a classmethod whose parameter list is **every combo input
on the node, followed by the original validate's parameter names** not
already in that set — each defaulted to `None` — plus `**kwargs` only if the
original had it (`_combo_input_names` and `_make_named_validate` in
`isolation/metadata.py`). Its body does two things: when the author wrote
a validate, it **records** the arguments it was handed; and it returns
`True`.

The signature is the point: it reproduces the exemptions the original
declared, and adds one set on top. The combos come first because of
[live dropdowns](live-dropdowns.md): the refresh can put a value into a
combo's option list that the scan-time snapshot never saw, and ComfyUI's
built-in check rejects any combo value that is not in the list it holds
unless the input's name is in the validate argspec. Naming every combo is
what lets a freshly uploaded file get past that check and reach the node.
It also means a node with **no** `VALIDATE_INPUTS` at all still gets a
synthesized one if it has any combo; a node with neither gets none. The
combo detection recognises both spec shapes — an entry whose first element
is a list, and the canonical `("COMBO", {"options": [...]})` that every V3
`Combo` input becomes — so a V3 node is exempted for its combos too, not
only for the names its own validate declared.

`_named_args` captures both the named parameters and whether the
original had a `**kwargs` catch-all, because ComfyUI reads both: an input is
exempted from the built-in checks if it is named in the argspec *or* the
function takes `**kwargs`. A pack that wrote `(cls, **kwargs)` therefore keeps
the blanket exemption it asked for, and a mixed `(cls, named, **kwargs)` form
keeps both halves. Names that are not valid identifiers are skipped, since
they could not have been exempted this way anyway.

### Where the body runs

Two places, and which one depends on whether the node's worker exists:

**At submit, when the worker is warm.** The stand-in is `async def` when
the author wrote a body (upstream awaits a coroutine validate and reads the
same parameter names off it), and after recording what it was handed it
asks the worker on the [side lane](worker-lifecycle.md) from an executor
thread. The worker runs the author's real validate against that view and
answers accepted, or the author's sentence, which is returned to upstream
and shown in the submit dialog as a native `custom_validation_failed`. Once
the worker for a node exists, a wrong widget value is rejected at the
click, as natively. This path is reject-only: it never spawns, never
blocks the event loop, and every miss (cold worker, pack not yet imported
by a real call, lane busy or slow) is "not rejected here", not "accepted".

**At execution, always, unless the submit path already answered.**

Upstream calls the stand-in at submit, inside the same executing context
(`CurrentNodeContext`, keyed by prompt id and node id) it will later wrap
around the node's function. The stand-in records what it received under
that key — the widget literals, `None` for every linked input, the
`input_types` dict if the author asked for it — in a store bounded to the
four most recent prompts, because a prompt rejected on some other node
never executes.

When the node's function is called, the proxy looks the record up under the
same context and ships it with the call. The worker resolves the author's
real validate (`first_real_override` on the locked class clone for V3,
`VALIDATE_INPUTS` for V1), filters the arguments down to that function's
own parameters exactly as upstream does (the stand-in's list is a superset,
because of the combos), awaits it if it is `async`, and runs it
**immediately before the function**. `True` passes. `False` or a string
raises, and the string is the node's error message. Anything else — an
`ExecutionBlocker` — passes, as it does at validation upstream.

The one visible difference from native: the rejection lands on the node at
execution rather than at submit, so nodes ahead of it in the graph run
first. Everything the author's body sees is what it would have seen
natively; a linked input is `None` in both places. A second, narrower
difference follows from the first: native ComfyUI validates a node even
when the executor will then serve it from cache, whereas here the body
runs only when the function does, so a cache-hit node's validate does not
run. It matters only for a validate whose verdict depends on something
outside the inputs (a file that has since vanished) on a node whose inputs
have not changed.

Why this order and not a round trip at submit: at submit the worker may not
exist (first prompt after launch), or may not have imported the pack yet,
and `validate_prompt` runs on the HTTP server's event loop. An answer that
depends on whether a process happens to be warm is an answer that changes
from one click to the next; running the body where the worker already is
makes validation deterministic and costs nothing at submit. (The side lane does not change this: it lets a *warm* worker answer at
submit, but a validate has no safe miss answer for a *cold* one, so the
execute-time run stays the guarantee. The rule this gives the user: the
first, cold run of a workflow may fail late on a bad value; a warm one
rejects at the click.)

What the user sees when the body rejects: the node fails with a plain
`ValueError` worded exactly as upstream words a submit-time rejection,
`Custom validation failed for node: <your message>`, and no worker
traceback on the node (it stays on the exception's `__cause__` for
debugging). The worker stamps the error frame `error_kind: "validation"` and
the host's translation registry maps that to `ValueError`, so nothing named
`comfy_env` reaches the frontend.

## `IS_CHANGED` runs in the worker, or answers "changed"

When a pack node defines `IS_CHANGED` (V1) or `fingerprint_inputs` (V3), the
proxy carries a fingerprint under the name its own shape needs:
`fingerprint_inputs` on a V3 proxy, `IS_CHANGED` on a V1 proxy. A V3 node
that falls back to the V1 proxy therefore carries `IS_CHANGED`, but the
worker is still asked for `fingerprint_inputs`, because that is what the real
class defines. It is a plain `(cls, **kwargs)`
classmethod, because ComfyUI never inspects a fingerprint's signature; it
calls it with every declared input as keyword arguments and hands linked
inputs in as `None`. The proxy walks the same ladder as
[live dropdowns](live-dropdowns.md) (`_forward_fingerprint` in
`isolation/metadata.py`, `_handle_fingerprint` in the worker):

| Rung | Worker for this env | Answer |
|---|---|---|
| 0 | any input or hidden value is not JSON data — a primitive, or a list or dict built only from primitives (a multiselect list and the `PROMPT` dict pass; a tensor does not) | *changed*, nothing is sent |
| 1 | alive, idle or mid-call, and the pack module imported by a real call | the pack's own fingerprint, as a primitive, answered on the side lane |
| 2 | alive but the module not yet imported, or no side reply within a second | *changed* |
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

Both are asked before the node runs, and neither can spawn a worker to
answer. A fingerprint that cannot be computed has a correct conservative
answer, *changed*, so it is asked of a warm worker and defaults otherwise.
A validation has no conservative answer — accepting is wrong, rejecting is
wrong — so instead of being asked early and sometimes, it is asked late and
always, at the one moment the worker is certain to exist.

## See also

- [Live dropdowns](live-dropdowns.md) — the ladder the fingerprint shares,
  and the rules for never spawning and never raising
- [ComfyUI custom nodepack background](comfyui-custom-nodepack-background.md)
  — the rest of the node contract
- [The process boundary](process-boundary.md) — what crosses, and when
