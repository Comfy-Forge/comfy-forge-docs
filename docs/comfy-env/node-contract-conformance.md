# What survives isolation

*ComfyUI calls about eight things on a node class during a run. This is which
of them still work once the node lives in another process — ordered by the
symptom you would actually see, not by mechanism.*
{: .subtitle }

Every failure below is **silent, cosmetic, or misattributed**. None of them
announce themselves as an isolation problem, which is why this table is
ordered by symptom: an author's entry point is never "`IS_CHANGED` is not
forwarded", it is *"why is my node stuck on the old result"*.

Three of them now announce themselves. comfy-env prints a named line at
startup — on the cached scan path as well as the fresh one — for every node
whose `IS_CHANGED` it had to drop, whose `check_lazy_status` it cannot forward,
and whose `INPUT_TYPES` raised during the scan:

```
[comfy-env] WARNING: MyPack: node 'LoadThing' defines IS_CHANGED/fingerprint_inputs,
  which is NOT forwarded across isolation. ComfyUI will treat this node as never
  changing and serve its cached output until restart. If it reads a file, a clock
  or an API, make that an input.
```

The lazy warning fires only when you actually defined a `check_lazy_status`.
Declaring `lazy` without one is not an isolation defect — see row 1b.

*Audited against ComfyUI `15b212cc` (2026-09-07). Each row was traced on both
sides of the boundary.*

<div class="verdict-table wide-table num-col" markdown>

| # | Mechanism | Status | What you will actually observe | What to do |
|---|---|---|---|---|
| 1 | `check_lazy_status` — **when you define one** | <span class="v v-no">not forwarded</span> | Your method is never called, so nothing promotes the pruned links back and the node runs with **every lazy input `None`**. The graph *appears* to have taken the fast path you asked for; the taken branch is not computed late, it is not computed at all | Take all inputs eagerly and branch inside the node |
| 1b | `{"lazy": True}` with **no** `check_lazy_status` | <span class="v v-partial">matches upstream</span> | The same `None` inputs — but plain ComfyUI does this too, so it is not an isolation defect. `ComfyNode.check_lazy_status`'s documented "requires all inputs" default is unreachable (`first_real_override` breaks at `GET_BASE_CLASS()`, which for a V3 node *is* `ComfyNode`), and `CheckLazyMixin` is opt-in with no core node inheriting it | Define `check_lazy_status` explicitly — but see row 1 |
| 2 | `__init__` and `self.x` | <span class="v v-partial">works, restart aside</span> | State survives across executions **with its types intact** — a tuple stays a tuple, `bytes` and `set` and `torch.device` all cross. What still bites: after a worker restart `__init__` does **not** re-run, so a node believes a file handle from the dead process is still open | Keep `self` state JSON-shaped. Never hold a live handle, socket, or thread on `self` — re-acquire it inside the function |
| 3 | `PromptServer.instance.send_sync(...)` | <span class="v v-no">not available</span> | Usually **`ModuleNotFoundError: No module named 'aiohttp'`** — `import server` pulls aiohttp (`server.py:32`), which a lean pack env has no reason to install. Where it *is* present you get `AttributeError: type object 'PromptServer' has no attribute 'instance'` instead, because nothing ever constructed a server in that process (`server.py:215-217`). Either way it reads as a broken ComfyUI install, and the pack's JS half loads fine, so it looks like a frontend bug | Return data through `{"ui": {...}}` instead. For inbound calls, use [`ROUTES`](register-nodes.md) |
| 4 | `ProgressBar` **preview** argument | <span class="v v-yes">works</span> | Live previews from an isolated sampler reach the browser. One limit: a single encoded preview over 1 MiB is dropped and the progress tick still goes, because upstream bypasses its own throttle whenever a preview is present | — |
| 5 | Progress v2 (`set_progress`) | <span class="v v-no">not handled</span> | `ValueError: node_id must be provided if not in executing context`, or — if you pass `node_id` — a silent no-op into a handler-less registry. Note upstream calls the v1 hook comfy-env relies on the *previous* API | Use `comfy.utils.ProgressBar` for now |
| 6 | `cls.SCHEMA` (V3) | <span class="v v-yes">works</span> | The worker fills it in with the node's own inherited `GET_SCHEMA()` before dispatch, so a V3 node returning an **expand** graph no longer dies on `NoneType` | — |
| 7 | `lock_class` (V3) | <span class="v v-no">not applied in the worker</span> | Nothing — and that is the problem. Writing `cls.foo = x` during execute succeeds and persists for the worker's life, where upstream raises. See the warning below | Do not write class state during execute. Test un-isolated before shipping |
| 8 | Class attributes on **V1** nodes | <span class="v v-partial">partial</span> | `DESCRIPTION` and `OUTPUT_TOOLTIPS` are dropped, so tooltips and the help panel are blank; `SEARCH_ALIASES` is dropped, so the node is unfindable by synonym; `NOT_IDEMPOTENT` is dropped, so two copies in one graph share a cache entry. V3 nodes are unaffected — **unless** the V3 build or scan capture fails, in which case the node silently demotes to a V1 proxy and loses all ten | Nothing available today. Consider a V3 node, where all of these are carried |
| 9 | `FUNCTION` dispatch, list batching, return shapes | <span class="v v-yes">works</span> | Nothing. Dispatch, `INPUT_IS_LIST`/`OUTPUT_IS_LIST`, `{"ui"}`/`{"result"}`/`{"expand"}`, V1 tuples and V3 `NodeOutput` all behave as upstream. Cost only: an *N*-item batch is *N* IPC round-trips | — |
| 10 | `INPUT_TYPES` | <span class="v v-yes">works</span> | Options go live whenever that pack's worker is alive and idle, because the node's own `INPUT_TYPES` is re-run there — however it builds the list. Frozen at scan values while the worker is cold or busy | See [dynamic combos](live-dropdowns.md) |
| 11 | `folder_paths` — the whole model-path registry | <span class="v v-yes">works</span> | Nothing. `get_full_path`, `get_save_image_path`, `recursive_search` all resolve against the host's real directories | See [model paths](folder-paths.md) |
| 12 | The metadata scan itself | <span class="v v-no">two silent failures</span> | If a pack's `INPUT_TYPES` **hangs**, ComfyUI never finishes starting and prints nothing — the scan subprocess has no timeout. If it **raises**, the node registers with zero widgets and the captured error is never read. This is the one defect you cannot read these docs to diagnose | Move the pack out of `custom_nodes` and restart. For a widget-less node, run with `COMFY_ENV_DEBUG_META=1` and read the scan's stderr |
| 13 | Hidden inputs (`PROMPT`, `EXTRA_PNGINFO`, `UNIQUE_ID`, the API credentials) | <span class="v v-yes">works</span> | Nothing. They travel in their own frame field and are applied by sentinel, so any parameter spelling works and isolated save nodes write their PNG workflow chunk. `DYNPROMPT` is the exception and is not forwarded | See [saved-image metadata](png-metadata.md) |

</div>

!!! warning "`lock_class` not applying is the one that points the wrong way"
    Upstream forbids a node writing to its own class during execution, and
    raises if you try. In a worker that write **succeeds** and persists for
    the life of the process.

    So isolation here is *more permissive* than plain ComfyUI. A pack
    developed only against comfy-env can ship a node that works isolated and
    breaks for everyone running it normally. Test both ways.

## Deliberately unsupported

These four are **decisions**, not gaps. Each has a written reason and a
stated condition under which it would be revisited, alongside four others,
on **[Deliberately unsupported](deliberately-unsupported.md)**. They are
listed here so the symptom is still findable from this page.

<div class="verdict-table wide-table num-col" markdown>

| # | Mechanism | Status | What you will actually observe | What to do |
|---|---|---|---|---|
| D1 | `IS_CHANGED` / `fingerprint_inputs` | <span class="v v-partial">deliberate</span> | `return float("nan")` stops re-running. The node caches on its inputs forever; restarting ComfyUI "fixes" it. Upstream fails toward *re-running*, comfy-env fails toward *caching* — opposite directions, which is why the symptom never points you at caching | Make the changing thing an **input**. A seed, an mtime, a counter widget — anything that moves changes the cache key honestly |
| D2 | `VALIDATE_INPUTS` / `validate_inputs` | <span class="v v-partial">deliberate</span> | The signature is reproduced faithfully — including a `**kwargs` catch-all, so the exemptions you declared survive. The **body** still never runs, so a rejection message never reaches the user: the node accepts the bad value and fails deeper in | Validate at the top of your node function and raise there. Name inputs explicitly rather than relying on `**kwargs` |
| D3 | `async def` node functions | <span class="v v-partial">deliberate</span> | The coroutine is never awaited. It fails at first execution with the real cause — *"its function is `async def` and the isolation worker does not await it … this is NOT a serialization problem"*. Why: see the linked page | Make the function synchronous. Run your own event loop inside it if you need one |
| D4 | Cancel (the Stop button) | <span class="v v-partial">deliberate</span> | Lands only while your node is driving a `ProgressBar`; otherwise nothing happens until the 600 s timeout kills the worker. Both of the old hazards are closed: the interrupt flag is now **read, not consumed**, so a click is never spent for nothing, and the worker's exception is a `BaseException` like upstream's, so a broad `except Exception` in your node can no longer swallow it | Drive a `ProgressBar` in any long loop, and never wrap it in a bare `except Exception` |

</div>

## The pattern behind the tables

`check_lazy_status`, `IS_CHANGED` and `VALIDATE_INPUTS` are the same shape:
a mechanism ComfyUI invokes **before** the node executes. Forwarding those to
a worker would cold-spawn every isolated environment in the prompt before a
single node ran — which is why two of them are deliberate (D1, D2) and
documented in [caching and validation](caching-and-validation.md).

`check_lazy_status` is *not* in that category and is simply missing: it fires
for a node ComfyUI has already picked to execute, whose worker is spawning
anyway.

`send_sync`, previews, progress v2 and cancel share a different shape: a
worker can be *called*, but it cannot **originate** traffic to the browser.
Inbound got an answer ([`ROUTES`](register-nodes.md)); previews now cross;
outbound events and progress handlers do not.

## See also

- [The two classmethods](comfyui-caching-validation.md) — what `IS_CHANGED`
  and `VALIDATE_INPUTS` do upstream
- [comfy-env, caching and validation](caching-and-validation.md) — why
  neither is forwarded
- [The process boundary](process-boundary.md) — what crosses, mechanism by
  mechanism
- [Saved-image metadata](png-metadata.md) — hidden inputs in detail
