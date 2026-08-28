# The ComfyUI memory map

*"Does ComfyUI manage any other memory than models?" Yes -- six kinds, with
very different amounts of actual management. This page is the map; line
references are against **ComfyUI v0.33.0** (`comfy/model_management.py` unless
noted).*

[How ComfyUI manages memory](comfyui-memory.md) walks the model ledger -- the
star of the show -- in detail. This page places it among everything else that
occupies RAM and VRAM in a running ComfyUI, because two of the six kinds are
not in `model_management.py` at all, and the biggest RAM consumer between runs
is usually one of those.

## The map

| # | Memory | Where it lives | How much ComfyUI manages it |
|---|---|---|---|
| 1 | Model weights on the GPU | VRAM | **Fully managed.** The `current_loaded_models` ledger and the evict-the-minimum loop -- [the whole sibling page](comfyui-memory.md). |
| 2 | Model weights evicted to the CPU | RAM | **Managed as the same ledger's other half.** Eviction is a *move*, not a delete. |
| 3 | Pinned (page-locked) RAM | RAM | **Fully managed**, by a separate budget. |
| 4 | Activation working memory | VRAM | **Managed by guessing.** Reserved headroom, never tracked. |
| 5 | The allocator cache | VRAM | **Counted and flushable**, not allocated by ComfyUI. |
| 6 | Cached node outputs and node instances | RAM (+VRAM if outputs hold GPU tensors) | **Managed by a different subsystem** -- the execution cache, not the memory manager. |

Only #1 and #3 involve ComfyUI actively deciding what to keep and what to
give back. #2 is a consequence of #1, #4 and #5 are arithmetic around memory
it cannot control, and #6 is a cache-retention policy that happens to hold
most of your RAM.

## 2. The RAM half of the ledger

Evicting a model does not destroy it -- `model_unload` moves weights to the
model's `offload_device`, which is CPU RAM. VRAM pressure therefore *converts
into* RAM pressure, and the eviction entry point accounts for both at once:

```python
def free_memory(memory_required, device, keep_loaded=[],
                for_dynamic=False, pins_required=0, ram_required=0):   # :863
```

`get_free_memory` on a CPU device answers from
`psutil.virtual_memory().available` (`:1754`) -- a **system-wide** number, not
a ComfyUI-private one. That choice matters: anything else on the machine
eating RAM automatically shrinks what ComfyUI thinks it can offload, so the
honest shared measurement coordinates with the rest of the system for free.

## 3. The pin budget

To copy weights CPU→GPU at full PCIe speed, the source pages must be
**pinned** -- page-locked so the OS cannot swap them out. Pinned pages are
subtracted from the whole machine's flexibility, not just ComfyUI's, so an
unbounded pin pool starves everything else running.

ComfyUI runs an explicit budget for this: `MAX_PINNED_MEMORY` is derived from
total RAM (40% on Windows -- *"Windows limit is apparently 50%"* -- and up to
90% minus safety margins elsewhere, `:1585-1587`), `ensure_pin_budget(size)`
(`:714`) gates every new pin against it, and `free_pins` (`:695`) walks
eviction tiers when the budget is exceeded, with an emergency path keyed on
system-available RAM under Windows swap pressure (`:706`). `--high-ram`
disables the gate entirely.

This is real memory management -- budget, admission, eviction -- entirely
about RAM, and entirely separate from the VRAM ledger.

## 4 and 5, briefly

Both are covered where they bite:

- **Activation headroom** -- the temporary tensors created *during* a forward
  pass are never tracked; ComfyUI just reserves room for them up front
  (`minimum_inference_memory()` = 0.8 GB + `EXTRA_RESERVED_VRAM`,
  `:847-861`) and estimates per-operation needs before big ops like VAE
  decode. Details in [Reserved headroom](comfyui-memory.md#reserved-headroom).
  When the guess is short, the symptom is an OOM mid-sampling and the knob is
  `--reserve-vram`.
- **The allocator cache** -- PyTorch keeps freed VRAM in a private reusable
  cache rather than returning it to the driver. ComfyUI counts it as free
  (`mem_free_total = mem_free_cuda + (mem_reserved - mem_active)`,
  `:1785-1787`) and can flush it (`soft_empty_cache`, `:2045`). Correct for
  vanilla ComfyUI; the reason it complicates cross-process accounting is
  [Sharing one GPU](sharing-one-gpu.md).

## 6. The execution caches -- the RAM you forgot about

After a workflow finishes, ComfyUI keeps two caches so the next run can skip
unchanged work (`execution.py:136-141`, `comfy_execution/caching.py`):

- **`outputs`** -- every node's return values, keyed by a signature of the
  node's inputs. Images, latents, meshes: real tensors, held between runs.
- **`objects`** -- the node *instances* themselves, keyed by node id. This is
  where a V1 node's `self.`-stashed state lives (a loaded model a node cached
  on itself survives here between runs).

This is why RAM grows after a run completes and stays grown with zero models
loaded -- and it is retention policy, not the memory manager: nothing in
`model_management.py` can evict a cached output. The user-facing controls are
the cache flags (`comfy/cli_args.py:142-143`):

| Flag | Behaviour |
|---|---|
| *(default)* | `HierarchicalCache` -- keep outputs for everything still in the graph |
| `--cache-lru N` | keep the N most recently used node results |
| `--cache-none` | keep nothing; every node re-executes every run |

!!! note "Why comfy-env cares about #6"
    For an isolated node, the `objects` cache holds the parent-side **proxy**,
    while any `self.` state lives in the worker's real instance -- kept alive
    by the worker's own object cache for exactly this reason. And a cached
    `outputs` entry can hold shared-memory tensors that crossed the process
    boundary, which is what the consumed-ack lifetime protocol
    ([ADR-0032](adr/0032-shm-lifetime-consumed-ack.md)) exists to keep valid.

## What nothing manages

Completing the map with the memory ComfyUI cannot see or steer:

- **Mid-execution temporaries** beyond the reserved headroom -- plain PyTorch
  refcounting, gone when the tensor goes out of scope.
- **Non-PyTorch allocations** -- `cuMemAlloc`, TensorRT engines, NVENC,
  OpenGL: invisible to `torch.cuda` accounting entirely. Measured
  consequences in [Sharing one GPU](sharing-one-gpu.md#what-this-does-not-fix).
- **Other processes' VRAM** -- including, without comfy-env's proxies, a
  worker's models; that gap is the entire subject of
  [Sharing one GPU](sharing-one-gpu.md).

## Where to go next

- [How ComfyUI manages memory](comfyui-memory.md) -- #1 in full: the ledger,
  partial residency, the eviction loop, `get_free_memory`.
- [Sharing one GPU](sharing-one-gpu.md) -- what comfy-env's process boundary
  does to #1, #4 and #5, and the design that follows.
- [ADR-0036](adr/0036-mirroring-comfyui-memory-management.md) -- the precise
  version.
