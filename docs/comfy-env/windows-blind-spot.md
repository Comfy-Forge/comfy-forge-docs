# Why Windows needs its own branch

`get_free_memory` is how ComfyUI asks "how much VRAM is free". It is the
input to every memory decision it makes: how much to evict, how much of a
model to load, how large a batch can be. On Linux it answers about the card.
On Windows it answers about the calling process, and is blind to every other
one, which is a problem when comfy-env's whole design is other processes.

Three things in the shipping code exist only because of what follows: the
platform branch (`blind_free_is_process_local`), the offset compensation that
makes ComfyUI's eviction arithmetic terminate on Windows, and the rule that
the reserve declares nothing on Linux and a pack's whole residency on
Windows.

## The measurement

`get_free_memory` derives its device term from `torch.cuda.mem_get_info`.
On Linux that is device-wide free memory. On Windows it is not.

Measured on an RTX 4060 Ti 16 GB, driver 581.57, WDDM, torch 2.8.0+cu128 —
one sibling process growing while an observer polls:

| sibling holds | observer's `mem_get_info` free | `nvidia-smi` free |
|--------------:|-------------------------------:|------------------:|
| 2,560 MB | 15,221 MB | 13,107 MB |
| 7,168 MB | 15,221 MB | 8,499 MB |
| 11,264 MB | 15,221 MB | 4,403 MB |
| 14,336 MB | **15,188 MB** | **1,331 MB** |

The card is down to 1.3 GB physically free and the observer still believes
it has 15 GB.

### The Linux control, same probe, same day

The point of the table below is a contrast, so here is the other half of it,
run with the same code on an RTX 3090, driver 580.126.20:

| | holder's own free | sibling's own free | `nvidia-smi` free |
|---|---:|---:|---:|
| idle | 23,687 | 23,687 | 23,688 |
| holder takes 4 GiB | 19,591 | **19,329** | 19,592 |
| holder takes 10 GiB | 13,447 | **13,185** | 13,448 |

The sibling tracks the holder to within 2 MiB, and its own reading agrees with
`nvidia-smi` to within 1 MiB. VMM and legacy allocations behave identically
here too. So the assumption the whole Linux branch rests on, that
`reserve.charge()` may return zero because the host already sees what packs
hold, is correct, and is now executed rather than read.

Two numbers from that run are worth carrying elsewhere. A bare CUDA context
costs **264 MiB** on this 3090, against 119 MiB on the 4060 Ti, so the context
is a card and driver property and not a constant. And `cuMemGetInfo` reports a
total of **24,122 MiB** where `nvidia-smi` reports 24,576: 454 MiB of the card
is driver reserved and never appears in the CUDA total, so any arithmetic that
starts from the `nvidia-smi` total and subtracts is 454 MiB optimistic.

### Re-measured at the driver, 2026-09-06

The table above came through torch. Since the whole platform branch rests on
it, it was repeated on the same card against `nvcuda.dll` directly, with no
torch in the process at all, so that torch's caching allocator could not be
the explanation. `cuMemGetInfo_v2`, two processes, one allocating with
`cuMemAlloc_v2` and one only looking:

| | holder's own free | sibling's own free | `nvidia-smi` free |
|---|---:|---:|---:|
| idle | 15,223 | 15,223 | 15,948 |
| holder takes 4 GiB | **11,127** | **15,223** | 14,037 |
| holder takes 10 GiB | **4,983** | **15,223** | 14,101 |

The holder's own number tracks its own allocation exactly, to the MiB. The
sibling's number does not move at all, at either size. The card does move.
So this is not a torch artefact and not a rounding effect: the driver is
answering a different question depending on who asks.

The API family makes no difference, which is worth stating because it is the
obvious objection to the table above. comfy-aimdo does not page with
`cuMemAlloc`; it uses the CUDA virtual memory APIs, `cuMemCreate`, `cuMemMap`
and `cuMemSetAccess`. Repeating the experiment with those, and again through
aimdo's own `VRAMBuffer`, gives deltas identical to the legacy numbers to the
MiB, at both 4 GiB and 10 GiB. The holder sees its allocation, the card moves,
the sibling does not. VMM memory is charged to a WDDM process budget exactly
as legacy memory is.

One thing on the same card is NOT blind, and it matters: comfy-aimdo's pager.
With a sibling holding 8 GiB, aimdo's own pressure reading fell 8311 MiB and
tracked `nvidia-smi` to within 1 MiB, in the same process whose
`cuMemGetInfo` moved zero. It loads `nvml.dll` itself rather than going
through the CUDA driver, so on Windows the pager sees the card while ComfyUI
sees only itself. Nothing forwards that view to ComfyUI, which keeps asking
`mem_get_info`.

Two of its three sources are blind, though, so this is a default rather than a
property. `poll_budget_deficit` picks between a DXGI WDDM budget, `cuMemGetInfo`
and NVML, and logs which one prevailed. Measured with a 4 GiB sibling present:

| source | idle | sibling holding 4 GiB | sees it |
|---|---:|---:|:--:|
| DXGI WDDM budget | available 7798 MB | available 7798 MB | no |
| `cuMemGetInfo` | free 15203 MB | free 15203 MB | no |
| NVML | free 15927 MB | free 11712 MB | **yes** |

The DXGI budget line is byte identical idle and under load, so the WDDM budget
path is exactly as blind as the CUDA one. In the same process at the same
moment, `cuMemGetInfo` says 15203 MB free and NVML says 11712, a gap of
3491 MB. `comfy_aimdo.control.init` defaults `nvml_pressure` to False; ComfyUI
passes it True unless `--disable-nvml-pressure` is given. So the pager sees the
card because ComfyUI asks it to, not because it does by nature.

A second reading from the same run: a bare CUDA context, created and
otherwise unused, costs **119 MiB** device-wide here, reproducibly. That is
the floor before torch loads a single cuBLAS or cuDNN handle, which is why
comfy-env books more than it (see the context floor in row 3), and it is the
first time that constant has been measured on the platform that uses it.

## What the number actually is

 `mem_get_info` on WDDM reports the calling
process's *VidMm commitment budget*. It debits 1:1 for that process's own
allocations — exact to the megabyte across 0→6 GiB of self-allocation — but
is blind to other processes until the video memory manager re-partitions
budgets. And re-partitioning is triggered by **process and context lifecycle
events, not by memory pressure**: in the run above, a *third* process
starting up and allocating 50 MB was enough to move the number, while 14 GB
of sibling growth was not.

Two fixed biases fall out of this, both reproduced:

- While the budget is pinned, the reported free is **583 MB below** true
  device free (four reproductions, invariant, independent of the caller's
  own usage).
- Just after re-partitioning, it is roughly **533 MB above** it.

## The failure is silent, and lands on someone else

This is the finding that reframes the whole problem. A process that over-allocates on
WDDM is not punished:

| | before | after the parent took 12 GiB |
|---|---:|---:|
| parent's own bandwidth | — | **244.6 GB/s** |
| sibling's bandwidth | 237.2 GB/s | **4.5 GB/s** |

The parent allocated 8 GiB with 109 MB physically free, at full speed, with
no OOM and no slowdown. The *sibling* collapsed by 53×, because VidMm
demand-paged its working set out to system RAM.

Three consequences, and they are design constraints rather than preferences:

1. **There is no local signal.** No exception, no slow path, no counter.
   Invisible to the allocator, to `mem_get_info`, and to NVML — which
   returns `NOT_AVAILABLE` for per-process memory on every PID under WDDM,
   including the caller's own.
2. **Therefore "allocate optimistically and back off on failure" is
   impossible here.** There is nothing to catch. This also means ComfyUI's
   own OOM-recovery path is dead code on Windows.
3. **Errors must be one-sided.** Under-admitting costs a bounded, visible
   reload. Over-admitting costs a 53× collapse in a *different* process,
   which the user experiences as "ComfyUI is randomly slow" with nothing in
   any log.

## What comfy-env does about it

Three things, all of them consequences of the table above rather than
choices:

* **A platform branch.** `blind_free_is_process_local` decides whether the
  host's free-memory reading can see other processes. Everything downstream
  asks it before trusting a number.
* **Offset compensation.** When a pack asks the host to make room, comfy-env
  adds the bytes packs are known to hold to the eviction target it passes to
  `free_memory`. ComfyUI's own arithmetic then behaves as though its reading
  were device-wide, and its eviction loop terminates at the right point
  instead of running the candidate list dry.
* **A reserve that is platform shaped.** On Linux comfy-env declares nothing,
  because the host already sees what packs hold. On Windows it declares what
  each pack holds right now, because the host sees none of it.

## What it cannot do

Nothing here closes the third-process case. Any unrelated program creating a
CUDA context re-partitions the budgets, and 50 MB was enough to move the
number. Between the instant comfy-env samples the truth and the instant
ComfyUI reads its own figure again, the correction can go stale. There is no
fix for that from inside comfy-env, and there is no signal to detect it
having happened.

