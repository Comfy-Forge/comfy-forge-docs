# Live dropdowns

*An isolated node's `INPUT_TYPES` is a snapshot taken once, in another
process. This is how its dropdowns stay current anyway — by asking the worker
that owns the node, but only if that worker is already awake.*
{: .subtitle }

## The problem this exists to solve

In vanilla ComfyUI, `INPUT_TYPES()` is a function of live state, re-evaluated
on every `/object_info` request. That is why the checkpoint dropdown updates
when you drop a new file into `models/checkpoints/` and hit refresh.

An isolated node cannot work that way. Its class lives in a worker process,
so the main process holds only a proxy whose `INPUT_TYPES` replays a payload
captured once, by the metadata scan, and then cached on disk. Left alone, a
combo built from a directory listing would be frozen at scan time: upload a
new `.obj` and it never appears — not on refresh, not on restart, because the
disk cache outlives the process that produced it.

!!! note "This is the modal case, not an edge case"
    Measured across a 493-pack third-party corpus, **236 packs (48%)** have an
    `INPUT_TYPES` that reads the filesystem.

## The ladder

comfy-env asks the worker that owns the node to re-run **the node's own
`INPUT_TYPES`**. Not a reconstruction of it, not a recipe inferred from its
output — the real function, in the environment that owns it.

The whole design is which worker it is willing to ask
(`isolation/metadata.py`, `_refresh_combo_options`):

<div class="num-col" markdown>

| # | Worker for this env | What happens |
|---|---|---|
| 1 | **alive and idle** | ask it. The pack's real `INPUT_TYPES()` runs and its combo options are spliced into the cached snapshot |
| 2 | **alive but mid-call** | `send_command_no_spawn` returns `"busy"` after 0.25 s. Fall through |
| 3 | **dead, or never started** | fall through |

</div>

Rungs 2 and 3 both mean *keep the cached options* — which is exactly what a
node with no dynamic dropdown at all does. **The miss path is the old frozen
behaviour, so the ladder can only ever add.**

### Why "spawn one" is not a rung

`/object_info` enumerates the **entire node registry** on every page load
(`server.py`), not the nodes on your canvas. So spawning there would
start every isolated environment on the machine in order to draw a dropdown —
and `_get_or_create_worker` runs on the asyncio event loop, holding a global
lock across the whole cold start. ComfyUI's HTTP server would freeze for the
duration: no `/prompt`, no `/interrupt`, no progress, no images.

Live options are worth a socket round trip to a process that already exists.
They are not worth starting one.

### What the worker sends back

Combo option lists, and nothing else. An input spec's first element is a list
only for a combo; `"IMAGE"` and `("INT", {…})` are left alone. An option list
containing anything but a primitive is refused outright rather than partially
converted.

The rest of the spec — tooltips, defaults, `image_upload`, and the set of
inputs itself — stays as captured at scan time. A node that changed its
**shape** between calls would invalidate the proxy class the parent already
built and handed to ComfyUI, so only the contents of a dropdown may move.

Two splice rules are load bearing:

- **An empty listing keeps the cache.** Splicing `[]` would fail combo
  validation for every saved workflow using that node the moment a folder is
  empty — mid-upload, external drive unmounted. A transient state must not
  brick a workflow.
- **An unreported input is left alone.** The fresh answer is additive.
  Silence about an input is not a claim that it disappeared.

## What a pack author writes

Nothing.

There is no helper to import, no marker key, no declaration. A node writes the
same `INPUT_TYPES` it would write for vanilla ComfyUI — `get_filename_list`, a
hand-rolled `os.walk`, a `glob`, anything — and the ladder runs it.

That is the entire point of asking the worker rather than reconstructing the
recipe in the host: **there is no recipe to reconstruct.**

## Validation

A live dropdown is useless if execution then rejects the freshly-uploaded
value. ComfyUI exempts an input from its built-in combo and min/max checks
when the input's name appears in the validate function's argspec
(`execution.py`), so the proxy carries a synthesized `VALIDATE_INPUTS`
(V1) / `validate_inputs` (V3) naming **exactly the node's combo inputs**, plus
any the pack's own validate named.

Combos only, and deliberately. They are the exact set whose options the
refresh can rewrite, and they have no min/max — so numeric clamps keep
working. comfy-env never *adds* a `**kwargs` form, because that would exempt
every input on the node and silently disable those clamps. It does
*reproduce* one: if the pack's own validate was written `(cls, **kwargs)`,
the synthesized signature carries `**kwargs` too (`validate_varkw`, captured
by the scan), and every built-in check is waived — exactly as it is
natively.

Only the *signature* is reproduced. The synthesized body is `return True`, so
the pack's own validation body runs in the worker, right before the node's
function, with what upstream handed the stand-in at submit — see
[Caching and validation](caching-and-validation.md).

## Never raises

`/object_info` enumerates every node, and a raise inside `INPUT_TYPES` makes
core omit the node **entirely**. A vanished node is strictly worse than a
stale dropdown.

So every failure on this path returns `None` and the proxy keeps its cached
options: a dead socket, a busy worker, a pack whose `INPUT_TYPES` throws, a
malformed reply. The worker logs the error on every failed call — once per
`/object_info` request — and the user sees a dropdown that has not moved.

## Limits — read this part

- **Cold means frozen.** A dropdown only goes live once that pack's worker is
  running, which in practice means after you have executed one of its nodes.
  Open ComfyUI, add a file, refresh without running anything, and you see the
  scan-time list.
- **The pack's own `IS_CHANGED` rides the same ladder.** A node that
  defines `IS_CHANGED` or `fingerprint_inputs` gets its real fingerprint
  from its worker on rung 1, and answers *changed* on rungs 2 and 3, so
  overwriting a mesh in place re-executes as long as the pack's fingerprint
  says so. The miss answer is inverted on purpose: a stale dropdown is
  cosmetic, a stale cached result is wrong. See
  [caching and validation](caching-and-validation.md).
- **`remote` combos are left alone.** Both spec shapes are recognised — the
  hand-written `(["a", "b"], {...})` and the canonical
  `("COMBO", {"options": [...]})` every V3 `Combo` input becomes — but a
  combo declared `remote` carries no options for anyone to refresh: the
  frontend fetches them from a route, and the author's own validate
  exemption covers the value, as natively.
- **Everything else in the payload stays frozen** — `RETURN_TYPES`, tooltips,
  and any option list computed from something other than a file listing
  (installed backends, GPU capability probes, API queries). Those are live
  under rung 1 like anything else, and frozen under 2 and 3.
- **No `remote` widget.** ComfyUI's frontend-fetched options widget would move
  the refresh onto the canvas repaint path, where comfy-env cannot control how
  often it fires, and its failure mode leaves the combo bound to a bare string
  for the rest of the session. Rejected; the ladder needs no frontend support
  at all.

## See also

- [`register_nodes()`](register-nodes.md) — where proxies are synthesized and
  the metadata scan runs
- [The process boundary](process-boundary.md) — why the class is not in the
  main process to begin with
- [Worker lifecycle](worker-lifecycle.md) — what "alive" means, and the idle
  sweep that ends it
