# The multiprocessing issue
*The rest of this page assumes you know how native ComfyUI manages memory.
**[If you don't, read this page first](comfyui-memory.md)**.*
{: .subtitle }

ComfyUI manages RAM and VRAM to optimize for speed and stability on all kinds
of hardware, also making use of [comfy-aimdo](comfyui-aimdo.md) for paging.

- streams weights per layer when a
model does not fit
- holds back a reserve for work it cannot size in
advance
- pins host RAM so transfers are fast
- runs paging through comfy-aimdo
- caches what every node produced so a re-run skips the work.
- ...

It is good code, well tested and runs on all operating systems and hardware ranging from a 2011 laptop to an H100 server.
We have tried to maintain as much of the native memory management as possible through several strategies, without doing any patching or using any unmaintanable or unclean tricks.

## An inventory of ComfyUI memory management

Unfortunately it currently assumes that there is ever only exactly *one* process.

comfy-env's isolated nodepacks run in separate processes, on the same
GPU/RAM, and no process can see any other's allocations directly.
Nothing outside a process can free that process's memory.

The host has to be able to ask, and the only place ComfyUI asks anything is the list it walks in
`free_memory`. So we place one object of ours in that list per worker model.
When ComfyUI evicts it, it forwards the request over IPC
and the worker does the real unload.

We truly dislike this design but at the moment upstream exposes no interface for registering memory held by
another process, so the stand-in is how the host reaches a worker's models
at all.

An upstream interface would remove that trick but not the need for
worker-specific logic, because each worker
[keeps some RAM for its interpreter and torch, and some VRAM for its CUDA context](process-footprint.md),
a fixed cost per process that no model ledger describes.

## What survives isolation

Every memory decision ComfyUI makes, ranked by how often it fires times how
many bytes it moves times how bad it is when a subprocess is invisible to
it, with whether it reaches a worker today. Each mechanism links to the page
that owns it: [the stand-in](stand-in.md) for everything that passes through
the fake model, [admission and the reserve](admission.md) for the load time
arithmetic, [inside a worker](worker-memory.md) for what runs in the pack's
own process, and [what only upstream can fix](upstream-ask.md) for the rest.

<div class="verdict-table wide-table num-col" markdown>

| # | Mechanism | Today | ELI5 |
|---|---|---|---|
| 1 | [`free_memory` eviction ladder](stand-in.md#row-1) | <span class="v v-yes">yes</span> | When the card is short, ComfyUI walks its list of loaded models and tells them to leave, cheapest first. The stand-in comfy-env registers for each worker model is in that list, so the worker is told and unloads. |
| 2 | [`load_models_gpu` admission](admission.md#row-2) | <span class="v v-yes">yes</span> | Before a load, ComfyUI adds up what it will need and checks free VRAM. On Linux free VRAM is device wide, so what workers hold is already counted; on Windows comfy-env publishes a reserve so that it is. |
| 3 | [The reserve (`EXTRA_RESERVED_VRAM`, `--reserve-vram`)](admission.md#row-3) | <span class="v v-yes">yes</span> | VRAM ComfyUI keeps untouched for work it cannot size in advance. comfy-env forwards the same reserve to every worker and to the pager, and raises it only where the host cannot see workers. |
| 4 | [Admission, the activation guess](admission.md#row-4) | <span class="v v-no">no</span> | The admission sum is only as good as its parts, and one part is a guess ComfyUI hardcodes. For a controlnet, the "how much scratch memory will this need while running" term returns zero, while in reality the control image is kept on the card at full pixel resolution for the whole run. Worse, a ControlLora builds a whole second network on the card without going through admission at all, so a model appears that nothing registered and nothing can evict. None of this is comfy-env's doing: the host under books by exactly the same amount as a worker would, because the number comes from upstream and comfy-env never computes it. The consequence under isolation is the same as native, a load admitted with less room than it really needs; it is listed here so nobody expects comfy-env to have fixed it. |
| 5 | [`get_free_memory`](admission.md#row-5) | <span class="v v-yes">yes</span> | "How much room is left", read the same way by both sides. The stand-in's size never enters it. |
| 6 | [Free memory as a batch sizer](upstream-ask.md#row-6) | <span class="v v-no">no</span> | Free VRAM is not only used to decide whether a model fits; samplers and the VAE also divide it by an estimate to decide how many images to process at once, several times per step, and that decides gigabytes of activations. The number they divide is "free VRAM plus everything the pager could still evict from this process". That second term is strictly local: a worker cannot know what the host could evict, and the host cannot know what a worker could. So on Linux each side sees what the other holds and shrinks its batches for it, but neither ever grows because the other could make room, which is slower than native, never unsafe. Two of upstream's own sizers make it worse regardless of comfy-env: one reads the raw free figure and under batches any paged model, and the tiled VAE fallback reads total VRAM rather than free and grows its tile until the estimate fits, so two processes each grow into most of the same card. Nothing on the process boundary can fix a decision made inside a sampler loop. |
| 7 | [Dtype chosen from the size of the whole card](upstream-ask.md#row-7) | <span class="v v-no">no</span> | When a model is loaded, ComfyUI decides whether to keep its weights small (fp8) or upcast them to fp16, and that decision is worth twice the whole weight set. It decides by comparing the model's size with 88 percent of the card's total memory, not with what is free. So nothing a sibling process holds enters the decision and no reserve can shrink it: a host and a worker on one 48 GB card, each loading a 12B fp8 checkpoint, each conclude there is room and each upcast, 48 GB of weights before a single activation. On a 24 GB card they both correctly decide there is no room, by luck of the arithmetic. This is the inverse of row 6: there both sides shrink for each other, here both grow. The gates fire in the default configuration, pager or not, and they run inside the loader where the process boundary has no say. Both sides pick exactly what native would have picked; the trouble is that native never had a sibling. |
| 8 | [cgroup RAM accounting](upstream-ask.md#row-8) | <span class="v v-no">no</span> | Inside a Docker container the machine may have 256 GB of RAM while the container is allowed 32 GB. Since 2026-08-27 ComfyUI reads the container's limit and sizes everything RAM related against it: how much RAM it may pin for fast transfers, when the results cache must start evicting, the Windows swap gate. comfy-env's own readings still come from `psutil`, which reports the whole machine, so inside a container the host and comfy-env are sizing against two different numbers. And the deeper problem has no fix from either side yet: the host and every worker live in the same container and share the one budget, but each process independently sizes its pin ceiling and its cache headroom against the full budget, as if it were alone. Five processes each believing they may pin 2 GB less than 32 GB is not a 32 GB budget. |
| 9 | [`unload_all_models` and the Free button](stand-in.md#row-9) | <span class="v v-yes">yes</span> | The button reaches the stand-in and the worker releases everything. |
| 10 | [Cast buffers](worker-memory.md#row-10) | <span class="v v-partial">partial</span> | Per process scratch VRAM for the layer being copied. Every process pays for its own; comfy-env books the incoming load's share in the reserve, but they are never shared. |
| 11 | [Partial load budget (`lowvram_model_memory`)](worker-memory.md#row-11) | <span class="v v-no">no</span> | "Load only this much of the model" never reaches a worker: the stand-in is not a partially loadable model on either path. |
| 12 | [`LoadedModel` size questions](stand-in.md#row-12) | <span class="v v-partial">partial</span> | How big is it, how much is on the card. The stand-in answers from what the worker measured; some reads assume internals the stand-in only fakes. |
| 13 | [aimdo headroom](admission.md#row-13) | <span class="v v-yes">yes</span> | The pager's own reserve. comfy-env forwards it at runtime as the published reserve changes. |
| 14 | [Model compiler, malloc graph and CUDA graphs](upstream-ask.md#row-14) | <span class="v v-no">no</span> | A sibling process faulting during another's CUDA graph capture is uncoordinated. The sharpest edge in this table. |
| 15 | [The inactive cache tier](worker-memory.md#row-15) | <span class="v v-yes">yes</span> | Results cached in RAM and drained at every node. Worker results sit in the host's cache like any other entry. |
| 16 | [Per-layer fault and aimdo's C-side eviction](worker-memory.md#row-16) | <span class="v v-yes">yes</span> | The pager pages each process's weights by the pressure it senses itself. Works, with no coordination possible from Python. |
| 17 | [`model_unload`, partial versus full](stand-in.md#row-17) | <span class="v v-yes">yes</span> | The stand-in implements partial unload and returns the bytes actually moved, so ComfyUI's arithmetic stays right. |
| 18 | [OOM branch in `execution.py`](worker-memory.md#row-18) | <span class="v v-yes">yes</span> | A worker's out of memory error crosses as the real torch class, so ComfyUI's top level handling runs as native. |
| 19 | [The OOM retry ladder](upstream-ask.md#row-19) | <span class="v v-no">no</span> | Eleven sites retry smaller on OOM, each shrinking its own process. A worker retries alone and the host never learns the pressure came from it. |
| 20 | [The activation estimate](admission.md#row-20) | <span class="v v-yes">yes</span> | A per model constant, read as native, now sizing the reserve on two processes instead of one. |
| 21 | [`/free`](upstream-ask.md#row-21) | <span class="v v-partial">partial</span> | The unload half reaches the stand-in. The cache reset half is host only, which is right, since worker results live in the host cache. |
| 22 | [`cleanup_models` prune](stand-in.md#row-22) | <span class="v v-yes">yes</span> | Dead entries are pruned; the stand-in carries the weakref the prune expects. |
| 23 | [Async offload streams](worker-memory.md#row-23) | <span class="v v-yes">yes</span> | The stream count is mirrored to workers and read live when booking cast buffers. |
| 24 | [`cudaMallocAsync` as the default allocator](worker-memory.md#row-24) | <span class="v v-yes">yes</span> | Mirrored. It is also why a GPU tensor crossing the boundary is copied rather than shared (see [zero-copy CUDA transfer](zero-copy-ipc.md)). |
| 25 | [Prompt boundary signals](upstream-ask.md#row-25) | <span class="v v-yes">yes</span> | The prompt id is read; upstream merged the provider and comfy-env registers none. |
| 26 | [`model_load` and the finalizer tripwire](stand-in.md#row-26) | <span class="v v-yes">yes</span> | The stand-in is inserted into the list directly and satisfies what the finalizer checks. |
| 27 | [Dirty mmap bounce](worker-memory.md#row-27) | <span class="v v-yes">yes</span> | Mirrored. Two workers loading the same file share the page cache instead of each copying it. |
| 28 | [Entry identity and the dead-entry sweep](stand-in.md#row-28) | <span class="v v-partial">partial</span> | The effect is right, the visibility is not: comfy-env must hold the stand-in's patcher strongly or the sweep drops it. |
| 29 | [Clone dedup and `is_clone`](stand-in.md#row-29) | <span class="v v-yes">yes</span> | Upstream probes the incoming host model with the stand-in as the argument, and the stand-in answers not a clone, which is the case that matters. Two of the three read paths assume internals. |
| 30 | [`is_dynamic` gate and the per-node ledger walk](stand-in.md#row-30) | <span class="v v-yes">yes</span> | The stand-in answers False and nothing deeper is read; the worker's own pager handles its models. |
| 31 | [Hook weight caching](upstream-ask.md#row-31) | <span class="v v-no">no</span> | A memory counter taken once per process and spent against a card two processes share. Nothing declares it, nothing reclaims it. |
| 32 | [The unpatch backup dict](upstream-ask.md#row-32) | <span class="v v-no">no</span> | Per process CPU copies of patched weights. Host RAM the pin ladder never sees. |
| 33 | [Multigpu deepclones](upstream-ask.md#row-33) | <span class="v v-no">no</span> | comfy-env is single device throughout. |
| 34 | [Pin eviction ladder](upstream-ask.md#row-34) | <span class="v v-no">no</span> | Worker pinned RAM is invisible to the host's pin ladder. |
| 35 | [`--disable-smart-memory`](worker-memory.md#row-35) | <span class="v v-yes">yes</span> | The flag is read, and the prompt end `unload_all_models` reaches the stand-in. |
| 36 | [Node output cache and RAM-pressure release](worker-memory.md#row-36) | <span class="v v-yes">yes</span> | Worker outputs are host cache entries and are released like any other, upstream's own tie breaking bug included. |
| 37 | [Allocator cache release (`soft_empty_cache`)](worker-memory.md#row-37) | <span class="v v-partial">partial</span> | Runs in the host only when something was actually unloaded. The worker's allocator cache is released by the idle sweep instead. |
| 38 | [`loaded_models()` leak into node code](stand-in.md#row-38) | <span class="v v-yes">yes</span> | Stand-ins register with `currently_used` False, so a node that calls `loaded_models()` does not pick them up. |
| 39 | [Interrupt flag](upstream-ask.md#row-39) | <span class="v v-partial">partial</span> | Forwarded at progress callbacks only, and read rather than consumed. See [exceptions](exceptions.md). |
| 40 | [`unload_model_and_clones`](stand-in.md#row-40) | <span class="v v-yes">yes</span> | The stand-in's clone id is a private sentinel that never matches a real uuid. |
| 41 | [`GET /system_stats`](upstream-ask.md#row-41) | <span class="v v-partial">partial</span> | On Linux `vram_free` is device wide and right. On Windows it excludes what workers hold. |
| 42 | [`MAX_PINNED_MEMORY` and hostbuf ceilings](worker-memory.md#row-42) | <span class="v v-partial">partial</span> | Mirrored, and it binds by default on one path out of six. |

</div>

Three things fall out of the table.

- **The stand-in is the mechanism, not an option.** Row 1 is reachable only
  because a fake model for each worker model sits in ComfyUI's list. Without
  it the host can decline to take memory it does not have, which is useful,
  but it cannot take memory back, which is the half that matters when the
  card is already full.
- **The risk is narrower than the mechanism.** Three rows carry a `broke`
  exposure on the stand-in page: 29 (clone dedup), 30 (the `is_dynamic`
  gate and the per node pin walk) and 38 (the `loaded_models()` leak into
  node code). Everything else comfy-env does is reading values and
  publishing one number.
- **Only upstream can fix** rows 14 (the model compiler and CUDA graph
  capture), 19 (the OOM retry ladder), 33 (multigpu deepclones) and 39 (the
  interrupt flag). Nothing has been proposed for any of them. The
  counterexample runs the other way: row 25, the cache provider, is already
  merged upstream and comfy-env simply does not register one.

??? note "Twenty-three more behaviours already work inside a worker and need no bridge"

    These are per process by nature. A worker runs the same manager on the
    same tree, so it already gets them right for its own models. Listed so
    the reader can see what a bridge does *not* have to carry.

    | # | Behaviour | ELI5 | Why it already works in a worker |
    |---|---|---|---|
    | 1 | `ModelPatcher.clone` | A second remote control for the same TV, not a second TV | A MODEL never crosses the boundary in either direction ([ADR-0040](adr/0040-models-never-cross.md)), so the host never clones a worker model |
    | 2 | LoRA patching, baked or applied per forward | Stickers on the weights | Entirely inside the process that owns the module |
    | 3 | Legacy load, partial load, partial unload, unpatch | Put as much on the card as fits, move layers back when told | The worker runs the real code on its own models |
    | 4 | `ModelPatcherDynamic.load` and per-layer paging | Reserve the seats, walk people in only when called | Each process owns its vbars and its aimdo context |
    | 5 | aimdo's own accounting (`get_total_vram_usage`, `loaded_size`) | The pager's own scoreboard, the only one right for a paged model | The worker reads its own context and publishes one scalar |
    | 6 | Pinned RAM registration | Lock the weights' RAM pages so the card can pull them fast | The worker pins into its own ledger and its own hostbufs |
    | 7 | `ensure_pin_budget` against machine-wide available RAM | Before locking more RAM, check the whole machine still has 2 GB spare | One machine-wide number, read independently by each process with no coordination. The floors differ, though (row 34): the host's is `max(RAM_CACHE_HEADROOM / 2, 2 GiB)` with the headroom set by its executor per prompt, while a worker runs no executor, so its headroom stays 0 and its floor is exactly 2 GiB |
    | 8 | `set_ram_cache_release_state` | Before locking RAM, ask the results cache to make space first | Per process; a worker can set its own headroom without touching the host |
    | 9 | Model files through the page cache (`load_safetensors`, `safe_open`) | Point at the file instead of copying it; two programs pointing at one file share one copy | The kernel shares pristine pages across processes under both mappings |
    | 10 | `read_tensor_file_slice_into` | Read the layer straight from the file into the fast lane | Per process |
    | 11 | Cast buffers and their per-node reset | Scratch space on the card for the layer being copied, wiped after every step | The worker runs its own reset at its node boundary |
    | 12 | `PromptModelTracker` marks | A sticky note "in use by this job" so the RAM bouncer picks someone else first | The worker reads the prompt id at its call boundary and marks its own models |
    | 13 | Dtype decisions (`should_use_fp16`, `unet_dtype`) | Whether a model is stored at full, half or quarter size | All dtype flags are mirrored as resolved values |
    | 14 | `vram_state` (`--lowvram`, `--highvram`, ...) | The big dial for how aggressively models stay on the card | Crosses per call on the budget request; the three vram flags are deliberately not mirrored |
    | 15 | Placement (`unet_offload_device`, `vae_device`, ...) | Where each kind of model is born and parked | Per process, and Linux free memory already sees worker VRAM |
    | 16 | `get_total_memory` | How big the card is | Each process reads the same card |
    | 17 | aimdo enable and pressure flags | Whether paging is on and how it senses pressure | Exported to workers as env vars; the worker follows the host's resolved state |
    | 18 | Stream, pinning, mmap and allocator flags | How many lanes copy weights, whether RAM is locked, which allocator is used | Mirrored as resolved values; `cudaMallocAsync` is inherited through the environment by default, unless the pack's `[env_vars]` sets its own `PYTORCH_CUDA_ALLOC_CONF` |
    | 19 | Cache type flags (`--cache-ram`, `--cache-none`, ...) | How much the host remembers between runs | Executor side; workers run no queue |
    | 20 | OOM recognition and retry smaller (VAE tiling, attention halving) | Recognise "out of memory" and retry at a smaller size | Runs on the worker's own allocator, which is the right process |
    | 21 | Interrupt checks inside node loops | Long loops peek at the Cancel flag | The code runs unchanged on the worker's own flag; the host never sets it (see row 39 above) |
    | 22 | `set_cudnn_benchmark` after node import | Undo plugins that turned on a memory-hungry speed setting | The worker sets its own policy at import |
    | 23 | `hook_breaker` save and restore | ComfyUI repeatedly undoes a specific kind of plugin tampering | Not a worker behaviour; listed because a planted list entry is data rather than a patched function, so it is immune, while a wrap is not |

## Where the arithmetic lives

| Module | Owns | Reads torch or comfy? |
|---|---|---|
| `reserve.py` | The reserve itself: `charge` (what one worker is billed), `total_reserve`, `ask_target` (the admission sum, deliberately upstream's own expression rather than a copy), `aimdo_headroom`, `reserve_for_requester` (the discount that stops a worker being charged for its own incoming load twice) | no |
| `state_sync.py` | The residency census and its per-model sequence protocol, the admission ceiling and its in-flight ratchet, `plan_idle_release` and `plan_pressure_release`, the prompt-epoch pin marks, `WORKER_VRAM_FLOOR` and the other constants | no |
| `contract.py` | The upstream coupling contract, as data. Thirty entries (seventeen memory symbols, thirteen `folder_paths` entries) with per-entry severity, side, tier and the version each appeared in | no |
| `mirrored_args.py` | Which host CLI flags reach a worker. This is what decides a worker's dtype, so it is the answer to "why is my worker fp16 when my host is fp8" | no |
| `isolation/pool.py` | The host side wiring: the budget callback workers call into, the reserve publish, stand-in registration, the idle sweep and the pressure hook | yes |
| `memory_manager.py` | The worker side: enabling the pager, the release ladders, the cast boundaries, the pin census | yes, inside the worker |

The first four import neither torch nor comfy on purpose. That is why the
admission and reclaim arithmetic is unit tested on three operating systems
with no GPU anywhere near CI.

## Where to go next

- [The stand-in](stand-in.md), and [where it is inaccurate](model-stand-in-inaccuracies.md)
- [Admission and the reserve](admission.md), and [why Windows needs its own branch](windows-blind-spot.md)
- [Inside a worker](worker-memory.md)
- [What only upstream can fix](upstream-ask.md)
- [What occupies RAM and VRAM in a process](process-footprint.md)
- [ADR-0038](adr/0038-the-memory-floor.md), the decision record
