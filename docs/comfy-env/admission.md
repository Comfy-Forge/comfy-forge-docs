# Admission and the reserve

*How a load is admitted when two processes share one card: the number
ComfyUI reads, what it cannot see on Windows, and the one value comfy-env
publishes to correct it.*
{: .subtitle }

This page assumes [what survives isolation](memory-approach.md), which
assumes [ComfyUI's memory management](comfyui-memory.md).

## The admission sum

Before loading a model, `load_models_gpu` adds up model size plus 10
percent, plus the larger of 0.8 GiB and the model's own activation
estimate, plus the reserve (`EXTRA_RESERVED_VRAM`), compares that with
`get_free_memory`, and calls `free_memory` for the shortfall. Every term is
upstream's; comfy-env computes none of them. What it does is make sure the
free figure and the reserve mean the same thing on both sides of the
process boundary.

`get_free_memory` is driver free (`torch.cuda.mem_get_info`) plus torch's
own idle cache. On Linux the driver figure is device wide, so every byte a
worker holds is already missing from it and the sum is right with nothing
declared. On Windows it is the calling process's own VidMm budget, blind to
every other process: a sibling can take 14 GB and the host's reading does
not move. That difference is the whole reason this page has two halves;
the measurement is [why Windows needs its own branch](windows-blind-spot.md).

## The budget round trip

The traffic runs the other way too, and it is the mechanism that makes most
of the reserve unnecessary. A worker about to load calls back to the host
(`request_vram_budget`): the worker's `load_models_gpu` is wrapped
(`_shimmed_load_models_gpu`, the one function comfy-env replaces in a
worker) to measure the incoming models, ask the host to free room for
them, write back what the host says, and only then call the original it
saved. The host runs ComfyUI's own `free_memory` on the worker's behalf,
re-deriving upstream's exact expression (`reserve.ask_target`) rather than
a copy of it. The host may fill the card, because a worker can ask it to
let go.

On the way it corrects the target. `pool._handle_vram_budget` adds an
offset to the shortfall it hands `free_memory`: ComfyUI's own blind reading
minus the device wide free figure from NVML, wherever NVML answers, Linux
included. On WDDM the difference is what the siblings hold; on Linux the
sibling term is in both readings and cancels, leaving only the host's idle
torch cache, so the correction is small and about the host. Without it,
ComfyUI computes a negative shortfall on Windows and frees nothing.

## The reserve

`EXTRA_RESERVED_VRAM` is how much of the card the host must always leave
alone, read on every load; `--reserve-vram` sets it. It is the one value
comfy-env writes in the host process, and it writes it into a knob the
operator already writes.

On Linux comfy-env publishes nothing of its own and leaves the value where
it found it, because the host already sees what packs hold. On Windows it
publishes what each worker holds now (`max(aimdo, torch)` per worker, never
their sum) plus a per worker floor for the CUDA context, capped at three
quarters of the card. The arithmetic is `reserve.py` (`charge`,
`total_reserve`, `ask_target`, `reserve_for_requester`), which imports
neither torch nor comfy and is unit tested on three operating systems with
no GPU. The census it bills from is `state_sync.py`. The wiring is
`pool._publish_reserve` and `pool._worker_charges`.

The same reserve is forwarded to the pager. ComfyUI seeds comfy-aimdo's
headroom once at startup from `--reserve-vram` and never touches it again;
comfy-env forwards `seed + (published - base)` into `set_simple_vram_headroom`
whenever the published value changes, live at the next page fault. On Linux
the added term is zero.

## Why the split is what it is

The platform verdict is twelve lines (`state_sync.blind_free_is_process_local`)
and one `if` in `reserve.charge`; everything else on this page is the
machinery it switches, all of it arithmetically inert on Linux. It is forced
by the host being both a party and the referee: on WDDM there is no local
signal of over admission (the allocation succeeds, and the *other* process
collapses, measured at 53x), so the host must be told what it cannot see,
and the only sources are a device wide reading or a ledger. comfy-env
prefers the reading and keeps the ledger as the fallback.

The simplification is upstream, where the blind number is made. comfy-aimdo
already reads NVML on Windows for the pager's own pressure sensing (ComfyUI
added `--disable-nvml-pressure` to "work around a cuMemGetInfo drift from
actual VRAM"), so the pager sees the card while `load_models_gpu` does not.
A `get_free_memory` that took `min(cuda_free, nvml_free)` on Windows would
let comfy-env delete the publish, the forward and the ledger; the
[upstream page](upstream-ask.md) records the ask.

## The rows

### 1. `load_models_gpu` admission { #row-1 }

**What ComfyUI does.** `load_models_gpu` admission: before loading a model, the host adds up model size plus 10 percent, plus the larger of 0.8 GiB and the incoming estimate, plus the reserve, and frees that much first.

**Today.** <span class="v v-yes">yes</span>: on Linux both sides read the same device wide free figure, so the sum is right without anything being declared; on Windows comfy-env supplies what the host cannot see. The sum itself is over incoming models and reads nothing off the stand-in. This row is also where the traffic runs the OTHER way, and it is the mechanism that makes most of row 3 unnecessary: a worker about to load calls back to the host (`request_vram_budget`), and the host runs ComfyUI's own `free_memory` on its behalf, re-deriving upstream's exact expression rather than a copy of it, and pre-compensating for its own blindness on Windows so the loop does not evaluate to "free nothing". The host may fill the card, because the worker can ask it to let go

**With the upstream hook.** <span class="v v-yes">yes</span>

### 3. The reserve (`EXTRA_RESERVED_VRAM`, `--reserve-vram`) { #row-3 }

**What ComfyUI does.** The reserve, `EXTRA_RESERVED_VRAM` and `--reserve-vram`: how much of the card the host must always leave alone. Read on every load. The pager never reads it at all: what ComfyUI seeds once at startup is the pager's own headroom (row 13), from `--reserve-vram`.

**Today.** <span class="v v-yes">yes</span>, and mostly not needed: on Linux the host already sees what packs hold, so comfy-env adds nothing of its own and leaves upstream's `EXTRA_RESERVED_VRAM` where it found it, which is 400 MiB by default and the operator's `--reserve-vram` only if they passed one. On Windows, where the host sees nothing of a pack, it publishes what each pack holds now, plus a 300 MiB floor per worker for the CUDA context and the cuBLAS and cuDNN handles. "What it holds" is one scalar per worker, `max(aimdo, torch)`, never their sum (the `held` census in `_persistent_worker`). Max was chosen from a sample where the two overlapped, 4.02 against 4.03 GiB for one 4 GiB model, so summing reserved 8 GiB for a 4 GiB worker. They are separate pools in general, though, so a worker paging 6 GB through aimdo while torch holds 2 GB of activations reports 6, and on Windows that under report is the reserve. The floor ADDS to residency rather than capping it, because the dominant term really is invisible: after `torch.cuda.init()` the driver shows the context while `memory_reserved()` still reads 0. They do NOT partition cleanly, though, and comfy-env's own source says so: modern torch allocates the cuBLAS workspace THROUGH the caching allocator, so 20 MiB of what this constant covers is already inside `memory_reserved`, 32 MiB under `cudaMallocAsync`. Floor and excess overlap by that much and adding them double books it. The 300 MiB is a Linux and RTX 3090 measurement (276 to 300 MiB) applied only on Windows, which is backwards, and it is now half checked: a bare CUDA context on a Windows RTX 4060 Ti costs 119 MiB device wide (measured 2026-09-06, driver level, twice). That is the floor before torch loads a cuBLAS or cuDNN handle, so 300 is not refuted, but the constant still has no Windows measurement of the thing it actually books. On Linux the context is charged at zero along with everything else, because the device wide reading already includes it. The total is capped at three quarters of the card, so a pathological census cannot reserve the whole GPU. Preventive on the legacy path, where the partial load budget shrinks with it; forwarded to the pager (row 13) for the paged one

**With the upstream hook.** <span class="v v-yes">yes</span>, plus a runtime headroom setter for the paged half

### 4. Admission, the activation guess { #row-4 }

**What ComfyUI does.** `load_models_gpu` admission, continued: one term of its sum is hardcoded to zero. `ControlBase.inference_memory_requirements` returns `0` for every ordinary controlnet, while `get_control` builds `cond_hint` at full pixel resolution and keeps it on the card for the whole run. Only `ControlLora` returns anything.

**Today.** <span class="v v-no">no</span>: an admission sum that under books on the host under books identically in a worker, and comfy-env cannot correct a number upstream declares as zero. `ControlLora.pre_run` is worse: it constructs a whole second `cldm.ControlNet`, moves it to the card and copies every lora weight over, with no `load_models_gpu`, no free memory read and no registration, so a second model appears on the card that nothing can see or evict

**With the upstream hook.** <span class="v v-partial">partial</span>: upstream would have to measure it

### 5. `get_free_memory` { #row-5 }

**What ComfyUI does.** `get_free_memory`: "how much room is left", which also sizes batches. Driver free plus torch's idle cache; on Linux it covers the whole card, on Windows [only the calling process](windows-blind-spot.md). Both halves measured at the driver on 2026-09-06 with the same probe: on Windows a sibling holding 10 GiB moved this process's number by zero MiB while the card moved; on Linux the same sibling moved it by 10,502 MiB, which is the allocation plus the holder's own context, tracking to within 2 MiB. Note also that the CUDA total is not the card: `cuMemGetInfo` reports 24,122 MiB where `nvidia-smi` reports 24,576, so 454 MiB is driver reserved and invisible to it.

**Today.** <span class="v v-yes">yes</span> to read, not modifiable; via the registered stand-in, the fake's size never enters this number. Eviction targets are `required minus free`. On Linux free already includes what the worker holds, so the target is right. On Windows it does not, and the target is systematically too small, which is the whole reason row 3 declares anything

**With the upstream hook.** <span class="v v-partial">partial</span>: upstream must choose free-side or ledger-side, never both

### 13. aimdo headroom { #row-13 }

**What ComfyUI does.** aimdo headroom: each process's pager keeps a safety margin, and ComfyUI seeds it once at startup and never touches it again. TWO seeds, not one, which matters for anything writing to them later: `--reserve-vram` becomes the process wide `simple_vram_headroom` (`main.py`), and `--vram-headroom` becomes a separate PER DEVICE `extra_vram_headroom` (`main.py`) which is added AFTER the `MAX` rather than inside it, so it steers both terms where the other steers one. The setter itself is live: changing it steers the next page fault.

**Today.** <span class="v v-yes">yes</span> now, on the two aimdo cells: comfy-env forwards `seed + (published - base)` into the pager's headroom at runtime, live at the next fault. That the setter is live is comfy-aimdo's contract as of #107, which documents it and ships a test asserting it; before that it was an undocumented C export and this repo's own experiment had concluded the opposite, because it used plain `nn.Linear` modules that never page. Worth knowing what the forward is worth per platform: on Linux the charge is 0, so published equals base, the added term is zero, and the value forwarded is the seed the pager already had. It moves a number only where the driver's free figure is process local. It steers the process the setter runs in, which is the host, and no worker's pager. Measured on Windows 2026-09-06, the first execution of this path anywhere: setting the headroom and driving one fault took residency from 14,784 MiB to 6,112 MiB, 8,672 MiB evicted in 52.3 ms, reproduced four times, with the resulting ceiling equal to capacity minus headroom to within one 32 MiB page. Three things the arithmetic did not know. The ctypes fallback is the LIVE path: the published Linux 0.5.2 wheel exports the C setter (`set_simple_vram_headroom`, whose argtypes `control.py` declares, plus the `simple_vram_headroom` data symbol) but ships no Python wrapper and no getter, so `pool` reaches the setter through `control.lib` and #107's accessors reach nobody yet; the Windows wheel has not been checked. Lowering is not the inverse of raising: it returns nothing on idle, on a watermark reset, or even on the next fault, and needs a `prioritize()` then a fault, so publishing a smaller reserve does not hand VRAM back at that moment. And there is a dead zone, wider than #107's docstring implies and not a constant: roughly 1,596 MiB on the 4060 Ti and 768 MiB on the 3090, against the 256 MiB the docstring claims, so it is wrong by 6x and 3x respectively. A single idle worker's forward of 556 MiB is inside both. The forward bites only once workers hold real weights. Measured on Linux, the KNOB works there too and better: the ceiling lands on `capacity - headroom` exactly, zero pages of error, about 4 GiB released per fault. What is inert on Linux is not the knob but the value, because `charge()` returns 0 there, so 18 GiB of worker weights still forwards the 256 MiB seed the pager already had. If comfy-env ever needs to move host residency on Linux this is a live lever; today it has nothing to say through it, because the host can already see every worker byte. It is also only half the pager's arithmetic: `simple_vram_headroom` appears in the per process cap term, while the cross process term uses the compile time 256 MiB constant. ComfyUI itself still seeds it once at startup and never again

**With the upstream hook.** <span class="v v-partial">partial</span>: ComfyUI should forward its own reserve too, rather than leaving it to us

### 20. The activation estimate { #row-20 }

**What ComfyUI does.** The activation estimate itself. `BaseModel.memory_required` has TWO branches, selected by which attention backend was chosen at import: with flash or xformers it is `area * dtype_size * 0.01 * factor`, and without it is `area * 0.15 * factor`, which drops the dtype term entirely and is 7.5x larger for fp16. Upstream's own comment calls the second "too aggressive". The dynamic patcher then adds 30 percent plus a flat 1 GiB. This is the `memory_required` that admission sums (row 2).

**Today.** <span class="v v-yes">yes</span> to read, and it is a per model constant tuned on one process now sizing the reserve on two. A worker's estimate and the host's are computed independently from the same formula and never reconciled, and if the two processes resolved different attention backends they are not even the same formula: 7.5x apart from identical shapes, with neither aware

**With the upstream hook.** <span class="v v-partial">partial</span>

## See also

- [Why Windows needs its own branch](windows-blind-spot.md), the measurement behind every platform branch
- [Inside a worker](worker-memory.md), the worker side of the round trip
- [ADR-0034](adr/0034-admission-by-arithmetic.md) and [ADR-0038](adr/0038-the-memory-floor.md), the decisions
