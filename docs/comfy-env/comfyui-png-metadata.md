# Hidden inputs and PNG chunks

*How ComfyUI hands a node values the user never wired up, and how two
of them end up inside the saved image. This is the mechanism behind
drag-and-drop workflow restore.*
{: .subtitle }

## The third input bucket

A node declares these in `INPUT_TYPES`, alongside `required` and `optional`:

```python
# nodes.py (SaveImage)
"hidden": {
    "prompt": "PROMPT", "extra_pnginfo": "EXTRA_PNGINFO"
},
```

The two halves of each entry in the "hidden" dict are not symmetrical:

- **The key is yours.** It is simply the name of the parameter you want the
  value delivered as. `prompt`, `p`, `the_graph`... anything.
- **The value is a fixed sentinel**, matched as a literal string against a
  closed list. Get it wrong by one character and nothing happens.

### The complete list of sentinels

There are **seven**, and the list is closed. The V1 executor matches them in
a flat `if` chain (`execution.py`) and V3 declares the same seven as
an enum (`comfy_api/latest/_io.py`), so neither path has anything
the other lacks.

*Last verified against ComfyUI `15b212cc` (2026-09-07). All seven resolve on
both paths; the Meaning column is upstream's own docstrings, condensed.*

#### How to read the "From" column

| Marking | Meaning |
|---|---|
| **engine** | the executor built it and owns it — a live object or its own execution state |
| **browser** | a verbatim `extra_data` lookup. The client sent it alongside the prompt and ComfyUI passes it through without inspecting it |

<div class="wide-table num-col" markdown>

| # | Sentinel | From | Meaning | What the executor hands over | V3 attribute |
|---|---|---|---|---|---|
| 1 | `PROMPT` | engine | the complete prompt sent by the client to the server | `dynprompt.get_original_prompt()`, or `{}` if there is no dynprompt | `cls.hidden.prompt` |
| 2 | `EXTRA_PNGINFO` | browser | a dict **copied into the metadata of any `.png` saved**. Packs also use it as a side channel to a downstream node | `extra_data.get('extra_pnginfo', None)` | `cls.hidden.extra_pnginfo` |
| 3 | `DYNPROMPT` | engine | a `comfy_execution.graph.DynamicPrompt`. Unlike `PROMPT` it **mutates during execution** in response to node expansion | `dynprompt` — the live object itself | `cls.hidden.dynprompt` |
| 4 | `UNIQUE_ID` | engine | this node's id, matching the client-side node id — used for server→client messages about a specific node | `unique_id` | `cls.hidden.unique_id` |
| 5 | `AUTH_TOKEN_COMFY_ORG` | browser | token from signing into a Comfy.org account in the frontend | `extra_data.get("auth_token_comfy_org", None)` | `cls.hidden.auth_token_comfy_org` |
| 6 | `API_KEY_COMFY_ORG` | browser | a Comfy.org API key, which skips the sign-in | `extra_data.get("api_key_comfy_org", None)` | `cls.hidden.api_key_comfy_org` |
| 7 | `COMFY_USAGE_SOURCE` | browser | which client submitted the prompt (`comfyui-frontend`, `comfy-cli`, `comfyui-mcp`), forwarded upstream by API nodes as a `Comfy-Usage-Source` header | `extra_data.get("comfy_usage_source", None)` | `cls.hidden.comfy_usage_source` |

</div>

That split explains more than it looks like it should. The four browser
values are ordinary JSON the server never validates, which is why
`EXTRA_PNGINFO` doubles as a pack-to-pack side channel and why the
credentials are strings rather than objects.

Of the three engine values, `DYNPROMPT` is the only one that is a **live mutating object** rather than
data, and that is exactly the one that cannot survive a process boundary.

### What "mutates during execution" means

A node may return a **subgraph** instead of a value, and the executor splices
it into the running prompt (`execution.py`):

```python
dynprompt.add_ephemeral_node(node_id, node_info, unique_id, display_id)
```

`original_prompt` never changes; `ephemeral_prompt` grows. Nodes that did not
exist when you pressed Run are now in the graph and will execute.

That is how looping works without ComfyUI having loops — a loop node returns a
subgraph containing a copy of **itself**, so the graph unrolls one iteration
at a time:

```
submitted:   [Open]  -> [body]  -> [Close]                  3 nodes
             Close returns a subgraph containing copies of
             Open, body, and itself
running:     [Open]  -> [body]  -> [Close]
             [Open'] -> [body'] -> [Recurse]                6 nodes  ...
```

Such a node needs `DYNPROMPT` rather than `PROMPT` because by the second
iteration the nodes it must clone were created by the first, and exist only in
`ephemeral_prompt`.

**Nothing ComfyUI ships uses this.** Grepping `"expand":` outside `tests/`
returns nothing, and V3 gates it behind `enable_expand=True` on the schema.

### "During execution" means between nodes, not during one

This is worth being exact about, because the loose reading is the one that
misleads. The ephemeral dicts have two write sites in the whole tree —
`__init__` (`graph.py`) and `add_ephemeral_node` — and
`add_ephemeral_node` has exactly one caller:

```
execution.py inside `if has_subgraph:` (:579)
                   and has_subgraph is known only AFTER
                   get_output_data() returns                     (:545)
```

Expansion is therefore strictly a **between-nodes** event. While a node is
running, nothing can touch the object it was handed.

That matters for anything crossing a process boundary: a snapshot taken at
call time reports exactly what the live object would, for that call's entire
duration. `DYNPROMPT` is a live object over the span of a *prompt*, not over
the span of a *call*.

### There is no validation, in either direction

The chain is a series of plain `if` statements with no `else` and no registry.
A sentinel ComfyUI does not recognise is **silently skipped**:
`input_data_all[x]` is never assigned, so the parameter falls back to its
Python default. A typo'd `"EXTRA_PNG_INFO"` produces no error and no warning —
just a node that quietly never receives its workflow.

V3 is forgiving in the other direction too. `HiddenHolder.__getattr__` returns
`None` for any attribute it does not hold, so reading a hidden value you never
declared gives `None` rather than raising. That only helps while `cls.hidden`
is itself a `HiddenHolder`; if it is `None`, the attribute access raises
normally.

## The two `add_text` calls that make restore work

`SaveImage` writes those two values into the PNG as text chunks
(`nodes.py`):

```python
metadata = None
if not args.disable_metadata:
    metadata = PngInfo()
    if prompt is not None:
        metadata.add_text("prompt", json.dumps(prompt))
    if extra_pnginfo is not None:
        for x in extra_pnginfo:
            metadata.add_text(x, json.dumps(extra_pnginfo[x]))

img.save(..., pnginfo=metadata, compress_level=self.compress_level)
```

That is the entire mechanism behind drag-and-drop workflow restore. The
`workflow` chunk comes from `extra_pnginfo`; the `prompt` chunk is the
executed form.

The V3 path does the same thing through a helper
(`comfy_api/latest/_ui.py`), reading from `cls.hidden` instead of
arguments:

```python
if args.disable_metadata or cls is None or not cls.hidden:
    return None
```

## Both paths fail open, and silently

`prompt=None` skips the chunk. `not cls.hidden` returns `None` for the whole
`PngInfo`. The image saves perfectly either way — correct pixels, correct
filename, correct counter. Nothing logs and nothing raises.

The loss is only discoverable by dragging the file back and watching nothing
happen, which is why this class of bug survives for a long time.

## See also

- [comfy-env and saved-image metadata](png-metadata.md) — what happens to all of
  this once the node runs in another process
