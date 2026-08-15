# ADR-0034: Admission by arithmetic, never by `mem_get_info`

**Status:** accepted (2026-08-15). Supersedes the WDDM position and the
1.1-headroom rule in [ADR-0025](0025-vram-co-management.md).

## Decision

> **comfy-env decides VRAM admission from numbers it owns, and treats
> `torch.cuda.mem_get_info` as unusable for cross-process questions.**
> The parent measures true device-wide free (NVML -> `nvidia-smi` -> its
> own ledger), computes the parent's over-report as an *offset*, and
> passes ComfyUI a **pre-compensated** eviction target. The worker
> receives the true device-free figure and corrects its own view through
> the existing `extra_reserved_vram` channel.

## Context

ADR-0025 assumed free VRAM was observable. It is not, across processes,
on the majority platform.

`get_free_memory` derives its device term from `torch.cuda.mem_get_info`
(`model_management.py:1776`). On Windows/WDDM that call reports the
*calling process's* budget. Measured on RTX 4060 Ti 16 GB, driver
581.57, torch 2.10+cu128: a sibling process allocated 13.0 GiB;
`nvidia-smi` free fell 13,443 MB while the parent's `mem_get_info` free
fell **75 MB**. At 4 GiB the parent's delta was exactly **0 MB**.

ComfyUI's eviction loop computes
`memory_to_free = memory_required - get_free_memory(device)` and acts
only `if memory_to_free > 0` (`model_management.py:883,889`). With the
free term stuck near full-card, the difference is negative for any
realistic request: **`free_memory()` evicted nothing** when a worker
asked for room. Meanwhile the worker sized `lowvram_model_memory` from
the same blind call and *over*-loaded. Both sides overcommitted; the
driver's sysmem fallback silently absorbed it at roughly 10x slower.
comfy-env was paying ~450 lines and several upstream couplings for
behaviour indistinguishable from doing nothing.

Patching ComfyUI's `get_free_memory` was available and rejected: it is
a global mutation of another project's function on behalf of every
caller, including ComfyUI's own loads, with effects far outside
comfy-env's blast radius.

## Decision detail

**Measure.** `_true_device_free(device)` tries `pynvml`, then
`nvidia-smi --query-gpu=memory.free` (short timeout), then returns
`None`. NVML is device-wide on both platforms; `mem_get_info` is not.

**Fall back without a dependency.** When neither is available,
`_worker_held_bytes()` reconstructs the missing quantity from
comfy-env's own books: it already tracks every worker model's size and
residency, plus a per-worker constant. Less accurate than NVML -- it
cannot see allocations the `Module.to()`/`.cuda()` hooks never observed
(the gap ADR-0025 records) -- but it needs no new package and cannot
drift from upstream.

**Compensate rather than patch.** Pass
`free_memory(need + offset)` where `offset = blind_free - true_free`.
This is exact, not a fudge: the offset is worker-held memory, constant
across the eviction loop, and every parent-side unload moves the blind
and true numbers by the same amount. ComfyUI's internal comparison
therefore evaluates as if it could see the whole device, and the loop
still self-terminates at the minimum eviction. No over-eviction, no
reimplementation of upstream's victim selection.

**Reshape the headroom.** `size * 1.02 + 250 MB/worker +
minimum_inference_memory()`. The 1.1 multiplier was the wrong *shape*:
the dominant invisible cost is a per-process constant (CUDA context
~160 MB, cuBLAS ~38 MB, cuDNN ~16 MB) that a percentage of model size
does not cover, while allocator slack under the default cudaMallocAsync
backend measured ~1%, not 10%. The inference reserve was simply
missing -- worker loads got ~1 GB less headroom than identical
in-process loads.

**Correct the worker too.** The reply carries `device_free_bytes`; the
worker computes `its own get_free_memory - device_free` = what every
other process holds, and reserves exactly that. Blindness is
bidirectional, and fixing only the parent leaves the over-load half of
the bug in place.

## Alternatives rejected

- **Patch `mm.get_free_memory`.** Global, affects ComfyUI's own loads,
  unbounded blast radius.
- **Upstream API** (`register_external_vram` / a memory-lease
  protocol). The best design on the table and unavailable: this project
  has no route into ComfyUI core. Recorded in
  [ADR-0024](0024-upstream-interface-contract.md) as permanent rent.
- **Fixed per-worker budgets** (`set_per_process_memory_fraction`),
  abandoning elastic sharing. Genuinely simpler, and measurement showed
  the fraction *is* enforced on Windows -- which is the argument against
  it: it converts WDDM's soft failure (slow) into a hard OOM. For this
  audience a slow render beats a failed one. Kept as an opt-in escape
  hatch (`COMFY_ENV_WORKER_VRAM_BUDGET`), not the default.
- **Require pynvml.** Rejected as a hard dependency; it is the first
  rung of a ladder that ends in comfy-env's own ledger.

## Consequences

- Eviction actually runs on Windows. The protocol stops being ceremony.
- comfy-env now depends on NVML *or* `nvidia-smi` *or* the fidelity of
  its own hook-based accounting. The last is the weakest: a pack
  allocating outside `nn.Module.to()` is invisible, so the offset
  under-counts and admission stays optimistic. Logged per request with
  its source (`nvml` / `ledger`) so the degraded mode is visible.
- The reported numbers are now honest enough to be worth acting on,
  which makes offload latency the next bottleneck rather than a
  theoretical one.
- `mem_get_info` remains correct for *self*-accounting; nothing forbids
  it there. The rule is narrow: never use it to reason about another
  process.
