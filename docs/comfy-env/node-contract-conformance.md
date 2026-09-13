# What survives isolation

*ComfyUI calls about eight things on a node class during a run.
Some of them still work once the node lives in another process, and some of them do not.*
{: .subtitle }

*Audited against ComfyUI `15b212cc` (2026-09-07). Each row was traced on both
sides of the boundary.*

## Working
<div class="verdict-table wide-table num-col" markdown>

| # | Mechanism | ELI5 |
|---|---|---|
| 1 | `check_lazy_status`| A node can mark inputs as *lazy*: ComfyUI skips computing them up front and asks the node which ones it actually needs, then runs only those. Under isolation the question goes to the worker, which is started if needed since the node is about to run anyway, and the node's own answer comes back, so the unused branch stays uncomputed, same as native. |
| 2 | `__init__` and `self.x` (V1) | **V1 nodes only** (a V3 node has no `self`). The host carries `self` between calls and the worker uses it, so attributes survive with their types, including in-place edits like `self.cache[k] = v`. If the worker crashes or restarts, the node reinitializes: `__init__` runs again in the new process, the same as ComfyUI itself restarting. |
| 3 | `PromptServer.instance`: `send_sync`, `send_progress_text`, `client_id` | ComfyUI keeps a websocket open to the browser and Python can push a message down with `send_sync(event, data)` or `send_progress_text`, `client_id`. Packs use it to talk to their own JavaScript. Comfy-env workers forward the message to the host onto the real socket, so `from server import PromptServer` works and so does the call. Anything else on the server raises an error saying so (D4). |
| 4 | `ProgressBar` **preview** argument | Preview frames from an isolated sampler show up in the browser. The worker shrinks each frame to the preview's `max_size` before sending it, exactly as ComfyUI's server does before it reaches the browser, so a full-resolution frame costs a thumbnail on the wire. A frame with no `max_size` crosses at full size, as it reaches the browser natively. |
| 5 | `cls.SCHEMA` (V3) | `cls.SCHEMA` is a V3 node's description of itself (inputs, outputs, flags such as `enable_expand`). ComfyUI fills it in once at registration and reads it at run time, for example to check `enable_expand` when a node returns a subgraph to run in its place. A worker never registers nodes, but comfy-env workers fill it in themselves before the first call. |
| 6 | `lock_class` (V3) | ComfyUI calls a V3 node's `execute` with a throwaway, locked copy of its class, so `cls.x = 1` raises. The worker runs your `execute` through that same lock, so a pack cannot rely on writes that would fail natively. |
| 7 | Class attributes on **V1** nodes | The flags ComfyUI and frontend extensions read off a node class (`CATEGORY`, `OUTPUT_NODE`, `DEPRECATED` and any other simple uppercase attribute) are copied onto the host-side stand-in, so the browser sees them as it would natively. |
| 8 | `FUNCTION` dispatch, list batching, return shapes | Calling the node, batching over lists, and every return shape behave as native. The only cost: a batch of N items is N trips over the socket. |
| 9 | `INPUT_TYPES` | Dropdown lists refresh whenever the pack's worker is awake, even while it is running one of its nodes, because the node's own `INPUT_TYPES` is re-run there on a side thread. While the worker is cold you see the list from startup. See [dynamic combos](live-dropdowns.md). |
| 10 | `folder_paths`: the whole model-path registry | Every model-path lookup resolves against the host's real folders, so files land where you expect. See [model paths](folder-paths.md). |
| 11 | The metadata scan itself | If a pack hangs while being scanned at startup, it gets killed after `COMFY_ENV_SCAN_TIMEOUT` (300 s) and the log names the node it was on. If a node's `INPUT_TYPES` throws, that node shows as a normal missing node — your saved widget values survive, and you see the real error when you queue. That scan isn't cached, so a one-off cause heals on the next start. For a hang, lower the timeout while you look for the culprit. |
| 12 | Hidden inputs (`PROMPT`, `EXTRA_PNGINFO`, `UNIQUE_ID`, the API credentials) | The hidden extras ComfyUI passes to nodes (the prompt, the workflow, your node id, API keys) all arrive, whatever you named the parameter, so isolated save nodes can make use of them and write the workflow into the PNG or whatever. `DYNPROMPT` is the one that doesn't cross. See [saved-image metadata](png-metadata.md). |
| 13 | `IS_CHANGED` / `fingerprint_inputs` | Your "has anything changed?" check runs in the worker when it's awake, busy or not; when it's cold the answer is "yes, re-run". So a miss can only cost a recompute, never serve a stale result. A tensor input arrives as `None` there, same as native. |
| 14 | `VALIDATE_INPUTS` / `validate_inputs` | A node can define `VALIDATE_INPUTS` to check its own widget values before anything runs, for example that a file exists or a number is in range. It returns `True` to accept or a message to reject, and ComfyUI shows that message to the user. Its parameter names also tell ComfyUI which inputs to skip its built-in min/max and dropdown checks for. Under isolation the host keeps the parameter names, so the skips still apply at submit, and the check itself runs in the worker right before your function with the same values ComfyUI would have given it (linked inputs are `None`, as natively). Once the worker for the node exists, a reject arrives at the click, in the submit dialog, as natively; on the very first, cold run it arrives when the node runs, as the node's error with your message, after the nodes ahead of it. Both carry upstream's wording, `Custom validation failed for node: <your message>`. |

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
