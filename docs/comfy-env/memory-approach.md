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

Unfortunately it currently assumes that there is ever only exactly *one* process, which creates a few problems for us.

We have tried to maintain as much of the native memory management as possible through several strategies, without doing any patching or using any unmaintanable or unclean tricks.

## ComfyUI memory management that survives comfy-env

Every memory optimization and function ComfyUI uses and whether it reaches a worker today.

<div class="verdict-table wide-table num-col" markdown>

| # | Mechanism | Today | ELI5 |
|---|---|---|---|
| 1 | `load_models_gpu` admission | <span class="v v-yes">yes</span> | <b>What ComfyUI does.</b> [<code>load_models_gpu</code>](comfyui-memory-api-reference.md#load_models_gpu) is what every loader node calls:<ul><li>adds up what the incoming models need: size plus 10 percent, plus the larger of 0.8 GiB and the model's own activation estimate, plus the reserve</li><li>calls <a href="../comfyui-memory-api-reference/#free_memory"><code>free_memory</code></a> for that much</li><li>then loads</li></ul><b>Under isolation.</b> The one input comfy-env has to keep honest is free VRAM:<ul><li>Linux: device wide, so what workers hold is already counted</li><li>Windows: the calling process's own budget, so comfy-env publishes what workers hold into the reserve term</li></ul>A worker about to load runs the same sum through the host, which frees room on its behalf. See [admission and the reserve](admission.md). |
| 2 | `free_memory` eviction ladder | <span class="v v-yes">yes</span> | <b>What ComfyUI does.</b> [<code>free_memory</code>](comfyui-memory-api-reference.md#free_memory) is asked for a number of bytes of VRAM on one device and makes them free:<ul><li>walks <code>current_loaded_models</code>, sorted: the model already most offloaded first, then the least referenced, then the smallest</li><li>unloads one at a time, re-reading free VRAM after each</li><li>stops when the request is covered or the list runs out; a model it never listed is never asked</li></ul><b>Under isolation.</b> The stand-in comfy-env registers for every model a worker holds is in that list, so a worker's model is asked in its turn and the worker does the real unload. See [the stand-in model](stand-in-model.md). |
| 3 | The reserve (`EXTRA_RESERVED_VRAM`, `--reserve-vram`) | <span class="v v-yes">yes</span> | VRAM ComfyUI keeps untouched for work it cannot size in advance. comfy-env forwards the same reserve to every worker and to the pager, and raises it only where the host cannot see workers. See [admission and the reserve](admission.md). |
| 4 | Admission, the activation guess | <span class="v v-no">no</span> | The admission sum is only as good as its parts, and one part is a guess ComfyUI hardcodes. For a controlnet, the "how much scratch memory will this need while running" term returns zero, while in reality the control image is kept on the card at full pixel resolution for the whole run. Worse, a ControlLora builds a whole second network on the card without going through admission at all, so a model appears that nothing registered and nothing can evict. None of this is comfy-env's doing: the host under books by exactly the same amount as a worker would, because the number comes from upstream and comfy-env never computes it. The consequence under isolation is the same as native, a load admitted with less room than it really needs; it is listed here so nobody expects comfy-env to have fixed it. See [admission and the reserve](admission.md). |
| 5 | `get_free_memory` | <span class="v v-yes">yes</span> | "How much room is left", read the same way by both sides. The stand-in's size never enters it. See [admission and the reserve](admission.md). |
| 6 | Free memory as a batch sizer | <span class="v v-no">no</span> | Free VRAM is not only used to decide whether a model fits; samplers and the VAE also divide it by an estimate to decide how many images to process at once, several times per step, and that decides gigabytes of activations. The number they divide is "free VRAM plus everything the pager could still evict from this process". That second term is strictly local: a worker cannot know what the host could evict, and the host cannot know what a worker could. So on Linux each side sees what the other holds and shrinks its batches for it, but neither ever grows because the other could make room, which is slower than native, never unsafe. Two of upstream's own sizers make it worse regardless of comfy-env: one reads the raw free figure and under batches any paged model, and the tiled VAE fallback reads total VRAM rather than free and grows its tile until the estimate fits, so two processes each grow into most of the same card. Nothing on the process boundary can fix a decision made inside a sampler loop. See [what only upstream can fix](upstream-ask.md). |
| 7 | Dtype chosen from the size of the whole card | <span class="v v-no">no</span> | When a model is loaded, ComfyUI decides whether to keep its weights small (fp8) or upcast them to fp16, and that decision is worth twice the whole weight set. It decides by comparing the model's size with 88 percent of the card's total memory, not with what is free. So nothing a sibling process holds enters the decision and no reserve can shrink it: a host and a worker on one 48 GB card, each loading a 12B fp8 checkpoint, each conclude there is room and each upcast, 48 GB of weights before a single activation. On a 24 GB card they both correctly decide there is no room, by luck of the arithmetic. This is the inverse of row 6: there both sides shrink for each other, here both grow. The gates fire in the default configuration, pager or not, and they run inside the loader where the process boundary has no say. Both sides pick exactly what native would have picked; the trouble is that native never had a sibling. See [what only upstream can fix](upstream-ask.md). |
| 8 | cgroup RAM accounting | <span class="v v-no">no</span> | Inside a Docker container the machine may have 256 GB of RAM while the container is allowed 32 GB. Since 2026-08-27 ComfyUI reads the container's limit and sizes everything RAM related against it: how much RAM it may pin for fast transfers, when the results cache must start evicting, the Windows swap gate. comfy-env's own readings still come from `psutil`, which reports the whole machine, so inside a container the host and comfy-env are sizing against two different numbers. And the deeper problem has no fix from either side yet: the host and every worker live in the same container and share the one budget, but each process independently sizes its pin ceiling and its cache headroom against the full budget, as if it were alone. Five processes each believing they may pin 2 GB less than 32 GB is not a 32 GB budget. See [what only upstream can fix](upstream-ask.md). |
| 9 | `unload_all_models` and the Free button | <span class="v v-yes">yes</span> | The button reaches the stand-in and the worker releases everything. See [the stand-in model](stand-in-model.md). |
| 10 | Cast buffers | <span class="v v-partial">partial</span> | Per process scratch VRAM for the layer being copied. Every process pays for its own; comfy-env books the incoming load's share in the reserve, but they are never shared. See [inside a worker](worker-memory.md). |
| 11 | Partial load budget (`lowvram_model_memory`) | <span class="v v-no">no</span> | "Load only this much of the model" never reaches a worker: the stand-in is not a partially loadable model on either path. See [inside a worker](worker-memory.md). |
| 12 | `LoadedModel` size questions | <span class="v v-partial">partial</span> | How big is it, how much is on the card. The stand-in answers from what the worker measured; some reads assume internals the stand-in only fakes. See [the stand-in model](stand-in-model.md). |
| 13 | aimdo headroom | <span class="v v-yes">yes</span> | The pager's own reserve. comfy-env forwards it at runtime as the published reserve changes. See [admission and the reserve](admission.md). |
| 14 | Model compiler, malloc graph and CUDA graphs | <span class="v v-no">no</span> | A sibling process faulting during another's CUDA graph capture is uncoordinated. The sharpest edge in this table. See [what only upstream can fix](upstream-ask.md). |
| 15 | The inactive cache tier | <span class="v v-yes">yes</span> | Results cached in RAM and drained at every node. Worker results sit in the host's cache like any other entry. See [inside a worker](worker-memory.md). |
| 16 | Per-layer fault and aimdo's C-side eviction | <span class="v v-yes">yes</span> | The pager pages each process's weights by the pressure it senses itself. Works, with no coordination possible from Python. See [inside a worker](worker-memory.md). |
| 17 | `model_unload`, partial versus full | <span class="v v-yes">yes</span> | The stand-in implements partial unload and returns the bytes actually moved, so ComfyUI's arithmetic stays right. See [the stand-in model](stand-in-model.md). |
| 18 | OOM branch in `execution.py` | <span class="v v-yes">yes</span> | A worker's out of memory error crosses as the real torch class, so ComfyUI's top level handling runs as native. See [inside a worker](worker-memory.md). |
| 19 | The OOM retry ladder | <span class="v v-no">no</span> | Eleven sites retry smaller on OOM, each shrinking its own process. A worker retries alone and the host never learns the pressure came from it. See [what only upstream can fix](upstream-ask.md). |
| 20 | The activation estimate | <span class="v v-yes">yes</span> | A per model constant, read as native, now sizing the reserve on two processes instead of one. See [admission and the reserve](admission.md). |
| 21 | `/free` | <span class="v v-partial">partial</span> | The unload half reaches the stand-in. The cache reset half is host only, which is right, since worker results live in the host cache. See [what only upstream can fix](upstream-ask.md). |
| 22 | `cleanup_models` prune | <span class="v v-yes">yes</span> | Dead entries are pruned; the stand-in carries the weakref the prune expects. See [the stand-in model](stand-in-model.md). |
| 23 | Async offload streams | <span class="v v-yes">yes</span> | The stream count is mirrored to workers and read live when booking cast buffers. See [inside a worker](worker-memory.md). |
| 24 | `cudaMallocAsync` as the default allocator | <span class="v v-yes">yes</span> | Mirrored. It is also why a GPU tensor crossing the boundary is copied rather than shared (see [zero-copy CUDA transfer](zero-copy-ipc.md)). See [inside a worker](worker-memory.md). |
| 25 | Prompt boundary signals | <span class="v v-yes">yes</span> | The prompt id is read; upstream merged the provider and comfy-env registers none. See [what only upstream can fix](upstream-ask.md). |
| 26 | `model_load` and the finalizer tripwire | <span class="v v-yes">yes</span> | The stand-in is inserted into the list directly and satisfies what the finalizer checks. See [the stand-in model](stand-in-model.md). |
| 27 | Dirty mmap bounce | <span class="v v-yes">yes</span> | Mirrored. Two workers loading the same file share the page cache instead of each copying it. See [inside a worker](worker-memory.md). |
| 28 | Entry identity and the dead-entry sweep | <span class="v v-partial">partial</span> | The effect is right, the visibility is not: comfy-env must hold the stand-in's patcher strongly or the sweep drops it. See [the stand-in model](stand-in-model.md). |
| 29 | Clone dedup and `is_clone` | <span class="v v-yes">yes</span> | Upstream probes the incoming host model with the stand-in as the argument, and the stand-in answers not a clone, which is the case that matters. Two of the three read paths assume internals. See [the stand-in model](stand-in-model.md). |
| 30 | `is_dynamic` gate and the per-node ledger walk | <span class="v v-yes">yes</span> | The stand-in answers False and nothing deeper is read; the worker's own pager handles its models. See [the stand-in model](stand-in-model.md). |
| 31 | Hook weight caching | <span class="v v-no">no</span> | A memory counter taken once per process and spent against a card two processes share. Nothing declares it, nothing reclaims it. See [what only upstream can fix](upstream-ask.md). |
| 32 | The unpatch backup dict | <span class="v v-no">no</span> | Per process CPU copies of patched weights. Host RAM the pin ladder never sees. See [what only upstream can fix](upstream-ask.md). |
| 33 | Multigpu deepclones | <span class="v v-no">no</span> | comfy-env is single device throughout. See [what only upstream can fix](upstream-ask.md). |
| 34 | Pin eviction ladder | <span class="v v-no">no</span> | Worker pinned RAM is invisible to the host's pin ladder. See [what only upstream can fix](upstream-ask.md). |
| 35 | `--disable-smart-memory` | <span class="v v-yes">yes</span> | The flag is read, and the prompt end `unload_all_models` reaches the stand-in. See [inside a worker](worker-memory.md). |
| 36 | Node output cache and RAM-pressure release | <span class="v v-yes">yes</span> | Worker outputs are host cache entries and are released like any other, upstream's own tie breaking bug included. See [inside a worker](worker-memory.md). |
| 37 | Allocator cache release (`soft_empty_cache`) | <span class="v v-partial">partial</span> | Runs in the host only when something was actually unloaded. The worker's allocator cache is released by the idle sweep instead. See [inside a worker](worker-memory.md). |
| 38 | `loaded_models()` leak into node code | <span class="v v-yes">yes</span> | Stand-ins register with `currently_used` False, so a node that calls `loaded_models()` does not pick them up. See [the stand-in model](stand-in-model.md). |
| 39 | Interrupt flag | <span class="v v-partial">partial</span> | Forwarded at progress callbacks only, and read rather than consumed. See [exceptions](exceptions.md). See [what only upstream can fix](upstream-ask.md). |
| 40 | `unload_model_and_clones` | <span class="v v-yes">yes</span> | The stand-in's clone id is a private sentinel that never matches a real uuid. See [the stand-in model](stand-in-model.md). |
| 41 | `GET /system_stats` | <span class="v v-partial">partial</span> | On Linux `vram_free` is device wide and right. On Windows it excludes what workers hold. See [what only upstream can fix](upstream-ask.md). |
| 42 | `MAX_PINNED_MEMORY` and hostbuf ceilings | <span class="v v-partial">partial</span> | Mirrored, and it binds by default on one path out of six. See [inside a worker](worker-memory.md). |

</div>

Three things fall out of the table.

- **The stand-in is the mechanism, not an option.** Row 2 is reachable only
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

## Process management.
An upstream interface would remove need for ducktyping but not the need for
worker-specific logic, because each worker
[keeps some RAM for its interpreter and torch, and some VRAM for its CUDA context](process-footprint.md),
a fixed cost per process that no model ledger describes.

## Where to go next

- [The stand-in model](stand-in-model.md), and [where it is inaccurate](model-stand-in-inaccuracies.md)
- [Admission and the reserve](admission.md), and [why Windows needs its own branch](windows-blind-spot.md)
- [Inside a worker](worker-memory.md)
- [What only upstream can fix](upstream-ask.md)
- [What occupies RAM and VRAM in a process](process-footprint.md)
- [ADR-0038](adr/0038-the-memory-floor.md), the decision record
