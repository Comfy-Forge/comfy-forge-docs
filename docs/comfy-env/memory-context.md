# Memory management context

Background for [comfy-env's memory management](memory-approach.md). None of
the design makes sense without at least the first page here, because ComfyUI
ships no documentation of its own memory manager and the whole problem is a
consequence of how that manager works.

These pages describe **upstream and the operating system**, not comfy-env.
They change when ComfyUI or the platform changes, not when comfy-env does.

## Start here

[**ComfyUI memory management background**](comfyui-memory.md)

How ComfyUI tracks models, decides what to evict, measures free memory, and
budgets pinned RAM. Read this before anything else. It also covers the two
eras of weight management, which is why the next page exists.

## The pager

[**How aimdo manages weights**](comfyui-aimdo.md)

comfy-aimdo pages weights per layer through a virtual address reservation
rather than loading them whole. It is what comfy-env's `paged` level uses,
and it behaves differently enough from the legacy path that most surprises in
this area trace back to it: its memory is invisible to torch, and its
headroom is fixed when its devices initialise.

## The platform underneath

Operating systems disagree about what "free memory" even means, and the
disagreement is load bearing here.

* [**Overview**](os-memory.md) — how the major platforms manage memory
* [**Kernel and driver differences**](kernel-differences.md) — what the GPU
  driver does on each
* [**Where the operating systems differ**](os-memory-differences.md) — the
  specific divergences that reach comfy-env

The one that matters most: on Windows WDDM the free-VRAM reading is the
calling process's own budget, while on Linux it is the whole device. That
single difference is why comfy-env's accounting has two branches.

## The surface, function by function

* [**The memory management API**](memory-api.md) — what ComfyUI offers a
  caller and what it demands of a model in return, and how comfy-env
  satisfies both from another process
* [**Memory API inventory**](memory-api-inventory.md) — every symbol on that
  surface with comfy-env's relationship to each, exhaustively

## Where this leads

Once you have the background, [comfy-env's memory
management](memory-approach.md) states the problem, what shipped, and what an
operator can switch. The decision record behind it is
[ADR-0038](adr/0038-the-memory-floor.md).
