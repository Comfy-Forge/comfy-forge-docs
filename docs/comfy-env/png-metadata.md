# comfy-env and saved-image metadata

*Hidden inputs used to be dropped on the way to a worker, so isolated save
nodes wrote PNGs with no workflow chunk. They now travel in their own channel.
What crosses, what deliberately does not, and why.*
{: .subtitle }

## ComfyUI background

How ComfyUI declares hidden inputs and writes them into a PNG is the
subject of its own page, and none of this makes sense without it.

**[Read it first](comfyui-png-metadata.md)**.

## The shape of the fix

**Hidden inputs are not node inputs.** Upstream never treats them as such: for
V1 it injects them by sentinel into `input_data_all`, for V3 they never touch
kwargs at all. comfy-env used to put them in the kwargs channel and then strip
them back out of it — which is where both failures came from.

So they get their own field on the request frame, and never enter `kwargs`:

```python
"hidden": [[SENTINEL, parameter_name_or_None, value], ...]
```

Each entry carries the sentinel that decides *whether* a value crosses and the
parameter name that decides *where it lands*, which is exactly the split
upstream makes (`execution.py`).

The field is plain JSON on the frame, deliberately **not** routed through
`_to_shm`. Every sentinel comfy-env forwards is a string or a dict the browser
sent, so the walker's tensor hunt has nothing to find and would only cost a
traversal of the whole prompt graph. Keeping them out of `kwargs` has a second
effect worth stating: `_describe_value`, the debug value-describer, iterates
`kwargs`, so it **structurally cannot print an auth token** — no filter to
write, none to keep in sync.

### Parent side

| Proxy | Where the values are | What it sends |
|---|---|---|
| **V1** | in `kwargs`, under the author's own parameter name | pops each one, pairs it with its sentinel |
| **V3** | already on `cls.hidden` | reads the holder, sends by sentinel |

The V3 half needed no new plumbing at all. comfy-env's V3 proxy is a real
`io.ComfyNode` with `FUNCTION = "execute"`, so ComfyUI runs `PREPARE_CLASS_CLONE`
on it like any other node and `cls` arrives carrying a populated
`HiddenHolder`. It was being discarded on the next line.

### Worker side

The worker dispatches on the **real class**, never on which proxy called it.
That matters because a V3 node can end up behind a V1 proxy when the V3 build
or the V3 scan fails, and only the target knows which shape it wants:

| Target | How the values are applied |
|---|---|
| **V3** (`PREPARE_CLASS_CLONE` present) | onto a per-call class clone. `execute()` takes no `prompt=` kwarg, and the clone dies with the call, so a credential cannot outlive the call that needed it in a worker that lives for hours |
| **V1** | plain kwargs, under the parameter name the parent shipped alongside each value |

## What crosses

| Sentinel | Crosses | Note |
|---|---|---|
| `PROMPT` | yes | the executed graph; one of the two PNG chunks |
| `EXTRA_PNGINFO` | yes | the workflow; the other chunk. See the mutation caveat below |
| `UNIQUE_ID` | yes | matched by sentinel, so any parameter spelling works — `node_id`, `uid`, anything |
| `AUTH_TOKEN_COMFY_ORG` | yes | isolated API nodes authenticate |
| `API_KEY_COMFY_ORG` | yes | as above |
| `COMFY_USAGE_SOURCE` | yes | upstream does not treat it as sensitive; without it an isolated API node reports the fallback `"comfyui-api"` |
| `DYNPROMPT` | **no** | see below |

### `DYNPROMPT` is deliberately not forwarded

It is a live `DynamicPrompt` rather than data, and **nothing ComfyUI ships
consumes it** — zero hits across `nodes.py`, `comfy_extras/` and
`comfy_api_nodes/`. Forwarding it means transcribing upstream's class into
comfy-env's own worker source, which is a copy that can drift silently.

A node declaring it sees the same absent kwarg it saw before this change, so
nothing regressed. The work to support it is understood and small if a real
pack ever needs it; it is not being carried speculatively.

Note that a node *returning* an expand graph already works and always did:
the worker returns a plain dict and `execution.py` splices it in with an
ungated `if 'expand' in r:`. Only *reading* dynprompt is unsupported.

## Known gaps

Real, deliberate, and each one is a separate piece of work rather than part of
this change.

| # | Gap | Consequence |
|---|---|---|
| 1 | **`EXTRA_PNGINFO` mutation does not travel back.** A worker mutates its own copy | A node writing an entry for a *downstream* node to read is not seen. **This is the common use, not the edge case**: of 505 packs surveyed, 84 declare the input and every pack that *writes* to it does so for a downstream saver — `mikey_nodes.AddMetaData` (returns `IMAGE`, saves nothing), `bjornulf` `resize_image`, `Simple_Readable_Metadata-SG`. Isolated, the note is written on the worker's copy and the host's `SaveImage` never sees it. ~25 lines to return it on the reply and apply in place |
| 2 | **The worker does not enter `CurrentNodeContext`.** The prompt id itself does cross -- every call frame carries it as `prompt_gen`, read off ComfyUI's progress registry -- but the worker uses it only for memory-epoch marks and never installs it into `comfy_execution.utils` | Isolated API nodes drop the `Comfy-Job-Id` header |
| 3 | **`GraphBuilder.set_default_prefix` is parent-only** (`execution.py`) | Two isolated expanding nodes in one prompt would mint colliding ids. Latent — needs gap 1's sibling, expansion, to matter |

A former gap -- `cls.hidden` was `None` in a worker unless the node
declared a hidden input -- is closed. The worker now runs `VALIDATE_CLASS()`
and `PREPARE_CLASS_CLONE` on every V3 call, hidden inputs or not, and locks
the clone with `make_locked_method_func` exactly as `execution.py` does, so a
node that declared nothing sees a `HiddenHolder` of `None`s, the same object
it sees under plain ComfyUI.

## See also

- [Hidden inputs and PNG chunks](comfyui-png-metadata.md) — the upstream mechanism
- [The process boundary](process-boundary.md) — what else does and does not cross
- [comfy-env and model paths](folder-paths.md) — the registry that crosses whole
