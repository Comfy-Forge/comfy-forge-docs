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
| 1 | Run a pack's `IS_CHANGED` / `VALIDATE_INPUTS` **body** | Both fire at validation time, once per node, **before anything executes** — for every node in the prompt, including ones that will be cache-skipped. Forwarding either cold-spawns every isolated environment on submit. And at validation upstream stubs every linked input to `None`, so a forwarded body would only ever see literal widget values anyway. The **signature** is reproduced, because that is what carries the exemptions | [caching and validation](caching-and-validation.md) | Upstream offered a way to validate without instantiating, or an isolated pack shipped a validate that genuinely cannot be expressed as an input |
| 2 | Forward `DYNPROMPT` | A live `DynamicPrompt` rather than data. It cannot mutate during a call, so a snapshot *would* be exact — but nothing ComfyUI ships consumes it, and forwarding means transcribing upstream's class into comfy-env's worker source, which drifts silently. Cost of not doing it: a node declaring it sees the same absent kwarg it always did | [saved-image metadata](png-metadata.md) | A real pack reads `DYNPROMPT` inside a worker. The work is understood and ~15 lines |
| 3 | Write a pack's `add_model_folder_path` into ComfyUI's global registry | The global feeds `/models`, the asset seeder, upload routing — and is snapshot-pushed wholesale into **every** worker. One pack's registration would appear in every other pack's process. Kept in a per-pack private registry instead; host-defined categories win | `metadata.py`, comment on `_PACK_FOLDER_REGISTRY` | A per-worker view of the registry, so a registration could be scoped |
| 4 | Cancel a node that is not reporting progress | The only worker→parent traffic during a call is the progress callback, so that is the only place the interrupt flag can be checked. A node that never reports is uncancellable until the timeout. Cooperative by progress, decided | [ADR-0018](adr/0018-worker-call-timeout.md) | The heartbeat frame ADR-0018 already names ships — a protocol change, not a patch |
| 5 | Wait longer than 600 s of silence | A worker that says nothing for ten minutes is treated as hung and killed. Long genuine work must report progress, which also makes it cancellable (row 4) | [ADR-0018](adr/0018-worker-call-timeout.md) | Same heartbeat |
| 6 | Isolate frontend JS, workflow templates, locales, subgraphs | One browser origin. Backend isolation buys nothing for code that runs in the page, and `WEB_DIRECTORY` works natively because the pack's `__init__.py` still runs in the host | [ADR-0031](adr/0031-frontend-javascript-isolation.md) | Never, short of iframes — recorded as deferred |
| 7 | Wrap the node call in `inference_mode` | `no_grad` instead. Only the node call is wrapped, so a model that lazily creates an `nn.Parameter` on its first forward creates it *inside* the context — and `inference_mode` stamps it an inference tensor, which breaks it on the next call. Upstream branches on the mode, so one model family produces different conditioning under each. There is a test | `_persistent_worker.py`, `tests/test_infer_mode.py`, pytorch#90882 | PyTorch resolves the lazy-parameter case |
| 8 | Support `async def` node functions | Every `async def execute` ComfyUI ships — all 41 — is an **API node**: an HTTP client with trivial dependencies and no reason to be isolated. Supporting them means a persistent event loop in the worker (a per-call loop would break sessions packs keep on `self`), a new concurrency surface bought for a population that does not exist yet. Fails loudly at first execution, naming the cause | [what survives isolation](node-contract-conformance.md) | A heavy-dependency pack ships an async node |

</div>

## The shapes these take

Three of the eight share one reason: **the mechanism fires before the node
executes** (rows 1, and half of 4 and 5's rationale). Forwarding anything at
that stage means starting a process to answer a question ComfyUI asks about
every node, every submit. comfy-env's whole cost model rests on a worker
starting once and answering many calls; validation-time forwarding inverts
it.

Two are **upstream owns the fix** (rows 2 and 7): transcribing a class that
is not ours, or working around a PyTorch limitation, would each be a copy
that drifts.

And two are **the naive fix is worse than the gap** (rows 3 and 8): writing
to the global registry leaks across workers; a per-call event loop breaks the
very packs it would serve.

## What is deliberately *not* on this page

The two most common questions, because they look like decisions and are not:

- **`IS_CHANGED` being absent** is a consequence of row 1, and it is now
  warned about at startup — but the *caching-forever* result is a gap, not a
  choice. The ladder that would soften it is in [Gaps](gaps.md).
- **`check_lazy_status` not being forwarded** is a plain gap. Unlike row 1,
  it fires for a node ComfyUI has already picked to execute, whose worker is
  spawning anyway. The cold-spawn objection does not transfer, and nobody
  has decided against it.
