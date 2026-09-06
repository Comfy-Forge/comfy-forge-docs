# Why the system is imperfect

comfy-env registers a stand-in object in ComfyUI's loaded-model list for
every model a worker holds. It has to answer for memory it does not hold, and
four of its answers are not true. Here is each one, why it is that way, and
what it costs.

These are not bugs waiting for a fix. They are what standing in for an object
in another process costs, against an interface nobody wrote down. They go away
when [the upstream hook](memory-approach.md#the-ask-if-you-are-reading-this-from-upstream)
exists, and not before.

## 1. It says a paged model is not paged

ComfyUI asks every entry in its list `is_dynamic()`: is this a model managed
by comfy-aimdo, which pages weights per layer, or a legacy one that lives on
the card whole? The stand-in always answers **False**, even when the worker's
model really is paged.

**Why.** Answering True opens a door. ComfyUI then reads `dynamic_pins` off
the object, a dictionary keyed by device holding six positional tuples, and
walks it after every node to manage pinned host RAM. That layout is internal,
it changed twice in one year, and getting it wrong does not raise an error;
it corrupts an accounting the host uses to decide what to page. Answering
False keeps the stand-in out of the whole pinned-memory machinery, which is
where most upstream churn lives.

**What it costs.** ComfyUI reasons about a paged worker model as though it
were a legacy one. In practice this matters least where you would expect: the
worker runs its own copy of ComfyUI's manager and pages its own models
correctly, so the *behaviour* is right and only the host's *description* of it
is wrong. What is genuinely lost is that worker pinned RAM never enters the
host's pin eviction ladder, which is listed separately under
[what this does not fix](memory-approach.md#what-this-does-not-fix).

## 2. One number stands in for three questions

ComfyUI asks a loaded model three different things: how big is it
(`model_size`), how much of it is on the card right now (`loaded_size`), and
therefore how much is already offloaded (`model_offloaded_memory`). It uses
the third to decide which model to evict first, on the reasoning that a model
already half on the CPU is the cheapest one to finish evicting.

The stand-in answers all three from **one measured scalar**: the maximum of
what comfy-aimdo says the worker holds and what torch says it has reserved.

**Why.** Those are the only two honest numbers a worker can report, and they
overlap rather than add: a 4 GiB model measured 4.02 GiB in torch and 4.03 GiB
in aimdo at the same instant, so summing them would book 8 GiB for a 4 GiB
model. The maximum is the closest thing to truth available, and it is a single
number because that is what the worker can measure about itself.

**What it costs.** The eviction ordering is worse than it looks on paper.
ComfyUI believes it is choosing the cheapest victim and is really choosing
from an approximation. On a card with one host model and one worker model the
choice is between two things and the ordering barely matters; with several of
each it can evict something more expensive than it needed to.

## 3. An eviction it could not deliver is reported as done

Sometimes the stand-in cannot deliver an unload. When that happens, ComfyUI
is told the memory came back anyway.

**When a send fails.** A busy worker is not the problem. Nodes run one at a
time, so the host is not loading models while a pack's node computes, and a
worker blocked waiting on the parent for a memory budget still services
eviction commands from its receive loop (`_call_parent` handles
`model_to_device` and the partial load and unload commands). Two things
actually fail:

* the worker process is **dead**. comfy-env handles this correctly: its VRAM
  died with it, so reporting the model as offloaded is true;
* the worker is **alive and did not answer**. Wedged, deadlocked, or past the
  command timeout. The weights are still on the card, and this is the one
  that matters.

**Why it gets reported as done.** ComfyUI's own code, in
`model_management.py`:

```python
if memory_to_free < self.model.loaded_size():
    freed = self.model.partially_unload(self.model.offload_device, memory_to_free)
    if freed >= memory_to_free:
        return False          # the partial unload was enough, keep it listed
self.model.detach(unpatch_weights)
return True                   # fully unloaded, caller pops it from the list
```

The stand-in honestly reports freeing zero bytes. Zero is less than what was
asked, so the code falls through to `detach()` and returns **True
unconditionally**. True means "this model is gone, remove it from the list",
and the caller does exactly that, while the memory is still resident in a
process nobody could reach.

`model_unload` has no way to express "I could not reach it". Its return value
is a boolean meaning "did you fully unload", and there is no third answer for
"ask me again later". A real in-process model can always be unloaded, so
upstream never needed one.

**What it costs.** ComfyUI's ledger loses track of resident memory. That is
worse than it sounds, because the entry being gone means the model is never
picked for eviction again, and every later admission decision is computed
against a card believed to have that much more free than it does.

comfy-env repairs it rather than preventing it. The stand-in keeps
`loaded_size` unchanged when a live worker did not answer, so ComfyUI keeps
escalating instead of believing the bytes came back, and the entry goes back
into the list at the next node boundary. The window is one node.

## 4. On Linux, its size is already counted

`get_free_memory` on Linux reports device-wide free memory, so every byte a
worker holds is already missing from it. The stand-in also reports those bytes
as its own size. Anything that adds the two together counts the same memory
twice.

**Why it is usually harmless.** ComfyUI's eviction arithmetic is
`what I need minus what is free`, and the sizes in the list are used only to
order the candidates, never to compute the target. So the double count has
nowhere to land on the normal path.

**Where it does land.** Node code outside `model_management.py` reads the
loaded-model list and hands entries straight back to `load_models_gpu`:
controlnet does it, three of the bundled extras nodes do it, and the multi-GPU
node reads deeper still. There, the stand-in's size does enter an admission
sum. comfy-env keeps it out by holding `currently_used` permanently False, so
the filtered version of that list never contains it, which works and is one
line away from not working.

## What would remove all four

Not a better stand-in. All four exist because the object must answer for
memory it does not hold, and no amount of care makes an approximation exact.

They are removed by ComfyUI gaining a way for an outside process to say how
much it needs kept free and to be asked to give memory back: two methods and a
registry, no object pretending to be a model, nothing in core that knows what
a subprocess is. That is written out at the end of
[comfy-env's memory management](memory-approach.md#the-ask-if-you-are-reading-this-from-upstream).
