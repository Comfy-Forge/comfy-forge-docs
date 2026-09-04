# Memory management

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

## Related

* [ADR-0038: The memory floor](adr/0038-the-memory-floor.md) — the decision
  and the measurements
* [Sharing one GPU](sharing-one-gpu.md) — the problem, explored before the
  design existed
* [ComfyUI memory management background](comfyui-memory.md) — how upstream
  works, which none of this makes sense without
* [How aimdo manages weights](comfyui-aimdo.md) — what `paged` is using
