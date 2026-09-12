# What survives isolation

*ComfyUI calls about eight things on a node class during a run. This is which
of them still work once the node lives in another process — ordered by the
symptom you would actually see, not by mechanism.*
{: .subtitle }

Every failure below is **silent, cosmetic, or misattributed**. None of them
announce themselves as an isolation problem, which is why this table is
ordered by symptom: an author's entry point is never "`VALIDATE_INPUTS` is
not forwarded", it is *"why did my node accept that value"*.

One of them announces itself. comfy-env prints a named line at startup, on
the cached scan path as well as the fresh one, for every node whose
`INPUT_TYPES` raised during the scan:

`check_lazy_status` no longer needs a warning: when you define one it is
forwarded (row 1).

*Audited against ComfyUI `15b212cc` (2026-09-07). Each row was traced on both
sides of the boundary.*

<div class="verdict-table wide-table num-col" markdown>

| # | Mechanism | Status | What you will actually observe | What to do |
|---|---|---|---|---|
| 1 | `check_lazy_status` — **when you define one** | <span class="v v-yes">works</span> | Your method runs in the worker and its answer comes back, so upstream's ask-then-compute loop gets a real answer: only the branch you name is computed. Forwarded only when the scan saw you define one. Cost: the inputs upstream already has cross once per round, typically two rounds | — |
| 2 | `__init__` and `self.x` | <span class="v v-yes">works</span> | State survives across executions **with its types intact** — a tuple stays a tuple, `bytes` and `set` and `torch.device` all cross — and an in-place `self.cache[k] = v` ships too (it silently did not, until 2026-09-11: the pre-call fingerprint was taken after the call). After a worker restart `__init__` **re-runs in the new process**. If any attribute was a live object the old process held (a thread pool, a lock, an open file), the fresh `__init__` state replaces the old state entirely, once, and the worker log says which attribute forced it; otherwise the old state is overlaid and the node continues where it was | Nothing. A live handle on `self` is fine: `__init__` rebuilds it after a restart, and the rest of `self` is reset with it — the same thing plain ComfyUI does on its own restart |
| 3 | `PromptServer.instance.send_sync(...)` | <span class="v v-yes">works</span> | A stand-in `server` module is installed before your pack imports, so `from server import PromptServer` at the top of a module no longer fails (154 of 493 surveyed packs have that line). `send_sync` reaches the browser through the host's real `PromptServer`: a dict, `bytes`, or a `(format, PIL.Image, max_size)` preview tuple all cross. `sid=None` broadcasts, as upstream; any other `sid` means *the client that queued this prompt*. `send_progress_text` and `client_id` work the same way. An event sent from a **background thread** is dropped with one log line rather than interleaved with the call. Anything else on `.instance` — `routes`, `prompt_queue`, `add_on_prompt_handler` — raises an `AttributeError` that names what is forwarded (D5) | Nothing for events. For inbound HTTP, use [`ROUTES`](register-nodes.md) |
| 4 | `ProgressBar` **preview** argument | <span class="v v-yes">works</span> | Live previews from an isolated sampler reach the browser. One limit: a single encoded preview over 1 MiB is dropped and the progress tick still goes, because upstream bypasses its own throttle whenever a preview is present | — |
| 5 | `cls.SCHEMA` (V3) | <span class="v v-yes">works</span> | The worker fills it in with the node's own inherited `GET_SCHEMA()` before dispatch, so a V3 node returning an **expand** graph no longer dies on `NoneType` | — |
| 6 | `lock_class` (V3) | <span class="v v-yes">works</span> | The worker runs the same four lines as `execution.py`, in the same order, hidden inputs or not: `VALIDATE_CLASS`, `PREPARE_CLASS_CLONE`, `make_locked_method_func`. `cls.foo = x` during execute raises the same `AttributeError` it raises in plain ComfyUI, and `cls.hidden` is a holder of `None`s rather than `None` itself when you declare no hidden inputs. Of 1,186 V3 node classes across 56 packs, exactly one wrote to `cls` in execute; it is deprecated and already raised upstream | Nothing |
| 7 | Class attributes on **V1** nodes | <span class="v v-partial">partial</span> | `DESCRIPTION` and `OUTPUT_TOOLTIPS` are dropped, so tooltips and the help panel are blank; `SEARCH_ALIASES` is dropped, so the node is unfindable by synonym; `NOT_IDEMPOTENT` is dropped, so two copies in one graph share a cache entry. V3 nodes are unaffected — **unless** the V3 build or scan capture fails, in which case the node silently demotes to a V1 proxy and loses all ten | Nothing available today. Consider a V3 node, where all of these are carried |
| 8 | `FUNCTION` dispatch, list batching, return shapes | <span class="v v-yes">works</span> | Nothing. Dispatch, `INPUT_IS_LIST`/`OUTPUT_IS_LIST`, `{"ui"}`/`{"result"}`/`{"expand"}`, V1 tuples and V3 `NodeOutput` all behave as upstream. Cost only: an *N*-item batch is *N* IPC round-trips | — |
| 9 | `INPUT_TYPES` | <span class="v v-yes">works</span> | Options go live whenever that pack's worker is alive and idle, because the node's own `INPUT_TYPES` is re-run there — however it builds the list. Frozen at scan values while the worker is cold or busy | See [dynamic combos](live-dropdowns.md) |
| 10 | `folder_paths` — the whole model-path registry | <span class="v v-yes">works</span> | Nothing. `get_full_path`, `get_save_image_path`, `recursive_search` all resolve against the host's real directories | See [model paths](folder-paths.md) |
| 11 | The metadata scan itself | <span class="v v-yes">works</span> | If a pack **hangs** during the scan, the whole scan process tree is killed after `COMFY_ENV_SCAN_TIMEOUT` (300 s) and the startup log names the node it was scanning, or says it hung during import. If a node's `INPUT_TYPES` **raises**, the node is a *missing* node: omitted from `/object_info` with the traceback logged, shown as the standard red placeholder that **keeps your saved widget values and links**, and the real cause reported when you queue a workflow that uses it. That scan is not cached, so a transient cause heals on the next start | Read the startup line. For a hang, lower the timeout while you look for the culprit |
| 12 | Hidden inputs (`PROMPT`, `EXTRA_PNGINFO`, `UNIQUE_ID`, the API credentials) | <span class="v v-yes">works</span> | Nothing. They travel in their own frame field and are applied by sentinel, so any parameter spelling works and isolated save nodes write their PNG workflow chunk. `DYNPROMPT` is the exception and is not forwarded | See [saved-image metadata](png-metadata.md) |
| 13 | `IS_CHANGED` / `fingerprint_inputs` — **when you define one** | <span class="v v-yes">works</span> | The pack's own fingerprint runs in its worker over the same ladder as [live dropdowns](live-dropdowns.md): when that worker is alive and idle it answers, otherwise the node counts as *changed* and re-runs. So the failure direction matches upstream: a cold worker, a busy worker, a raise, a non-primitive return or a non-primitive input all mean "re-run", never "serve the old result". The only cost is a recompute after a restart or an idle exit, which is what native ComfyUI does after a restart too | Nothing. Write the `IS_CHANGED` you would write for vanilla ComfyUI. If it depends on a tensor input, remember ComfyUI hands it `None` there natively as well |

</div>

## Deliberately unsupported

These five are **decisions**, not gaps. Each has a written reason and a
stated condition under which it would be revisited, alongside four others,
on **[Deliberately unsupported](deliberately-unsupported.md)**. They are
listed here so the symptom is still findable from this page.

<div class="verdict-table wide-table num-col" markdown>

| # | Mechanism | Status | What you will actually observe | What to do |
|---|---|---|---|---|
| D1 | `VALIDATE_INPUTS` / `validate_inputs` | <span class="v v-partial">deliberate</span> | The signature is reproduced faithfully — including a `**kwargs` catch-all, so the exemptions you declared survive. The **body** still never runs, so a rejection message never reaches the user: the node accepts the bad value and fails deeper in | Validate at the top of your node function and raise there. Name inputs explicitly rather than relying on `**kwargs` |
| D2 | `async def` node functions | <span class="v v-partial">deliberate</span> | The coroutine is never awaited. It fails at first execution with the real cause — *"its function is `async def` and the isolation worker does not await it … this is NOT a serialization problem"*. Why: see the linked page | Make the function synchronous. Run your own event loop inside it if you need one |
| D3 | Cancel (the Stop button) | <span class="v v-partial">deliberate</span> | Lands only while your node is driving a `ProgressBar`; otherwise nothing happens until the 600 s timeout kills the worker. Both of the old hazards are closed: the interrupt flag is now **read, not consumed**, so a click is never spent for nothing, and the worker's exception is a `BaseException` like upstream's, so a broad `except Exception` in your node can no longer swallow it | Drive a `ProgressBar` in any long loop, and never wrap it in a bare `except Exception` |
| D4 | Progress v2 (`set_progress`) | <span class="v v-partial">deliberate</span> | `ValueError: node_id must be provided if not in executing context`, or — if you pass `node_id` — a silent no-op into a handler-less registry. `set_progress` is `async def`, so this is blocked behind D2 — supporting it means supporting async nodes first | Use `comfy.utils.ProgressBar` for now |
| D5 | `PromptServer.instance.routes`, `prompt_queue`, `add_on_prompt_handler` | <span class="v v-partial">deliberate</span> | `AttributeError: PromptServer.instance.routes is not available in an isolated node process. comfy-env forwards send_sync, send_progress_text, client_id; HTTP routes, the prompt queue and prompt handlers run in the host and are not forwarded.` A pack that registers routes in the same module as its nodes fails at import with that line. 123 of the 154 surveyed packs that import `server` register routes | Move route registration behind [`ROUTES`](register-nodes.md), which the host serves and forwards |

</div>

## The pattern behind the tables

`check_lazy_status`, `IS_CHANGED` and `VALIDATE_INPUTS` are the same shape:
a mechanism ComfyUI invokes **before** the node executes. Forwarding those to
a worker by spawning it would cold-start every isolated environment in the
prompt before a single node ran. `IS_CHANGED` escapes that by asking only a
worker that already exists (row 14); `VALIDATE_INPUTS` stays deliberate (D1) and
documented in [caching and validation](caching-and-validation.md).

`check_lazy_status` is *not* in that category, which is why it *is*
forwarded: it fires for a node ComfyUI has already picked to execute, whose
worker is spawning anyway.

`send_sync`, previews, progress v2 and cancel share a different shape: a
worker can be *called*, but it cannot **originate** traffic to the browser
on its own. Inbound got an answer ([`ROUTES`](register-nodes.md)); previews
and `send_sync` now cross on the same callback channel; progress v2 stays
behind async (D4) and cancel stays cooperative-by-progress (D3).

## See also

- [The two classmethods](comfyui-caching-validation.md) — what `IS_CHANGED`
  and `VALIDATE_INPUTS` do upstream
- [comfy-env, caching and validation](caching-and-validation.md) — why
  neither is forwarded
- [The process boundary](process-boundary.md) — what crosses, mechanism by
  mechanism
- [Saved-image metadata](png-metadata.md) — hidden inputs in detail
