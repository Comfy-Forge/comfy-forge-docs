# ComfyUI's memory manager

A reference description of how ComfyUI manages VRAM and host RAM, written
because upstream has none. Everything here is read from source; nothing is
inferred from behaviour unless labelled as a measurement.

**Baseline:** ComfyUI `0.32.0`, commit `b323a34` (2026-08-12). Line numbers
refer to `comfy/model_management.py` unless stated otherwise. Measurements
were taken on an RTX 4060 Ti 16 GB, Windows 11, driver 581.57, WDDM,
torch 2.8.0+cu128.

This page describes upstream only. What comfy-env does about it is
[ADR-0036](adr/0036-mirroring-comfyui-memory-management.md); the
plain-language version is [Sharing one GPU](sharing-one-gpu.md).

---

## 1. What it tracks

One module-level list:

```python
current_loaded_models = []
```

Entries are `LoadedModel` objects, each wrapping a `ModelPatcher`. Per model,
the manager can ask:

| Question | Method | Notes |
|---|---|---|
| How big is it? | `model_size()` | full weight bytes |
| How much is on the GPU *now*? | `loaded_size()` | 0 … `model_size()` — **not** a boolean |
| How much is not? | `model_offloaded_memory()` (`:773`) | `model_size() - loaded_size()` |
| Where does it live / go? | `load_device`, `offload_device` | offload target is normally CPU RAM |
| Is it in use this moment? | `currently_used` | set `True` on load, cleared for every eviction candidate (`:876`) |
| Is it still referenced? | `sys.getrefcount(model)` | used as a tiebreak in victim ordering |

Two important structural details:

- **`LoadedModel._model` is a weakref** (`:750`), exposed through a `.model`
  property (`:762-764`). `__eq__` is `self.model is other.model` (`:820-821`)
  — identity on the *patcher*, not on the `LoadedModel`.
- **Residency is fractional.** A model can be 30% resident, which is what
  "lowvram" means: keep some weights on the card and stream the rest per
  layer.

There is no allocator here, no arena, no ownership graph. A list, some sizes,
and two globals re-read on demand.

## 2. The VRAM state machine

```python
class VRAMState(Enum):
    DISABLED = 0    # no VRAM: never move models to GPU
    NO_VRAM = 1     # very low VRAM: every saving enabled
    LOW_VRAM = 2
    NORMAL_VRAM = 3
    HIGH_VRAM = 4
    SHARED = 5      # no dedicated VRAM (unified memory)
```

Selected at import (`:558-579`) from `--lowvram` / `--novram` /
`--highvram` / `--gpu-only` / `--cpu`, defaulting to `NORMAL_VRAM`. `SHARED`
is for unified-memory devices; `DISABLED` for CPU-only.

The state matters mainly at load time: `NO_VRAM` forces the smallest possible
resident fraction, `HIGH_VRAM` skips offloading, and `LOW_VRAM`/`NORMAL_VRAM`
both go through the weight-streaming calculation in §6.

## 3. Measuring free memory — the part that differs by OS

`get_free_memory(dev, torch_free_too=False)` (`:1739`) is the single input to
every decision below. What it returns depends on the device:

**CPU or MPS** (`:1744-1746`):

```python
mem_free_total = psutil.virtual_memory().available
```

System-wide, honest, shared between processes.

**CUDA** (`:1774-1778`):

```python
mem_free_cuda, _  = torch.cuda.mem_get_info(dev)
mem_free_torch    = mem_reserved - mem_active     # torch's own free cache
mem_free_total    = mem_free_cuda + mem_free_torch
```

Two terms, and both are per-process:

- `mem_free_torch` is *this process's* allocator cache — blocks torch holds
  but is not using. Counting them as free is correct, since torch will reuse
  them without asking the driver.
- `mem_free_cuda` comes from `mem_get_info`, and **this is where the OS
  distinction lives**.

| Platform | What `mem_get_info` reports |
|---|---|
| Linux | free memory on the device, globally |
| Windows / WDDM | **this process's VidMm commitment budget** |

On Windows the number is not device free memory. It debits 1:1 for the
calling process's own allocations, but is blind to other processes until the
video memory manager re-partitions budgets — and *re-partitioning is
triggered by process and context lifecycle events, not by memory pressure*.

Measured: one sibling process grew from 256 MB to 14,336 MB, taking the card
to 1,331 MB physically free, while the observer's reported free never moved
from 15,221 MB. A *third* process starting and allocating 50 MB did move it.

Two biases fall out, both reproduced: while the budget is pinned, the
reported free sits **583 MB below** true device free (four reproductions,
independent of the caller's own usage); just after re-partitioning it sits
roughly **533 MB above** it.

### The failure mode this creates

On Linux, exhausting VRAM raises an out-of-memory error — loud, catchable,
attributable. On Windows it does not fail. WDDM demand-pages some process's
GPU allocations out to system RAM.

Measured: a process allocated 8 GiB with 109 MB physically free, sustained
143 GB/s, and never saw an error — while a *sibling* went from 237 GB/s to
**4.5 GB/s**, then recovered to 248 GB/s on a second pass as its pages
faulted back in.

The cost lands on a bystander, and there is no signal anywhere: no exception,
no counter, invisible to the allocator, to `mem_get_info`, and to NVML —
which returns `NOT_AVAILABLE` for per-process memory on every PID under WDDM,
including the caller's own.

Upstream is aware of the symptom even without naming the mechanism:

```python
EXTRA_RESERVED_VRAM = 400 * 1024 * 1024
if WINDOWS:
    EXTRA_RESERVED_VRAM = 600 * 1024 * 1024  # Windows is higher because of the shared vram issue
    if total_vram > (15 * 1024):             # more on 16GB+ cards
        EXTRA_RESERVED_VRAM += 100 * 1024 * 1024
```

`:847-851`. Overridable with `--reserve-vram`.

## 4. Reserves

```python
def minimum_inference_memory():
    return (1024 * 1024 * 1024) * 0.8 + extra_reserved_memory()   # :860-861
```

0.8 GB of working room, plus the reserve above. On a 16 GB Windows card that
is 0.8 + 0.7 = **1.5 GB** held back from weights for activations and
workspaces.

`MIN_WEIGHT_MEMORY_RATIO` is `0.4`, but **`0.0` on NVIDIA** (`:454-456`),
which removes the "keep at least 40% of weights resident" floor from the
streaming calculation in §6.

## 5. Eviction

```python
def free_memory(memory_required, device, keep_loaded=[],
                for_dynamic=False, pins_required=0, ram_required=0):   # :863
```

Two stages.

**Build the candidate list** (`:871-877`). Everything on this device that is
not in `keep_loaded` and not `is_dead()`. Each candidate's `currently_used`
is cleared as a side effect. Sort key:

```python
(-model_offloaded_memory(), sys.getrefcount(model), model_memory(), index)
```

Ascending — so **the most already-offloaded model is evicted first**, because
it is cheapest to finish evicting.

**Walk it** (`:879-894`):

```python
for x in can_unload_sorted:
    memory_to_free = memory_required - get_free_memory(device)   # :883
    if memory_to_free > 0 and current_loaded_models[i].model_unload(memory_to_free):
        unloaded_model.append(i)
```

The target is **recomputed on every iteration**, and the loop acts only while
it is positive. That re-measurement is what makes this evict the minimum
rather than everything: each eviction raises free memory, shrinking the
remaining shortfall, until the guard stops the walk.

`model_unload` (`:806-815`) escalates:

```python
if memory_to_free < self.model.loaded_size():
    freed = self.model.partially_unload(self.model.offload_device, memory_to_free)
    if freed >= memory_to_free:
        return False          # enough — stays registered, just smaller
self.model.detach(unpatch_weights)
return True                   # full unload — caller pops it from the list
```

Note the guard: when `memory_to_free >= loaded_size()`, the partial path is
skipped and the model goes straight to `detach()`. A model with
`loaded_size() == 0` therefore always takes the detach path and is **popped
while freeing nothing**.

Returning True causes the entry to be removed from `current_loaded_models`
(`:893-894`), and **nothing re-adds it** — re-registration only happens
through `load_models_gpu`.

`unload_all_models()` is `free_memory(1e30, device)` (`:2054-2056`), reached
from OOM recovery (`execution.py:645`), from `--disable-smart-memory` after
every prompt (`execution.py:837`), and from the "free memory" button.

## 6. Loading

`load_models_gpu(models, memory_required=0, force_patch_weights=False, minimum_memory_required=None, force_full_load=False)` (`:934`).

**Budget:**

```python
inference_memory = minimum_inference_memory()
extra_mem = max(inference_memory, memory_required + extra_reserved_memory())
```

**Dedup and expand** — order-preserving, includes `model_patches_models()`.

**Re-register already-loaded models.** For each model being loaded, every
entry whose patcher `is_clone` of it is popped, `detach(unpatch_all=False)`'d,
and re-inserted (`:951-959`). This is bookkeeping, not an unload:
`ModelPatcher.detach(unpatch_all=False)` skips `unpatch_model` entirely
(`model_patcher.py:1295-1299`), so **weights stay on the GPU**.

**Free room** (`:969-974`):

```python
free_memory(total_memory_required[device] * 1.1 + extra_mem, device,
            for_dynamic=free_for_dynamic,
            pins_required=total_pins_required.get(device, 0))
```

Note `model_memory_required(device)` (`:776-780`) asks only for the
*offloaded* portion when the model is already on the target device — a
partially resident model does not re-request its whole size.

**Decide the resident fraction** (`:988-1002`):

```python
lowvram_model_memory = max(0,
    (current_free_mem - minimum_memory_required),
    min(current_free_mem * MIN_WEIGHT_MEMORY_RATIO,
        current_free_mem - minimum_inference_memory()))
lowvram_model_memory -= loaded_memory
if lowvram_model_memory == 0:
    lowvram_model_memory = 0.1
if vram_set_state == VRAMState.NO_VRAM:
    lowvram_model_memory = 0.1
```

**`0.1` is a sentinel meaning "load essentially nothing"** — set when the
computed budget is zero, and unconditionally under `NO_VRAM`.

**Load** (`:782-790`):

```python
use_more_vram = lowvram_model_memory
if use_more_vram == 0:
    use_more_vram = 1e32          # 0 means "no limit"
self.model_use_more_vram(use_more_vram, ...)   # -> partially_load(device, extra_memory)
```

So `partially_load` receives either `1e32` (everything), a real byte budget,
or `0.1` (nothing). It never receives `0`.

## 7. Host RAM

Eviction does not delete a model — it moves it to `offload_device`, i.e. your
RAM. VRAM pressure therefore becomes RAM pressure, and there is a second
budget for it. `free_memory` accepts `ram_required` and `pins_required`
alongside `memory_required`.

**Pinned memory** is page-locked host memory that the OS may not swap,
required for DMA transfers that don't stage through a bounce buffer and can
overlap with compute. ComfyUI enables it by default on NVIDIA and AMD, using
`cudaHostRegister` on tensors that already exist (`:1626`,
`pinned_memory.py:61,106`) — so `MAX_PINNED_MEMORY` is a **ceiling with a
running total**, not an up-front reservation.

```python
if WINDOWS:
    MAX_PINNED_MEMORY = ram * 0.40   # "Windows limit is apparently 50%"
else:
    MAX_PINNED_MEMORY = max(ram * 0.40,
                            min(ram * 0.90, ram - 4GB,
                                ram + get_disk_swap_total() - 16GB))
```

`:1575-1578`. The Linux branch folds swap into the budget; `get_disk_swap_total()`
reads `/proc/swaps` and returns 0 anywhere else. Release policy is the mirror
image (`:701-711`): non-Windows frees pins on *any* pressure, Windows only
below 512 MB available or at ≥5% swap usage.

Disable with `--disable-pinned-memory`.

## 8. The dynamic path

Models answering `is_dynamic() → True` use a separate just-in-time loader:
weights are faulted in on use rather than loaded up front, backed by
uncommitted file-backed mappings the OS can reclaim. `is_dynamic()` gates it
throughout — `:650`, `:884-888`, `:934`, `:966`, `:1006`, `:1430` — including
a branch inside the eviction loop that declines to unload dynamic models on
behalf of other dynamic models.

Its OS distinction is about host RAM, not VRAM: on Windows this shows up as
high apparent RAM usage that Windows reclaims on demand, on Linux as low
usage with the remainder counted as disk cache. See
[Dynamic VRAM in ComfyUI](https://blog.comfy.org/p/dynamic-vram-in-comfyui-saving-local).

## 9. Cache flushing

```python
def soft_empty_cache(force=False):
    ...
    elif torch.cuda.is_available():
        torch.cuda.synchronize()
        torch.cuda.empty_cache()
        torch.cuda.ipc_collect()
```

**The `force` argument is ignored on the CUDA branch** — there is no cheap
mode. `free_memory` calls it once *after* its loop (`:900-906`), never
before.

Measured: `empty_cache()` does return memory to the driver under
`backend:cudaMallocAsync` (NVML used 2,364 → 316 MB); the marginal cost over
an already-pending sync is ~0.02 ms, and releasing 2 GiB costs ~12-15 ms.

Note that flushing does **not** change `get_free_memory`: it moves bytes from
`mem_free_torch` into `mem_free_cuda`, and the sum is invariant.

## 10. Flags

| Flag | Effect |
|---|---|
| `--lowvram` / `--novram` / `--highvram` / `--gpu-only` / `--cpu` | pick `VRAMState`; mutually exclusive |
| `--reserve-vram GB` | overrides `EXTRA_RESERVED_VRAM` |
| `--disable-smart-memory` | skips the eviction-target recompute (`:882`) and unloads everything after each prompt |
| `--disable-pinned-memory` | sets `MAX_PINNED_MEMORY = -1` |
| `--cache-ram` | RAM-pressure cache thresholds |
| `--fast-disk` | prefer disk-backed offload over unpinned RAM |

## Upstream reading

There is no official document describing any of the above. The repository has
no `docs/` directory, and `docs.comfy.org` covers flags rather than mechanism.

- [Dynamic VRAM in ComfyUI](https://blog.comfy.org/p/dynamic-vram-in-comfyui-saving-local)
  — best first-party writing; covers §8 and the RAM-side OS distinction.
- [NVFP4, async offload and pinned memory](https://blog.comfy.org/p/new-comfyui-optimizations-for-nvidia)
  — why §7 is on by default.
- [Startup flags](https://docs.comfy.org/development/comfyui-server/startup-flags)
  — reference for §10.
- [DeepWiki: Memory and Device Management](https://deepwiki.com/Comfy-Org/ComfyUI/2.6-memory-and-device-management)
  — machine-generated from source; orientation only.
- [PR #11845](https://github.com/Comfy-Org/ComfyUI/pull/11845) — proposes
  reading WDDM's target VRAM figure *"rather than using the pytorch/Cuda
  stack reported numbers"*, i.e. the §3 diagnosis. **Status unconfirmed:** no
  DXGI or WDDM call exists anywhere in the `b323a34` tree, so it is not what
  we build against today.
