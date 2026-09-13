# Gap inventory

*Everything ComfyUI does that comfy-env does not carry across the process
boundary, in one place. What breaks, how loudly, and whether anyone has
decided what to do about it.*
{: .subtitle }

*Last verified against ComfyUI `15b212cc` (2026-09-07) and comfy-env
`fe9ff74` (2026-09-12). Produced by five parallel audits reading ComfyUI first and
comfy-env second, then rechecked. Rows marked **[V]** were verified
independently after the audit reported them; **[A]** were reported with
`file:line` on both sides and not rechecked.*

## The pattern

Every gap has one shape:

> **comfy-env carries what flows through a node's arguments and return value,
> and drops — or silently copies — what flows through ComfyUI's process-global
> state or through live object identity.**

Two failure families follow. *Global state* — registries, hooks, interrupt
flags, log levels — is simply absent in a worker. *Object identity* — `is`,
`hash`, `id` — cannot survive a copy, so upstream logic keyed on it stops
matching without raising.

## Status key

| Code | Meaning |
|---|---|
| **fixed** | in the tree, with a test that fails without it |
| **decided** | an ADR says what to do; enforcement not yet built |
| **deliberate** | won't fix, reason recorded |
| **open** | nobody has decided |

---

## Silently wrong

The node runs, the result is subtly incorrect, nothing is logged.

| # | Gap | In plain English | Code | Docs | ✓ |
|---|---|---|---|---|---|
| 1 | Hidden inputs stripped → no PNG metadata | An isolated save node wrote images with no workflow chunk | **fixed** | [metadata](png-metadata.md) | V |
| 2 | Host MODEL passed into a worker crosses as a full pickle copy | The node "works" but every patch lands on a copy the host never sees; VRAM doubles | **decided** | [ADR-0040](adr/0040-models-never-cross.md) | V |
| 3 | Hooks lost, aliasing destroyed, `is_clone` disagrees — consequences of that copy | Hook LoRAs silently do nothing; a MODEL wired twice arrives as two objects | **decided** | [ADR-0040](adr/0040-models-never-cross.md) | A |
| 4 | Cancel was swallowable — `RuntimeError` where upstream uses `BaseException` | Cancel did nothing if the node had a `try/except`, and the host forgot it asked. The worker's `_InterruptedError` now derives from `BaseException`, and the host reads the interrupt flag with `processing_interrupted()` instead of consuming it | **fixed** | [conformance](node-contract-conformance.md) | V |
| 5 | `logging.info()` dropped in workers | Every INFO line vanished; WARNING kept working, which is why it looked fine | **fixed** | [logging](logging-approach.md) | V |
| 6 | `models_dir` never crossed | A pack carving out its own model folder pointed at the wrong root on `--models-directory` | **fixed** | [model paths](folder-paths.md) | V |
| 7 | Seven attention flags not mirrored | `--use-sage-attention` and `--use-flash-attention` now cross as one resolved backend (`mirrored_args.py`'s `ATTENTION_KEY`), so a worker follows the host onto sage or flash. The other seven do not: `--use-split-cross-attention`, `--use-quad-cross-attention`, `--use-pytorch-cross-attention`, `--use-ck-attention`, `--disable-xformers` and the two upcast flags. You picked a low-VRAM attention mode; the worker uses the default and OOMs — in the pack only | open | [attention](attention.md) | V |
| 8 | A dozen more startup flags not mirrored — allocator, compiler, Triton, DirectML | The worker starts with factory settings for anything outside dtype and memory | open | [CLI args](args-mirror.md) | A |
| 9 | `EXTRA_PNGINFO` mutation doesn't travel back | A node writes a note for a downstream saver; the saver never sees it. **This is the field's common use**: 84 of 505 surveyed packs declare it, and every writer writes for a downstream node — `mikey_nodes.AddMetaData` is named for it | open | [metadata](png-metadata.md) | V |
| 10 | `prompt_id` not forwarded | Isolated API nodes lose their `Comfy-Job-Id` header | open | [metadata](png-metadata.md) | A |
| 11 | `cls.hidden` is `None` when a node declares nothing | Upstream gives an empty holder whose attributes read `None`; ours raised. The clone is now built on every call, as upstream does | **fixed** | [conformance](node-contract-conformance.md) | V |
| 12 | `GraphBuilder.set_default_prefix` is parent-only | Two isolated expanding nodes mint colliding ids. Latent | open | [metadata](png-metadata.md) | A |
| 13 | Worker debug log in shared `/tmp`, five sites | One world-readable file per machine that grows forever | open | [logging](logging-approach.md) | V |
| 14 | `sys.stdout.write` → `DEVNULL`; `print(end=)` dropped | A library writing to the stream object directly goes into a black hole | open | [logging](logging-approach.md) | V |
| 15 | `on_load()` runs in the scan process only | A V3 pack's setup happens in a throwaway process; at run time it's gone. Looks intermittent | open | — | A |
| 16 | `RAMPressureCache` scores isolated outputs at 0.05 bytes | ComfyUI's default cache thinks pack outputs are free; host RAM fills and it evicts the wrong things | open | — | A |
| 17 | `lock_class` never applied | Upstream stops a node scribbling on its class; in a worker the scribbles stuck for hours. The worker now locks the clone with upstream's own `make_locked_method_func` | **fixed** | [conformance](node-contract-conformance.md) | V |
| 18 | `--comfy-api-base` not mirrored | Host on staging, isolated API nodes on production, same token | open | — | A |
| 19 | `set_cudnn_benchmark()` not re-applied | Upstream resets a cuDNN flag after loading nodes because packs flip it; the worker doesn't | open | — | A |
| 20 | Worker cwd is the pack directory | Relative paths resolve somewhere unexpected | open | — | A |
| 21 | No `SIGTERM` handler, no session | `docker stop` never tells the workers; they hold VRAM until the socket dies, or forever mid-call | open | — | A |
| 22 | `no_grad` vs `inference_mode` | Upstream branches on the mode; one model family produces different conditioning | deliberate | [deliberately unsupported](deliberately-unsupported.md) row 6 | A |
| 48 | `self.cache[k] = v` never returned to the host | The worker fingerprinted inbound state *after* the call, against the very dict the node had just mutated: identical on both sides, nothing shipped. Rebinding (`self.n += 1`) worked; in-place mutation of a dict or list, the usual shape of a node cache, did not | **fixed** | [conformance](node-contract-conformance.md) | V |
| 49 | `__init__` never re-ran after a worker restart | The parent's seed flag was set once and never cleared, so a fresh process got the old markers and no `__init__`; a node holding a thread pool or lock on `self` raised "its value is gone and will be recomputed" on every call, and nothing recomputed it. `crt-nodes`' image crawler is the idiomatic case. The worker now keeps its own book of seeded instances | **fixed** | [conformance](node-contract-conformance.md) | V |
| 50 | A node whose `INPUT_TYPES` raised registered with zero widgets | It looked healthy in the menu, and a workflow saved with it lost every widget value, because the frontend serialises a registered node from its live widgets. It is now a *missing* node, which keeps saved data verbatim | **fixed** | [conformance](node-contract-conformance.md) | V |

## Fails loudly

Bad, but visible.

| # | Gap | In plain English | Code | Docs | ✓ |
|---|---|---|---|---|---|
| 23 | Host `VAE` / `CONTROL_NET` can never enter a worker | Both have instance lambdas that can't pickle; the error tells the author to write a serializer for a core type | **decided** | [ADR-0040](adr/0040-models-never-cross.md) | V |
| 24 | `import nodes` resolves to the pack's `nodes/` | The path list was applied backwards, so the pack dir shadows ComfyUI's `nodes.py` | open | — | V |
| 25 | `<ComfyUI>/comfy` not on the worker's path | Old packs do `import model_management` bare; upstream allows it, the worker doesn't | open | — | V |
| 26 | `init_extra_nodes()` never runs | `NODE_CLASS_MAPPINGS` has ~65 core entries; all 138 extras are missing. `KeyError` on `"SamplerCustom"` | open | — | V |
| 27 | Native `@PromptServer.instance.routes` at import | `import server` failed in a lean worker env and **every node in the pack vanished**. The import now succeeds; the route decorator raises an `AttributeError` naming what is forwarded and pointing at `ROUTES` | deliberate | [deliberately unsupported](deliberately-unsupported.md) row 8 | V |
| 28 | Lazy inputs / `check_lazy_status` | Forwarded when the author defined one, so the taken branch is computed. A node declaring `lazy` *without* one still gets `None` — and so does plain ComfyUI, whose own default is unreachable; that half is upstream's | **fixed** | comfy-env `4eece2c` | V |
| 29 | `async def` node functions | A coroutine reaches the serializer | open | partial: [ADR-0001](adr/0001-process-isolation-via-persistent-subprocess-workers.md) | A |
| 30 | `cls.SCHEMA` was `None` in the worker | Every `NodeOutput(expand=…)` died with an error naming nothing about expansion. The worker calls the node's inherited `GET_SCHEMA()` before dispatch when `SCHEMA` is `None`; a test pins upstream's contract (an unregistered V3 class still dereferences an unset `SCHEMA`) and that an expand graph survives a real worker | **fixed** | [conformance](node-contract-conformance.md) | V |
| 31 | `validate_inputs(cls, **kwargs)` lost its blanket exemption | ComfyUI rejected values the node would have accepted; the node never ran. The scan now records whether the author's validate declares a catch-all, and the stand-in emits `**kwargs` only when it did | **fixed** | [conformance](node-contract-conformance.md) | V |
| 32 | `send_progress_text` has no crossing | The API for writing status into a node's body; core's own mesh and 3D nodes use it. Forwarded; the host packs the TEXT frame with upstream's own code | **fixed** | [conformance](node-contract-conformance.md) | V |
| 33 | `PromptServer.instance` is `None` | `client_id` now answers with the host's own, shipped per call. `prompt_queue`, `last_node_id` and the rest raise an `AttributeError` that says so | **fixed** | [conformance](node-contract-conformance.md) | V |
| 34 | `WorkerError` masks the real exception type | Dialog says `WorkerError`, not `FileNotFoundError`; anything keyed on the type misfires | open | [exceptions](exceptions.md) | A |
| 35 | `dpm_fast` / `dpm_adaptive` SAMPLER objects | Two of 32 samplers are closures and can't pickle | open | — | A |
| 36 | Windows: host `PATH` replaced wholesale | `ffmpeg not found` from a pack that works un-isolated | open | — | A |
| 37 | `NodeOutput` matched by name, not `isinstance` | A pack subclassing it isn't recognised | open | — | A |

## Nothing happens

| # | Gap | In plain English | Code | Docs | ✓ |
|---|---|---|---|---|---|
| 38 | Progress v2 (`set_progress`, `ProgressRegistry`) | The API upstream tells authors to migrate *to*. No bar, no error | open | — | A |
| 39 | Latent / `ProgressBar` previews | The preview during sampling stayed blank. The worker fits the preview to its `max_size` the way `server.send_image` does, ships it on the progress callback (1 MiB cap for frames with no `max_size`, above which the tick still goes and the image is dropped) and the host rebuilds it for `PROGRESS_BAR_HOOK`; tests drive ComfyUI's own `ProgressBar` from a worker with a 40x30 frame and a 2048x2048 one and check the tuple arrives fitted, and that a huge frame with no `max_size` is dropped while the tick arrives | **fixed** | [conformance](node-contract-conformance.md) | V |
| 40 | `send_sync` | A pack's own websocket events never left the worker; its JS was fine, so it looked like a frontend bug. Forwarded on the callback channel; `sid=None` broadcasts as upstream | **fixed** | [conformance](node-contract-conformance.md) | V |
| 41 | `ComfyExtension.get_routes()` | V3's proper route registration is never called. Endpoints 404 | open | — | A |
| 42 | Sampler / scheduler registration | Registers into the worker's list; the host's dropdown never shows it | open | — | A |
| 43 | `hook_breaker_ac10a0` never runs | Upstream actively undoes one monkeypatch every few seconds; the worker doesn't | open | — | A |
| 44 | `log_startup_warning` lands in the worker's own list | Never appears in the end-of-startup replay block that exists so warnings aren't buried | open | — | A |
| 45 | sqlite session unavailable | ComfyUI's database isn't reachable from a worker | open | — | A |
| 46 | `add_on_prompt_handler`, `node_replacement.register`, `Caching.register_provider` | Registered into worker-local state; no effect, no error. `add_on_prompt_handler` now raises an `AttributeError` naming what is forwarded; the other two are unchanged | open | [deliberately unsupported](deliberately-unsupported.md) row 8 for the first | A |
| 47 | `PROGRESS_BAR_HOOK` absent from ADR-0024's loan book | We depend on it from both sides; an upstream rename kills cancel *and* progress with green CI | open | — | A |
| 51 | The metadata scan had no timeout | A pack that hung at import held ComfyUI's startup forever with nothing printed. A plain timeout would not have fixed it: under `pixi run` the scanning Python is a grandchild, and on Windows the parent then blocks on its pipe. The whole process tree is now killed after `COMFY_ENV_SCAN_TIMEOUT` and the log names the node | **fixed** | [conformance](node-contract-conformance.md) | V |
| 52 | Live dropdowns never fired for `("COMBO", {"options"})` inputs | Every V3 `io.Combo.Input` (and some core V1 nodes) uses that shape; every site recognised a combo only by a list in first position. V3 model dropdowns stayed at the scan's listing across restarts and a new file was rejected as not-in-list. Both shapes are recognised now | **fixed** | [dynamic combos](live-dropdowns.md) | V |
| 53 | The 600 s timeout killed only the `pixi` wrapper | The Python grandchild lived on with its VRAM, parent pid 1, invisible to the reaper. Workers spawn in their own group; one `_kill_tree()` serves all four kill sites; the reaper decides by the host pid in the temp-dir name | **fixed** | [worker lifecycle](worker-lifecycle.md) | V |
| 54 | The last call's tensors stayed pinned until the next call | A keeper held every call's inputs and results for 60 s and pruned only on the next keep: indefinitely while idle. It protected nothing (torch's CUDA IPC has a cross-process refcount). Gone; the one load-bearing keep is released at end of call | **fixed** | [the process boundary](process-boundary.md) | V |
| 55 | V1 proxies dropped every class attribute but seven | No `DESCRIPTION`, `OUTPUT_TOOLTIPS`, `SEARCH_ALIASES`, `DEPRECATED`, `EXPERIMENTAL`, `NOT_IDEMPOTENT` for ~155 packs. The scan sweeps every uppercase JSON-shaped attribute; a canary walks upstream's `node_info` | **fixed** | [conformance](node-contract-conformance.md) | V |
| 56 | `VALIDATE_INPUTS` bodies never ran | ~200 real validate bodies in ~80 packs; a rejected value failed deeper with a traceback. The host stand-in records what it was handed; the worker runs the real body right before the function | **fixed** | [caching and validation](caching-and-validation.md) | V |

## Deliberate, and recorded

Eight gaps are decisions, not defects, and each has a written reason and a
stated condition under which it would be revisited. They live on their own
page so the reasoning is not buried in a list of things that are simply
missing: **[Deliberately unsupported](deliberately-unsupported.md)**.

In short: `DYNPROMPT`,
`add_model_folder_path` into the global registry, cancel without progress,
the 600 s silence timeout, frontend isolation, `async def` nodes, and the
host's own server surface (`routes`, `prompt_queue`, prompt handlers).

## Tally

| | |
|---|---|
| Distinct gaps | **56** |
| Fixed | 22 |
| Decided, enforcement pending | 3 (one ADR) |
| Deliberate, recorded | 2 in these tables (rows 22 and 27); the full list of eight lives on [its own page](deliberately-unsupported.md) |
| Open | **29** |

## Where isolation genuinely does not apply

Recorded so nobody re-audits them. A pack's **frontend JS, workflow
templates, `locales/` and `subgraphs/`** are read off its real directory
under `custom_nodes/`, which comfy-env never moves; the pack's `__init__.py`
still runs in the host. **Asset enrichment** under `--enable-assets` is a
host-side transform over the returned `ui` dict. **`/models`, `/view`,
`/userdata`, `/settings`, `/history`, `/queue`** are host-side reads the
browser makes directly. `ExecutionBlocker`, `INPUT_IS_LIST` /
`OUTPUT_IS_LIST`, cache-key identity, `rawLink` and the whole V3
dynamic-input family were checked and are faithful.

## How this list was made

By hand, it missed things — the hidden-inputs bug was found by accident. So
the second pass derived ComfyUI's surface mechanically: every attribute the
executor reads off a node class (25, by AST), every module-level mutable
global (the state that cannot cross by definition), and the full CLI-args
namespace (107 dests, enumerated). Then five audits, one per subsystem, each
reading ComfyUI before comfy-env so none inherited comfy-env's framing.

The list will rot. The mechanism to keep it honest — re-deriving the surface
in CI and failing when upstream grows a symbol nobody has classified — is the
same shape as [ADR-0024](adr/0024-upstream-interface-contract.md)'s loan book,
and is not built.
