# What only upstream can fix

*The mechanisms nothing on the process boundary can reach, and the one
change to ComfyUI that would retire the stand-in.*
{: .subtitle }

This page assumes [what survives isolation](memory-approach.md).

## The ask, if you are reading this from upstream

One method and a registry, modelled on `set_ram_cache_release_state` and
the cache provider registry, both of which already live in the tree:

```python
class MemoryHolder:
    def release_memory(self, device, size) -> int: ...   # bytes actually freed
```

`free_memory` asks registered holders after its own models and before it
gives up, where it already asks the pinned memory helpers. It may return 0.
It may answer from cached state and do the real work afterwards. Nothing
changes when nobody registers, core learns nothing about subprocesses, and
comfy-env deletes [the stand-in](stand-in.md) and its eighteen attributes.
Upstream has accepted this shape before, for the results cache and for
external pinned memory pressure.

Three things about that signature, because an earlier draft got all three
wrong and would have been right to refuse.

**It takes a size.** Without one a holder cannot be asked for a shortfall,
only told to drop everything, which is the 1e30 hammer this design already
has. comfy-env's own `partial_release` takes a size and its planner computes
a per worker ask, so the caller side already exists.

**It returns bytes.** Without a return `free_memory` cannot terminate its
loop on the result, and cannot tell "freed nothing" from "freed enough".
That is exactly the contract `partially_unload` already has.

**It must be answerable synchronously.** This runs on ComfyUI's thread,
inside `free_memory`, inside a node, and a holder that blocks there stalls
every host load. A holder is therefore allowed to answer from what it
already knows and do the freeing behind the call.

**There is no `reserved_memory` half.** `EXTRA_RESERVED_VRAM` is read live
on every load and comfy-env already writes it. Wrapping a working global in
a registry is not worth a patch.

What the hook does and does not fix: it is about reclaim, so it retires the
stand-in. It does nothing for admission on Windows, whose blind free figure
is a separate ask, an NVML backed `get_free_memory`, recorded on the
[admission page](admission.md).

## The rows

Two kinds. Rows where the decision is made inside a sampler, a loader or a
model, and no process boundary has a say; and rows where a coordinator is
needed that nobody has proposed.

### 6. Free memory as a batch sizer { #row-6 }

**What ComfyUI does.** Free memory as a BATCH SIZER, not an evictor. `ModelPatcher.get_free_memory` returns `mm.get_free_memory` plus `vbars_analyze`, everything the pager could still evict, and samplers and VAE encode/decode divide it by an activation estimate to choose a batch size (`samplers.py`, `sd.py/1304/1373`). There is a second sizer that is supposed to match and does not: `samplers.py`, in the multigpu path whose own header says "keep in sync with `_calc_cond_batch` above", reads the RAW `mm.get_free_memory` rather than the patcher's, so it misses the `vbars_analyze` term and systematically under batches on any paged model, per device. And `sd.py`'s tiled fallback reads TOTAL rather than free and GROWS its tile while the estimate still fits, so two processes each grow into 80 percent of the same card. It fires several times per sampler step and decides gigabytes of activations.

**Today.** <span class="v v-no">no</span>, and nothing can be done from here. The `vbars_analyze` term is strictly process local, so neither side counts what the other could give back. On Linux the `get_free_memory` term already counts what the other HOLDS. Net effect: both sides shrink their batches for each other and neither grows for the other. No stand-in is read; this number never passes through the list

**With the upstream hook.** <span class="v v-partial">partial</span>: a holder would need to declare what it could RELEASE, not what it holds, which is a different interface from anything proposed

### 7. Dtype chosen from the size of the whole card { #row-7 }

**What ComfyUI does.** Dtype chosen from the size of the WHOLE card. `maximum_vram_for_weights` is `get_total_memory * 0.88 - minimum_inference_memory()`, and three gates divide a model's parameter count into it to decide whether fp8 weights stay fp8, and whether fp16 and bf16 get a manual cast. Each decision is worth 2x the entire weight set.

**Today.** <span class="v v-no">no</span>, and it is the inverse of row 6: both sides GROW for each other. It reads TOTAL, not free, so nothing a sibling holds enters it and no reserve can shrink it. Two processes on one 48 GB card, a 12B fp8 checkpoint, neither carrying a dtype flag: each computes 24 GB against 41.4 GB, each concludes there is room, each upcasts to fp16, 48 GB of weights before an activation. On a 24 GB card they agree correctly by luck, because 24 GB fails against 20.3 GB for both. Note the asymmetry that makes this the sharpest one on the load path: `unet_inital_load_device` and `text_encoder_initial_device` both return early when the pager is on, and these three gates have no such bypass, so they fire in the DEFAULT configuration

**With the upstream hook.** <span class="v v-partial">partial</span>: the honest fix is upstream reading free rather than total here, which changes single process behaviour too

### 8. cgroup RAM accounting { #row-8 }

**What ComfyUI does.** cgroup RAM accounting (`comfy/system_memory.py`, merged 2026-08-27). Clamps total and available RAM to the container's limit, and feeds `total_ram`, the pin ceiling, the pin budget floor, the Windows swap gate, CPU `get_free_memory` and both cache eviction targets.

**Today.** <span class="v v-no">no</span>: comfy-env's own readings still come from `psutil` and see the machine. Inside a container the two sides now disagree, ComfyUI sizing against the cgroup and comfy-env against the host. Worse in kind than a wrong number: host and every worker share ONE cgroup, so each process independently sizes its pin ceiling and its cache headroom against the same single budget

**With the upstream hook.** <span class="v v-partial">partial</span>: upstream is right and comfy-env should follow it. The N processes sharing one cgroup problem is nobody's yet

### 14. Model compiler, malloc graph and CUDA graphs { #row-14 }

**What ComfyUI does.** Comfy model compiler, malloc graph and CUDA graphs (`comfy/model_prefetch.py`, landed 2026-08-13 and 2026-09-04). Per block weight prefetch doubles transient residency, the malloc graph records the allocation pattern for the pager, and CUDA graph capture holds a private allocator pool per module until cleanup. Capture performs a full `synchronize()` plus a device wide VBAR eviction while it records.

**Today.** <span class="v v-no">no</span>, and this is the sharpest edge in the table. A sibling faulting during another process's capture window is coordinated by nothing, and capture's device wide eviction does not stop at the process boundary. Flags: `--disable-comfy-compiler`, `--disable-cuda-graphs`

**With the upstream hook.** <span class="v v-no">no</span>: nothing has been proposed, and cross process graph capture is not obviously solvable

### 19. The OOM retry ladder { #row-19 }

**What ComfyUI does.** The OOM retry ladder BELOW the top level branch. Eleven sites catch `OOM_EXCEPTION` and shrink their own work rather than stopping (nine through `raise_non_oom`, two as a literal `except OOM_EXCEPTION` in `nodes_frame_interpolation.py`): VAE decode and encode fall back to tiling, attention and the VAE's own `slice_attention` double their step count, the upscaler halves its tile, SeedVR and mesh post-processing fall back, and frame interpolation halves its batch. The top level clear in `execution.py` only runs when all eleven have given up.

**Today.** <span class="v v-no">no</span>, and this is the best available hook comfy-env is not using. Each retry shrinks THIS process in response to pressure a sibling may have caused, and permanently degrades this run. Four of the eleven call `soft_empty_cache` on the way, which hands the freed pages back to the driver where the sibling can take them, so the retry actively feeds the process that caused the OOM. None calls `mm.free_memory`, so the stand-in eviction and the idle worker ask never learn the event happened. The upscaler's retry releases no cache at all, so a fragmented allocator can fail the halved tile for the same reason

**With the upstream hook.** <span class="v v-no">no</span>: nothing proposed. These eleven are the natural place for an ask-a-sibling-before-you-shrink signal

### 21. `/free` { #row-21 }

**What ComfyUI does.** `/free` with `free_memory`: the stronger button also throws away every remembered step result, including results a worker sent back.

**Today.** <span class="v v-partial">partial</span>: the stand-in hears the unload half of the button. The cache reset it never hears about is not a leak, though: `e.reset()` rebuilds the whole `CacheSet`, so host side copies of worker results are dropped with everything else. What survives is what the worker still holds in its own process, which nobody asked it to drop

**With the upstream hook.** <span class="v v-partial">partial</span>: needs a cache reset hook

### 25. Prompt boundary signals { #row-25 }

**What ComfyUI does.** Prompt boundary signals: the prompt id in `comfy_execution.progress`, and the merged cache provider hooks for job start and end.

**Today.** <span class="v v-yes">yes</span> for the id; the provider is merged and comfy-env registers none

**With the upstream hook.** <span class="v v-yes">yes</span>, already merged

### 31. Hook weight caching { #row-31 }

**What ComfyUI does.** Hook weight caching. `patch_hooks` builds a `MemoryCounter` seeded with the WHOLE of `get_free_memory(load_device)` and keeps hook backups on the GPU until it runs out.

**Today.** <span class="v v-no">no</span>: one number, taken once, spent against a card two processes share. Nothing declares it and nothing reclaims it. Fires only in workflows that use hooks

**With the upstream hook.** <span class="v v-partial">partial</span>

### 32. The unpatch backup dict { #row-32 }

**What ComfyUI does.** The unpatch backup dict. `patch_weight_to_device` keeps a CPU copy of every patched weight for the life of the patch, cleared only on `unpatch_model`. A LoRA'd checkpoint costs its patched weights twice in host RAM.

**Today.** <span class="v v-no">no</span>: per process, invisible, and it is host RAM rather than VRAM, so the pin ladder never sees it either

**With the upstream hook.** <span class="v v-partial">partial</span>

### 33. Multigpu deepclones { #row-33 }

**What ComfyUI does.** Multigpu deepclones. `deepclone_multigpu` makes a full copy of a model per extra GPU, and `match_multigpu_clones` runs on every `_prepare_sampling`.

**Today.** <span class="v v-no">no</span>: comfy-env is single device throughout, everything routing through one `get_torch_device()`. Rare, but the bytes are a whole model each

**With the upstream hook.** <span class="v v-no">no</span>: multi GPU is a different design problem

### 34. Pin eviction ladder { #row-34 }

**What ComfyUI does.** Pin eviction ladder: when machine RAM gets tight, listed models let go of their locked RAM, models not used by this job first. Reads machine-wide available RAM.

**Today.** <span class="v v-no">no</span>: worker pins are invisible, and until 2026-09-06 comfy-env was not merely absent from this ladder but the reason it ran. A stand-in in a leaked `loaded_models()` list answers `is_dynamic()` False, which adds its FULL `model_size()` to `total_pins_required` (`mm.py`) and flips `free_for_dynamic`; `free_memory` then spends that on `ensure_pin_budget`, whose only reachable victims are the host's own dynamic models. With the pager running the entire ask was phantom, because host models are dynamic and book nothing. Closed by registering every stand-in with `currently_used` False, which shuts six of the seven leak sites. The two sides also do not stop at the same floor. The default branch is `max(RAM_CACHE_HEADROOM / 2, 2 GiB)`, and `RAM_CACHE_HEADROOM` is set by the executor for the duration of each prompt (`execution.py`, `min(10, max(2, total_ram * 0.10))` GB). A worker runs no executor, so its headroom stays 0 and its floor is exactly 2 GiB while the host's is higher on any machine above roughly 40 GB. Under `--fast-disk` the test is not a floor at all but the per process `MAX_PINNED_MEMORY` ceiling, and under `--high-ram` there is no test

**With the upstream hook.** <span class="v v-partial">partial</span>: needs a pin facet nobody has described

### 39. Interrupt flag { #row-39 }

**What ComfyUI does.** Interrupt flag: the stop button, checked before every node and every cast. It returns memory mid-step by unwinding.

**Today.** <span class="v v-partial">partial</span>: forwarded at progress callbacks only, and the forward deliberately does NOT consume the flag. `pool._handle_progress` reads it with the non-consuming `mm.processing_interrupted()` and raises into the worker, leaving the flag set so ComfyUI's own per-node check still finds it; comfy-env never calls `throw_exception_if_processing_interrupted`, which clears the flag before raising and would spend the user's click on a pack that then swallows the exception (`contract.py` lists the non-consuming read as FATAL for exactly that reason). Between progress callbacks the worker cannot see the flag at all

**With the upstream hook.** <span class="v v-no">no</span>: it is a call into the worker, not a holder interface

### 41. `GET /system_stats` { #row-41 }

**What ComfyUI does.** `GET /system_stats`: the numbers the UI gauge shows. Worker allocations show as used, never as reclaimable.

**Today.** <span class="v v-partial">partial</span> on Linux. On Windows it is worse than the header says: `vram_free` comes from `get_free_memory`, so worker allocations are not shown as used either, they are absent, and the gauge reads the card as freer than it is. Measured 2026-09-06: with a sibling holding 10 GiB, this process's `mem_get_info` did not move at all. The RAM half of the same endpoint behaves the opposite way on both platforms: it comes from a machine wide figure, so worker RAM does count as used

**With the upstream hook.** <span class="v v-partial">partial</span>

Only upstream can fix rows 14 (the model compiler and CUDA graph capture),
19 (the OOM retry ladder), 33 (multigpu deepclones) and 39 (the interrupt
flag), and nothing has been proposed for any of them. The counterexample runs
the other way: row 25, the cache provider, is already merged upstream and
comfy-env simply does not register one.

## See also

- [ADR-0024](adr/0024-upstream-interface-contract.md), the loan book of what comfy-env would ask upstream for
- [ADR-0038](adr/0038-the-memory-floor.md), where upstream reached the same shape and it did not ship
