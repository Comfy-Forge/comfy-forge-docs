# Deliberately unsupported

*Things comfy-env could carry across the process boundary and has decided
not to. Each one has a reason, the reason is written down here rather than
in a code comment, and each one says what would change the decision.*
{: .subtitle }

The companion to [Gaps](gaps.md), which lists what is missing by accident or
not yet decided. Nothing on this page is a bug. Several are the *opposite* of
a bug: the naive fix exists, is small, and would make things worse.

<div class="num-col" markdown>

| # | We do not | Because | Recorded | Would change if |
|---|---|---|---|---|
| 1 | Forward `DYNPROMPT` | A live `DynamicPrompt` rather than data. It cannot mutate during a call, so a snapshot *would* be exact — but nothing ComfyUI ships consumes it, and forwarding means transcribing upstream's class into comfy-env's worker source, which drifts silently. Cost of not doing it: a node declaring it sees the same absent kwarg it always did | [saved-image metadata](png-metadata.md) | A real pack reads `DYNPROMPT` inside a worker. The work is understood and ~15 lines. Note that the trigger is closer than "nobody": packs in the survey already declare it — rgthree-comfy, comfyui-inspire-pack and comfyui-easy-use among them — so it only takes one of those being isolated |
| 2 | Copy a pack's `add_model_folder_path` back into ComfyUI's global registry | The global feeds `/models`, the asset seeder, upload routing — and is snapshot-pushed wholesale into **every** worker. One pack's registration copied back would appear in every other pack's process. So the call runs against the worker's own `folder_paths` copy and stays there: the pack's nodes resolve the path, the host's listings do not see it | [folder paths](folder-paths.md) | A per-worker view of the registry, so a registration could be scoped |
| 3 | Cancel a node that is not reporting progress | A worker originates four kinds of traffic to the parent during a call — the progress callback, `send_sync`, `send_progress_text` and the VRAM-budget request — and only the progress handler reads the interrupt flag; the others do the one thing they were sent for. So a node that never reports progress is uncancellable until the timeout. Cooperative by progress, decided | [ADR-0018](adr/0018-worker-call-timeout.md) | The heartbeat frame ADR-0018 already names ships — a protocol change, not a patch |
| 4 | Wait longer than 600 s of silence | A worker that says nothing for ten minutes is treated as hung and killed. Long genuine work must report progress, which also makes it cancellable (row 4) | [ADR-0018](adr/0018-worker-call-timeout.md) | Same heartbeat |
| 5 | Isolate frontend JS, workflow templates, locales, subgraphs | One browser origin. Backend isolation buys nothing for code that runs in the page, and `WEB_DIRECTORY` works natively because the pack's `__init__.py` still runs in the host | [ADR-0031](adr/0031-frontend-javascript-isolation.md) | Never, short of iframes — recorded as deferred |
| 6 | Wrap the node call in `inference_mode` | `no_grad` instead. Only the node call is wrapped, so a model that lazily creates an `nn.Parameter` on its first forward creates it *inside* the context — and `inference_mode` stamps it an inference tensor, which breaks it on the next call. Upstream branches on the mode, so one model family produces different conditioning under each. There is a test | `_persistent_worker.py`, `tests/test_infer_mode.py`, pytorch#90882 | PyTorch resolves the lazy-parameter case |
| 7 | Support `async def` node functions | Every `async def execute` ComfyUI ships — 249 of them, across 41 files, all under `comfy_api_nodes/` — is an **API node**: an HTTP client with trivial dependencies and no reason to be isolated. Supporting them means a persistent event loop in the worker (a per-call loop would break sessions packs keep on `self`), a new concurrency surface bought for a population that does not exist yet. Fails loudly at first execution, naming the cause | [what survives isolation](node-contract-conformance.md) | A heavy-dependency pack ships an async node |
| 8 | Forward `PromptServer.instance.routes`, `prompt_queue`, `add_on_prompt_handler` | These are the host's server, not a node's. A route handler runs inside the host's aiohttp loop; the queue and prompt handlers are the host's scheduling state. Forwarding any of them means a second RPC design, and `ROUTES` already covers the one use that has a node-shaped answer. Of the 154 surveyed packs that import `server`, 123 register routes and 7 add prompt handlers; all of them get an `AttributeError` naming what *is* forwarded (`send_sync`, `send_progress_text`, `client_id`) instead of a silent no-op or an aiohttp import error | `server_stub.py`, [what survives isolation](node-contract-conformance.md) | A pack whose routes cannot be expressed as `ROUTES` — a websocket upgrade, a streaming response |

</div>

## The shapes these take

Two of the eight share one reason: **the only channel is the one the node
opens** (rows 3 and 4): a worker that never reports progress is a worker the
host cannot reach mid-call, so cancel and the silence timeout are cooperative
by design.

Two are **upstream owns the fix** (rows 1 and 6): transcribing a class that
is not ours, or working around a PyTorch limitation, would each be a copy
that drifts.

And three are **the naive fix is worse than the gap** (rows 2, 7 and 8):
writing to the global registry leaks across workers; a per-call event loop
breaks the very packs it would serve; a stand-in `routes` that collected
handlers nobody would ever call would let a pack import and then fail its
first request with no clue why.

## What is deliberately *not* on this page

The three most common questions, because they look like decisions and are not:

- **`VALIDATE_INPUTS`** was row 1 of this page until 2026-09-12. The
  objection was real — forwarding the body at submit means spawning every
  isolated environment, or talking to one that happens to be warm, on the
  HTTP loop — and the answer was to run it in the other order: the host's
  stand-in keeps the signature and records what it was handed, and the
  body runs in the worker right before the function. See
  [caching and validation](caching-and-validation.md).
- **`IS_CHANGED`** is forwarded, over the same ladder as
  [live dropdowns](live-dropdowns.md): the pack's fingerprint runs in its
  worker when that worker is alive and idle, and every other case answers
  *changed*. It used to be dropped with a startup warning; that is gone.
- **`check_lazy_status`** is forwarded when the author defined one. It
  fires for a node ComfyUI has already picked to execute, whose worker is
  spawning anyway, so the cold-spawn objection never applied. What
  is *not* done — and this one is a decision — is synthesising a default for
  a node that declares `lazy` without writing the method: plain ComfyUI hands
  that node `None` too, because upstream's own default is unreachable, and
  making isolation more correct than upstream is the same author-trap as
  `lock_class` inverted.
