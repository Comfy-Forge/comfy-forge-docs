# What survives isolation

*ComfyUI calls about eight things on a node class during a run. This is which
of them still work once the node lives in another process — ordered by the
symptom you would actually see, not by mechanism.*
{: .subtitle }

Every failure below is **silent, cosmetic, or misattributed**. None of them
announce themselves as an isolation problem, which is why this table is
ordered by symptom: an author's entry point is never "`IS_CHANGED` is not
forwarded", it is *"why is my node stuck on the old result"*.

*Audited against ComfyUI `15b212cc` (2026-09-07) and comfy-env `2990fb6`. Each
row was traced on both sides of the boundary.*

<div class="verdict-table wide-table num-col" markdown>

| # | Mechanism | Status | What you will actually observe | What to do |
|---|---|---|---|---|
| 1 | `check_lazy_status` — **when you define one** | <span class="v v-no">not forwarded</span> | Your method is never called, so nothing promotes the pruned links back and the node runs with **every lazy input `None`**. The graph *appears* to have taken the fast path you asked for; the taken branch is not computed late, it is not computed at all | Take all inputs eagerly and branch inside the node |
| 1b | `{"lazy": True}` with **no** `check_lazy_status` | <span class="v v-partial">matches upstream</span> | The same `None` inputs — but plain ComfyUI does this too, so it is not an isolation defect. `ComfyNode.check_lazy_status`'s documented "requires all inputs" default is unreachable (`first_real_override` breaks at `GET_BASE_CLASS()`, which for a V3 node *is* `ComfyNode`), and `CheckLazyMixin` is opt-in with no core node inheriting it | Define `check_lazy_status` explicitly — but see row 1 |
| 2 | `IS_CHANGED` / `fingerprint_inputs` | <span class="v v-no">not forwarded</span> | `return float("nan")` stops re-running. The node caches on its inputs forever; restarting ComfyUI "fixes" it. Upstream fails toward *re-running*, comfy-env fails toward *caching* — opposite directions, which is why the symptom never points you at caching | Make the changing thing an **input**. A seed, an mtime, a counter widget — anything that moves changes the cache key honestly |
| 3 | `VALIDATE_INPUTS` / `validate_inputs` | <span class="v v-partial">signature only</span> | The body never runs, so a rejection message never reaches the user; the node accepts the bad value and fails deeper in. Worse for a `**kwargs` signature: nothing is attached at all, ComfyUI re-imposes the checks you exempted, and a workflow that submits fine natively is **rejected** once isolated | Validate at the top of your node function and raise there. Name inputs explicitly rather than relying on `**kwargs` |
| 4 | `__init__` and `self.x` | <span class="v v-partial">works, as JSON</span> | **`TypeError: Object of type device is not JSON serializable`** if anything on `self` is not JSON-shaped — `torch.device`, a dtype, `bytes`, a `set`, a small tensor — failing every call, forever. Otherwise state survives across executions, but as JSON: `self.t = (1,2,3)` reads back `[1,2,3]`, `{1:"a"}` reads back `{"1":"a"}`, with nothing logged. After a worker restart `__init__` does **not** re-run, so a node believes a file handle from the dead process is still open | Keep `self` state JSON-shaped. Never hold a live handle, socket, or thread on `self` — re-acquire it inside the function |
| 5 | `async def` node functions | <span class="v v-no">not supported</span> | The coroutine is never awaited and reaches the serializer, so the error you get says *"cannot serialize 'coroutine' … register a serializer in your pack's serialization.py"* — pointing at entirely the wrong place | Make the function synchronous. Run your own event loop inside it if you need one |
| 6 | `PromptServer.instance.send_sync(...)` | <span class="v v-no">not available</span> | Usually **`ModuleNotFoundError: No module named 'aiohttp'`** — `import server` pulls aiohttp (`server.py:32`), which a lean pack env has no reason to install. Where it *is* present you get `AttributeError: type object 'PromptServer' has no attribute 'instance'` instead, because nothing ever constructed a server in that process (`server.py:215-217`). Either way it reads as a broken ComfyUI install, and the pack's JS half loads fine, so it looks like a frontend bug | Return data through `{"ui": {...}}` instead. For inbound calls, use [`ROUTES`](register-nodes.md) |
| 7 | `ProgressBar` **preview** argument | <span class="v v-no">dropped</span> | Value and total move the bar correctly and land on the right node. The `preview` argument is discarded, so an isolated sampler shows a moving bar and a **permanently blank preview**, with no error and no log line | Nothing available today. Bar-only progress works |
| 8 | Cancel (the Stop button) | <span class="v v-partial">cooperative</span> | Lands only while your node is driving a `ProgressBar`; otherwise nothing happens until the 600 s timeout kills the worker. The two remaining hazards are one chain, not two: the flag is consumed before the raise, and comfy-env's exception is a `RuntimeError` where upstream's is deliberately a `BaseException` — so a broad `except Exception` in your node **swallows it after the click was already spent**, and the user must press Stop again. Consumed-and-propagated works correctly; consumed-and-swallowed is what loses the click | Drive a `ProgressBar` in any long loop, and never wrap it in a bare `except Exception` |
| 9 | Progress v2 (`set_progress`) | <span class="v v-no">not handled</span> | `ValueError: node_id must be provided if not in executing context`, or — if you pass `node_id` — a silent no-op into a handler-less registry. Note upstream calls the v1 hook comfy-env relies on the *previous* API | Use `comfy.utils.ProgressBar` for now |
| 10 | `cls.SCHEMA` (V3) | <span class="v v-no">not populated in the worker</span> | Ordinary nodes are unaffected — the check short-circuits. A V3 node returning an **expand** graph dies on `AttributeError: 'NoneType' object has no attribute 'enable_expand'`. The V1 expand path works fine, so this looks version-specific and arbitrary | Return an expand graph from a V1 node, or avoid expansion in an isolated V3 pack |
| 11 | `lock_class` (V3) | <span class="v v-no">not applied in the worker</span> | Nothing — and that is the problem. Writing `cls.foo = x` during execute succeeds and persists for the worker's life, where upstream raises. See the warning below | Do not write class state during execute. Test un-isolated before shipping |
| 12 | Class attributes on **V1** nodes | <span class="v v-partial">partial</span> | `DESCRIPTION` and `OUTPUT_TOOLTIPS` are dropped, so tooltips and the help panel are blank; `SEARCH_ALIASES` is dropped, so the node is unfindable by synonym; `NOT_IDEMPOTENT` is dropped, so two copies in one graph share a cache entry. V3 nodes are unaffected — **unless** the V3 build or scan capture fails, in which case the node silently demotes to a V1 proxy and loses all ten | Nothing available today. Consider a V3 node, where all of these are carried |
| 13 | `FUNCTION` dispatch, list batching, return shapes | <span class="v v-yes">works</span> | Nothing. Dispatch, `INPUT_IS_LIST`/`OUTPUT_IS_LIST`, `{"ui"}`/`{"result"}`/`{"expand"}`, V1 tuples and V3 `NodeOutput` all behave as upstream. Cost only: an *N*-item batch is *N* IPC round-trips | — |
| 14 | `INPUT_TYPES` | <span class="v v-yes">works</span> | Options go live whenever that pack's worker is alive and idle, because the node's own `INPUT_TYPES` is re-run there — however it builds the list. Frozen at scan values while the worker is cold or busy | See [dynamic combos](live-dropdowns.md) |
| 15 | `folder_paths` — the whole model-path registry | <span class="v v-yes">works</span> | Nothing. `get_full_path`, `get_save_image_path`, `recursive_search` all resolve against the host's real directories | See [model paths](folder-paths.md) |
| 16 | The metadata scan itself | <span class="v v-no">two silent failures</span> | If a pack's `INPUT_TYPES` **hangs**, ComfyUI never finishes starting and prints nothing — the scan subprocess has no timeout. If it **raises**, the node registers with zero widgets and the captured error is never read. This is the one defect you cannot read these docs to diagnose | Move the pack out of `custom_nodes` and restart. For a widget-less node, run with `COMFY_ENV_DEBUG_META=1` and read the scan's stderr |
| 17 | Hidden inputs (`PROMPT`, `EXTRA_PNGINFO`, `UNIQUE_ID`, the API credentials) | <span class="v v-yes">works</span> | Nothing. They travel in their own frame field and are applied by sentinel, so any parameter spelling works and isolated save nodes write their PNG workflow chunk. `DYNPROMPT` is the exception and is not forwarded | See [saved-image metadata](png-metadata.md) |

</div>

!!! warning "`lock_class` not applying is the one that points the wrong way"
    Upstream forbids a node writing to its own class during execution, and
    raises if you try. In a worker that write **succeeds** and persists for
    the life of the process.

    So isolation here is *more permissive* than plain ComfyUI. A pack
    developed only against comfy-env can ship a node that works isolated and
    breaks for everyone running it normally. Test both ways.

## The pattern behind the table

Rows 1, 2 and 3 are the same shape: a mechanism ComfyUI invokes **before** the
node executes. Forwarding those to a worker would cold-spawn every isolated
environment in the prompt before a single node ran, which is why two of them
are deliberate — see [caching and validation](caching-and-validation.md).

Row 1 is *not* in that category and is simply missing: `check_lazy_status`
fires for a node ComfyUI has already picked to execute, whose worker is
spawning anyway.

Rows 6 through 9 share a different shape: a worker can be *called*, but it
cannot **originate** traffic to the browser. Inbound got an answer
([`ROUTES`](register-nodes.md)); outbound events, previews and progress
handlers did not.

## See also

- [The two classmethods](comfyui-caching-validation.md) — what `IS_CHANGED`
  and `VALIDATE_INPUTS` do upstream
- [comfy-env, caching and validation](caching-and-validation.md) — why
  neither is forwarded
- [The process boundary](process-boundary.md) — what crosses, mechanism by
  mechanism
- [Saved-image metadata](png-metadata.md) — hidden inputs in detail
