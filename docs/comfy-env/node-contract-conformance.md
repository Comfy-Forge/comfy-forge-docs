# What survives isolation

*ComfyUI calls about eight things on a node class during a run. This is which
of them still work once the node lives in another process — ordered by the
symptom you would actually see, not by mechanism.*
{: .subtitle }

Every failure below is **silent, cosmetic, or misattributed**. None of them
announce themselves as an isolation problem, which is why this table is
ordered by symptom: an author's entry point is never "`VALIDATE_INPUTS` is
not forwarded", it is *"why did my node accept that value"*.

One of them announces itself. comfy-env prints a named line at startup for
every node whose `INPUT_TYPES` raised during the scan. A scan payload that
carries such a node is never written to the cache, so there is no cached
path for this line: it comes from a fresh scan every time, and the next
start retries the scan:

`[comfy-env] WARNING: <pack>: node '<name>' INPUT_TYPES() raised during the scan; ComfyUI will show it as a missing node and report the cause when a workflow uses it (scan is retried on the next start): <error>`

`check_lazy_status` no longer needs a warning: when you define one it is
forwarded (row 1).

*Audited against ComfyUI `15b212cc` (2026-09-07). Each row was traced on both
sides of the boundary.*

<div class="verdict-table wide-table num-col" markdown>

| # | Mechanism | Status | ELI5 | What to do |
|---|---|---|---|---|
| 1 | `check_lazy_status` — **when you define one** | <span class="v v-yes">works</span> | A node can mark inputs as *lazy*: ComfyUI skips computing them up front and asks the node which ones it actually needs, then runs only those. Under isolation the question goes to the worker and the node's own answer comes back, so the unused branch stays uncomputed, same as native. | — |
| 2 | `__init__` and `self.x` (V1) | <span class="v v-yes">works</span> | **V1 nodes only** (a V3 node has no `self`). The host carries `self` between calls and the worker uses it, so attributes survive with their types, including in-place edits like `self.cache[k] = v`. If the worker crashes or restarts, the node reinitializes: `__init__` runs again in the new process, the same as ComfyUI itself restarting | Nothing. A live handle on `self` is fine, `__init__` rebuilds it after a restart |
| 3 | `PromptServer.instance.send_sync(...)` | <span class="v v-yes">works</span> | ComfyUI keeps a websocket open to the browser and `send_sync(event, data)` is how Python pushes a message down it. Packs use it to talk to their own JavaScript: write text into a widget while the node runs, pop a toast, queue another prompt, send a value to a receiver node. Under isolation the worker forwards the message to the host, which pushes it on the real socket, so `from server import PromptServer` works and so does the call. `sid=None` means every tab, anything else means the tab that clicked Run. A send from a background thread is dropped with one log line. Anything on the server other than `send_sync`, `send_progress_text` and `client_id` raises an error saying so (D4) | Nothing for events. For inbound HTTP, use [`ROUTES`](register-nodes.md) |
| 4 | `ProgressBar` **preview** argument | <span class="v v-yes">works</span> | Preview frames from an isolated sampler show up in the browser. One frame bigger than 1 MiB is skipped (the progress bar still ticks) because ComfyUI sends every frame that has a preview without throttling | — |
| 5 | `cls.SCHEMA` (V3) | <span class="v v-yes">works</span> | `cls.SCHEMA` is a V3 node's description of itself (inputs, outputs, flags such as `enable_expand`). ComfyUI fills it in once at registration and reads it at run time, for example to check `enable_expand` when a node returns a subgraph to run in its place. A worker never registers nodes, but there is custom machinery so that the worker fills it in itself before the first call | — |
| 6 | `lock_class` (V3) | <span class="v v-yes">works</span> | ComfyUI hands your V3 `execute` a locked copy of the class so `cls.foo = x` fails. The worker does exactly the same, so a node can't work isolated and break for everyone else. (Of 1,186 V3 node classes, one writes to `cls`; it's deprecated and already fails natively) | Nothing |
| 7 | Class attributes on **V1** nodes | <span class="v v-yes">works</span> | The labels on the box — description, tooltips, deprecated/experimental badges, search aliases, the "don't share my cache" flag, and any label ComfyUI reads tomorrow — are copied onto the stand-in. Huge lists (over 200 items) are treated as data, not labels. A V3 node that falls back to the V1 stand-in keeps them too, and that fallback is printed to the log | Nothing |
| 8 | `FUNCTION` dispatch, list batching, return shapes | <span class="v v-yes">works</span> | Calling the node, batching over lists, and every return shape behave as native. The only cost: a batch of N items is N trips over the socket | — |
| 9 | `INPUT_TYPES` | <span class="v v-yes">works</span> | Dropdown lists refresh whenever the pack's worker is awake and idle, because the node's own `INPUT_TYPES` is re-run there — for both ways a dropdown can be written, including the one every V3 `io.Combo` uses (that one was frozen until 2026-09-12). While the worker is cold or busy you see the list from startup. A `remote` dropdown is the browser's business, not ours | See [dynamic combos](live-dropdowns.md) |
| 10 | `folder_paths` — the whole model-path registry | <span class="v v-yes">works</span> | Every model-path lookup resolves against the host's real folders, so files land where you expect | See [model paths](folder-paths.md) |
| 11 | The metadata scan itself | <span class="v v-yes">works</span> | If a pack hangs while being scanned at startup, it gets killed after `COMFY_ENV_SCAN_TIMEOUT` (300 s) and the log names the node it was on. If a node's `INPUT_TYPES` throws, that node shows as a normal missing node — your saved widget values survive, and you see the real error when you queue. That scan isn't cached, so a one-off cause heals on the next start | Read the startup line. For a hang, lower the timeout while you look for the culprit |
| 12 | Hidden inputs (`PROMPT`, `EXTRA_PNGINFO`, `UNIQUE_ID`, the API credentials) | <span class="v v-yes">works</span> | The hidden extras ComfyUI passes to nodes (the prompt, the workflow, your node id, API keys) all arrive, whatever you named the parameter, so isolated save nodes write the workflow into the PNG. `DYNPROMPT` is the one that doesn't cross | See [saved-image metadata](png-metadata.md) |
| 13 | `IS_CHANGED` / `fingerprint_inputs` — **when you define one** | <span class="v v-yes">works</span> | Your "has anything changed?" check runs in the worker when it's awake and idle; any other time the answer is "yes, re-run". So a miss can only cost a recompute, never serve a stale result. A tensor input arrives as `None` there — same as native | Nothing. Write the `IS_CHANGED` you would write for vanilla ComfyUI. If it depends on a tensor input, remember ComfyUI hands it `None` there natively as well |
| 14 | `VALIDATE_INPUTS` / `validate_inputs` — the **body** | <span class="v v-yes">works, at execution</span> | The list of inputs you told ComfyUI to leave alone is honoured at submit time on the host. Your actual checking code runs in the worker right before your function, with exactly what ComfyUI would have shown it (widget values; linked inputs are `None`), and a `False` or a message becomes the node's error with your words in it. Async checks are awaited. The only difference: the "no" arrives when your node runs, not when you click Run, so nodes ahead of it run first | Nothing. Keep validating widget values; a linked input is `None` here as natively |

</div>

## Deliberately unsupported

These four are **decisions**, not gaps. Each has a written reason and a
stated condition under which it would be revisited, alongside four others,
on **[Deliberately unsupported](deliberately-unsupported.md)**. They are
listed here so the symptom is still findable from this page.

<div class="verdict-table wide-table num-col" markdown>

| # | Mechanism | Status | ELI5 | What to do |
|---|---|---|---|---|
| D1 | `async def` node functions | <span class="v v-partial">deliberate</span> | Your `async def` function is never awaited. It fails on first run with a message that says exactly that — it's not a serialization problem | Make the function synchronous. Run your own event loop inside it if you need one |
| D2 | Cancel (the Stop button) | <span class="v v-partial">deliberate</span> | Pressing Stop only lands while your node is reporting progress; otherwise nothing happens until the 600 s timeout kills the worker. The click is never wasted (the flag is read, not consumed) and your `except Exception` can't swallow it | Drive a `ProgressBar` in any long loop, and never wrap it in a bare `except Exception` |
| D3 | Progress v2 (`set_progress`) | <span class="v v-partial">deliberate</span> | `set_progress` either complains about a missing node id or silently goes nowhere. It's `async`, so it's stuck behind D1 | Use `comfy.utils.ProgressBar` for now |
| D4 | `PromptServer.instance.routes`, `prompt_queue`, `add_on_prompt_handler` | <span class="v v-partial">deliberate</span> | Registering HTTP routes, touching the prompt queue, or adding prompt handlers raises an error naming what *is* forwarded. Those things belong to the host's server and can't run in a worker. Most packs that import `server` do this (123 of 154) | Move route registration behind [`ROUTES`](register-nodes.md), which the host serves and forwards |

</div>

## The pattern behind the tables

`check_lazy_status`, `IS_CHANGED` and `VALIDATE_INPUTS` are the same shape:
a mechanism ComfyUI invokes **before** the node executes. Forwarding those to
a worker by spawning it would cold-start every isolated environment in the
prompt before a single node ran. `IS_CHANGED` escapes that by asking only a
worker that already exists (row 13); `VALIDATE_INPUTS` escapes it the other
way round — the host keeps the signature and records what it was handed, and
the body runs in the worker right before the function (row 14, and
[caching and validation](caching-and-validation.md)).

`check_lazy_status` is *not* in that category, which is why it *is*
forwarded: it fires for a node ComfyUI has already picked to execute, whose
worker is spawning anyway.

`send_sync`, previews, progress v2 and cancel share a different shape: a
worker can be *called*, but it cannot **originate** traffic to the browser
on its own. Inbound got an answer ([`ROUTES`](register-nodes.md)); previews
and `send_sync` now cross on the same callback channel; progress v2 stays
behind async (D3) and cancel stays cooperative-by-progress (D2).

## See also

- [The two classmethods](comfyui-caching-validation.md) — what `IS_CHANGED`
  and `VALIDATE_INPUTS` do upstream
- [comfy-env, caching and validation](caching-and-validation.md) — why
  neither is forwarded
- [The process boundary](process-boundary.md) — what crosses, mechanism by
  mechanism
- [Saved-image metadata](png-metadata.md) — hidden inputs in detail
