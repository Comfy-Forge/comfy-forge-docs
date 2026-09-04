# comfy-env's memory management

Your packs run in separate processes. Their models occupy the same card as
ComfyUI's, and neither side can see the other's allocations directly. This
page is the whole story: what makes that hard, what comfy-env does about it,
and what you can switch.

Parts 1 to 3 state the problem and were written before any of it was built.
They hold up, and several of the conclusions they led to did not, which is
noted where it matters. Part 4 is what shipped, with the measurements behind
it. The decision record is [ADR-0038](adr/0038-the-memory-floor.md).

## ComfyUI background

The aim of comfy-env is to be completely invisible to ComfyUI, to let it manage VRAM as it already does across processes.
ComfyUI manages VRAM and RAM extensively, including models, whatever.
The rest of this page assumes that the user is already familiar with this crucial context.

**[If you're not, please read this page first](comfyui-memory.md)**.

Two processes, one card, one pool of VRAM, and neither can see the other's
allocations. This page explains how ComfyUI decides what to evict, what
breaks when half the models live in a subprocess, what Windows actually
does underneath, and the design that follows from those facts.

It is written to be read start to finish before any of it is built.

---

## The short answer: it is not foolproof

Asked plainly — is there a way to mirror ComfyUI's memory management
exactly? No. Not on Windows, and not with the interfaces available to us.

What *is* achievable is narrower and worth stating precisely. Two of these
three held up when built; the middle one did not, and Part 4 says what
replaced it:

- **The eviction target can be made exact.** ComfyUI's own arithmetic can be
  made to compute the right number. What shipped goes further than this page
  originally proposed: comfy-env reproduces upstream's expression rather than
  correcting its own, because every place it re-derived that shape it drifted.
- **The decision about *which* models to evict can be made correct** by
  splitting it in two, comfy-env deciding about its own models and ComfyUI
  about its own. ~~Neither can do the other's half.~~ **This was dropped.**
  Splitting the decision required registering a stand-in for a worker's model
  in ComfyUI's ledger, which was the source of every loud breakage comfy-env
  has had. Workers now release on their own instead, and the host is told to
  reserve rather than asked to reclaim.
- **The measurement can be made honest.** It can, and the honest number turned
  out not to be the one this page assumed: ComfyUI's own ledger reads zero for
  a paged model, so a worker reports what it physically holds instead.

And what stays broken no matter what we build:

- We cannot see memory a pack allocates outside PyTorch.
- We cannot detect the failure this whole system exists to prevent.
- A third process can invalidate our measurement between taking it and
  acting on it.

Those are in [What this does not fix](#what-this-does-not-fix). Read that
section before treating any of this as finished.

---

## Part 1 — How ComfyUI decides

Everything ComfyUI knows about resident models is one module-level list:

```python
current_loaded_models = []   # comfy/model_management.py
```

When something needs room, it calls `free_memory(memory_required, device)`.
Stripped to its bones (`model_management.py:863-894`):

```python
def free_memory(memory_required, device, keep_loaded=[], ...):
    can_unload = []
    for i in range(len(current_loaded_models) - 1, -1, -1):
        shift_model = current_loaded_models[i]
        if device is None or shift_model.device == device:
            if shift_model not in keep_loaded and not shift_model.is_dead():
                can_unload.append((-shift_model.model_offloaded_memory(),
                                   sys.getrefcount(shift_model.model),
                                   shift_model.model_memory(), i))
                shift_model.currently_used = False

    for x in sorted(can_unload):
        i = x[-1]
        memory_to_free = memory_required - get_free_memory(device)   # :883
        if memory_to_free > 0 and current_loaded_models[i].model_unload(memory_to_free):
            unloaded_model.append(i)
```

Three properties matter, and every design decision downstream follows from
them.

**It is a feedback loop, and `get_free_memory` is its progress meter.**
`memory_to_free` is recomputed *inside* the loop, once per candidate. Evict
something, free memory goes up, the remaining shortfall goes down, and when
it reaches zero the `> 0` guard stops the loop. That is how it evicts the
*minimum* rather than everything.

**Eviction escalates.** `model_unload` (`:806-815`) first asks the model to
give back only what was asked:

```python
if memory_to_free < self.model.loaded_size():
    freed = self.model.partially_unload(self.model.offload_device, memory_to_free)
    if freed >= memory_to_free:
        return False          # enough — keep the model registered
self.model.detach(unpatch_weights)
return True                   # full unload — caller pops it from the list
```

A short return is a designed signal, not a failure: it escalates to a full
detach. Note the guard — when `memory_to_free >= loaded_size()`, the partial
path is skipped entirely and the model goes straight to `detach()`.

**Victims are sorted by how much is already offloaded.** The first sort term
is `-model_offloaded_memory()`, i.e. `model_size() - loaded_size()`. Models
that are *already* mostly on the CPU are evicted **first**, because they are
the cheapest to finish evicting. Hold on to that; it becomes a problem later.

---

## Part 2 — What a subprocess breaks

comfy-env runs each node pack in its own process with its own environment.
When a pack loads a model, the weights land on the GPU — but in *that*
process. ComfyUI's ledger knows nothing about them.

So comfy-env creates a stand-in. For each worker-resident model, a
`SubprocessModelPatcher` is inserted into the parent's
`current_loaded_models`. It holds no weights. It answers questions about
size and residency, and forwards eviction requests over IPC to the worker,
which performs the real unload.

The intent is that worker models become ordinary citizens of ComfyUI's
eviction logic. The reality is two blind spots.

**Blind spot one: the progress meter doesn't move — on Windows.** When
ComfyUI evicts a worker model, real VRAM is genuinely freed, in the worker's
process. Whether the parent notices depends on the platform, because its
free number has two independent parts:

```python
mem_free_total = mem_free_cuda + (mem_reserved - mem_active)   # :1776-1778
```

The right-hand term is the *parent's own* allocator cache. A worker freeing
memory never moves it, on any platform. The left-hand term comes from
`mem_get_info` — and on Linux that is device-wide free, so a worker's frees
**do** show up there. On Windows they do not (Part 3).

So on Linux the feedback loop works and the loop terminates correctly. On
Windows the signal is severed for exactly the models comfy-env added: it
evicts one, sees no progress, evicts the next, sees no progress, and keeps
going until the candidate list is empty.

**Blind spot two: on Windows the meter cannot see the worker at all**, even
before any eviction. This is the deeper one, and it needs its own section.

### What about RAM?

ComfyUI manages host memory too, and comfy-env does not mirror that half at
all.

Eviction does not delete a model — it moves it to `offload_device`, i.e. CPU
RAM. VRAM pressure therefore converts into RAM pressure, which is why
`free_memory` takes `ram_required` and `pins_required` alongside
`memory_required` (`:863`), why `get_free_memory` on a CPU device returns
`psutil.virtual_memory().available` (`:1745`), and why there is a separate
pinned-memory budget (`ensure_pin_budget`, `free_pins`) with Windows-specific
swap-pressure logic (`:701-711`). Pinned memory is page-locked and cannot be
swapped by the OS, so an unbounded pin pool starves the whole machine.

comfy-env's proxy answers `is_dynamic() → False`, which deliberately excludes
it from every pin and RAM-eviction path. That is the right call today — those
paths assume a real patcher holding real weights.

**The RAM half degrades more gracefully than the VRAM half, and for an
instructive reason.** `ensure_pin_budget` measures against
`psutil.virtual_memory().available`, which is *system-wide*: it already
counts memory our workers hold. So when workers consume host RAM, ComfyUI's
pin budget shrinks on its own and it pins less. The honest, shared
measurement does the coordination for free — precisely what `mem_get_info`
fails to do for VRAM on Windows.

What remains is narrower: ComfyUI cannot tell that some of that consumed RAM
is worker model weights which *could* be released, so it can never ask for
them back — it can only back off itself. Failing toward under-pinning is the
safe direction, so this is a limitation rather than a bug, and nothing in
this document addresses it.

---

## Part 3 — What Windows actually does

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

**What the number actually is.** `mem_get_info` on WDDM reports the calling
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

**And the failure is silent, and lands on someone else.** This is the
finding that reframes the whole problem. A process that over-allocates on
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

---

---

## Part 4 — What comfy-env actually does

!!! success "This is the part that shipped"

    Everything above states the problem. Everything below is the answer
    as built and measured, superseding the design and the work plan this
    page used to carry. The decision record is
    [ADR-0038](adr/0038-the-memory-floor.md).


Your packs run in separate processes. Their models occupy the same card as
ComfyUI's, and neither side can see the other's allocations directly. This
page is what comfy-env does about that, what you can switch, and what each
setting actually costs.

The design and the measurements behind it are
[ADR-0038](adr/0038-the-memory-floor.md).

## What it does, in one sentence

comfy-env does at runtime what `--reserve-vram` does at launch: it keeps
ComfyUI honest about how much of the card is really available, asks ComfyUI
to free its own models when a pack needs room, and has packs let go of VRAM
when they go quiet.

It does not patch ComfyUI to do any of that. It reads values ComfyUI already
exposes, writes one number ComfyUI already reads, and calls two of its
public functions.

## The one setting

```
COMFY_ENV_MEMORY_MANAGEMENT=auto     # default
```

An ordered level, not a set of flags, because the features are not
independent: every pin feature is downstream of paging, since ComfyUI's
eviction skips models that are not dynamic.

| Level | What it turns on |
|---|---|
| `off` | Nothing. Packs load models the way any process would |
| `ledger` | The reserve, the ask path, and idle release. ComfyUI's own low-VRAM streaming handles big models |
| `paged` | comfy-aimdo pages weights per layer, so a pack holds a fraction of its model resident. Prompt marks come with this level, never separately |
| `shared` | Packs also return pinned system RAM when the machine is short of it |
| `auto` | The highest level your ComfyUI and environment actually support |

### What each level needs, measured

Computed by sweeping comfy-env's contract over 5866 upstream commits
(`research/memory-floor/sweep_contract.py`); re-run it rather than trusting
this table:

| Level | Works with ComfyUI from | Bounded by |
|---|---|---|
| `ledger` | ~September 2024 | `EXTRA_RESERVED_VRAM` |
| `paged` | between late 2025 and early 2026 | `ModelPatcherDynamic`, plus comfy-aimdo 0.4.10 |
| `shared` | mid 2026 | `free_pins` |

The floor reaching back two years is the point of the design. Features that
need a recent ComfyUI degrade to it **by name** rather than failing.

### When it drops a level, it says so

An **unrequested** demotion is loud:

```
[comfy-env] memory management: auto selected ledger: comfy-aimdo is not
usable in this worker environment
```

A **requested** one is silent. Choosing `ledger` on a RAM-poor machine is a
decision, not a fault, and warning about it every time would train you to
ignore the channel that carries the real demotions. Four environments on the
development machine were silently running a different memory manager than
their host, which is the failure this polarity exists to prevent.

Asking for a level the host cannot support runs the highest it can and says
which requirement was missing, with the ComfyUI commit that would provide
it. It never refuses to start a pack over an unavailable memory feature.

## Why `ledger` is a real choice, not a fallback

It is not simply "paged minus paging":

* **Zero pinned system RAM.** Paging costs roughly twice the model size in
  host RAM for pinned buffers. On a RAM-poor machine that is the dominant
  cost, and `ledger` avoids it entirely.
* **Big models still run.** ComfyUI's own low-VRAM streaming loads what fits
  and pulls the rest per step. Paging buys residency and speed, not
  feasibility.
* **The widest compatibility of any level**, by about eighteen months.

## The optional observer

```
COMFY_ENV_MEMORY_OBSERVER=on         # default: off
```

Two signals exist only inside ComfyUI's loaded-model list, and this is the
only way to hear them:

* **The Free-memory button.** With the observer off, that button frees
  ComfyUI's own models and silently leaves pack memory alone.
* **Host memory pressure.** Being asked to free is the only in-process
  notice that ComfyUI is short of VRAM. Idle release cannot cover this,
  because during an out-of-memory event the packs are not idle.

It is off by default because it is the one remaining piece with a breakage
history: both of comfy-env's loud failures in a year came through an object
registered in that list. This one is safer than its predecessor because it
reports holding *nothing*, which is true, so ComfyUI asks, gets zero and
moves on rather than relying on its numbers. It is still a coupling, so it
is a switch, and the switch is off.

## What you get, and what you do not

**You get:** packs and host workflows coexisting on one card; the card
coming back when a pack finishes; packs able to demand space from the host;
big models running.

**You do not get:** the host taking VRAM back from a pack that is currently
running. It avoids over-committing and waits for the pack to finish or go
idle. That is the deliberate price of not patching or impersonating
anything, and the one case it does not cover is a host out-of-memory event
while packs are busy.

## Reading the logs

| Line | Meaning |
|---|---|
| `memory management: auto selected <level>: <reason>` | A level below the maximum was chosen; the reason names what was missing |
| `admission tight env=... need=... true_free=...` | A pack asked for more than was free; the host was asked to evict |
| `idle release: <pack> gave back N GB` | A quiet pack returned its VRAM |
| `PIN REGRESSION env=... active_evicted=...` | Pins were taken from a model that was still in use. Should never appear; report it |
| `worker teardown env=... cause=...` | A pack's process was removed, with the reason |

## What this does not fix

Stated plainly, because the rest of this page is confident and these are the
places it should not be.

**We cannot see memory allocated outside PyTorch.** A worker's
`memory_reserved()` is blind to raw driver allocations — measured, 1,536 MB
allocated via `cuMemAlloc` moved the accounting gap by 1,536 MB with
`memory_reserved()` unchanged. Blender/Cycles, TensorRT, cuPy and NVENC all
allocate this way. Device-wide NVML *does* see it, so admission stays
correct; but comfy-env cannot attribute it to a worker, and therefore cannot
ask anyone to release it. Under pressure the only models we can evict remain
the ones we can see.

**We cannot detect the failure we are preventing.** Part 3's 53× collapse
happens in another process, with no signal available to us. Every decision
here is argued from a model validated by measurements taken *outside* the
running system. No test can prove the absence of this failure.

**A third process can invalidate the measurement mid-flight.** The
substitution in Part 4 is exact at the instant it is sampled. ComfyUI
re-reads `get_free_memory` on every loop iteration, and any unrelated
process creating a CUDA context re-partitions VidMm — 50 MB was enough.
Between our sample and ComfyUI's next iteration, the term can go stale.
There is no fix from inside comfy-env.

**The first load of each worker is a guess.** Step 8 replaces constants with
measured ratios, but the measurement needs a completed load to exist.

**Multi-GPU is untouched.** Everything routes through the singular
`get_torch_device()`. This work adds one more device-0 assumption to unwind
later.

**Host RAM is not addressed.** Worker models offload into the worker's own
RAM, outside ComfyUI's `ram_required` / pinned-memory accounting. This is
less dangerous than the VRAM equivalent — the pin budget measures against a
system-wide `psutil` figure that already sees worker RAM, so it backs off on
its own — but ComfyUI can never ask a worker to release host memory, only
decline to pin more itself.

**Cross-worker eviction still has a lock-ordering hazard.** Phase one does
targeted IPC to sibling workers from inside a budget callback. Snapshotting
the patcher dict and staying clear of the pool lock are necessary but not
sufficient; a real single-flight or ordered-lock discipline is still owed.

---

## Where to go next

* [ADR-0038](adr/0038-the-memory-floor.md) is the precise version of Part 4:
  the decision, the measurements, and what it supersedes.
* [Memory management context](memory-context.md) is the background this page
  assumes: upstream's manager, how operating systems differ, what aimdo does,
  and the full API inventory.

## Related records

- [ADR-0025](adr/0025-vram-co-management.md) — the original co-management
  protocol
- [ADR-0034](adr/0034-admission-by-arithmetic.md) — admission by arithmetic
  (contains claims this page corrects)
- [ADR-0035](adr/0035-duck-typed-model-proxy.md) — the duck-typed proxy
- [ADR-0024](adr/0024-upstream-interface-contract.md) — what we would ask
  upstream for, if asking were possible
