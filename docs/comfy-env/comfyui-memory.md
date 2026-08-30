# ComfyUI memory management background

*What a running ComfyUI holds on to, where it holds it, and what has to happen
before it lets go.*

*Last verified against ComfyUI `b133e483` (2026-08-26) with comfy-aimdo 0.4.15.*

You need this page before [Sharing one GPU](sharing-one-gpu.md).

## Why does ComfyUI manage memory?

ComfyUI runs a workflow as a graph.

Each node does its work, hands the result to the next one, and most of the time nothing about memory is worth thinking about.

IMAGE/GIF

It becomes worth thinking about when the graph is heavy.

A model checkpoint can be several gigabytes and a user's GPU/RAM might hold a fixed number of them: ask one workflow for a UNet, a VAE, a text encoder and two ControlNets and they might not all fit at once.

ComfyUI does things about it:

- It shuffles weights on and off the card while
the graph runs.
- It keeps room back for work whose size it cannot predict.
- It remembers what each node produced, in case you run the graph again.
- It retries in smaller pieces when something fails.

None of that is one system. Five different things hold memory during a run, they
are held for different reasons, and only the first three of them react at all
when you run short.

## Where memory lives

Before the five, the places. People say "RAM and VRAM" and that is close enough
until you start moving things, at which point it matters that RAM is really three
places and there is a fourth below it.

| Place | What it is | Getting a weight back from here costs |
|---|---|---|
| **VRAM** | on the card | nothing. It is already there |
| **Pinned RAM** | host memory the OS has promised not to move | the transfer, and nothing else |
| **Pageable RAM** | ordinary host memory | a staging copy the driver makes for you, then the transfer |
| **Page cache** | the checkpoint file, still in RAM because the kernel kept it | a page fault, then the transfer |
| **The file** | the safetensors on disk | reading it, though ComfyUI can read straight to the card and skip host RAM entirely |

This is why eviction is a move and not a delete. Pushing a model out of VRAM
puts it somewhere in that list, and where it lands decides what it costs to
bring back. It is also why VRAM pressure becomes RAM pressure: the bytes have to
go somewhere, and the somewhere is your machine's memory.

!!! note "Pinned and pageable are both real RAM"
    The difference is not where the bytes are, it is whether the kernel has
    promised to leave them alone. The card's copy engine reads host memory by
    physical address and needs that address to hold still for the length of the
    transfer. Pageable pages do not qualify, because the kernel may relocate or
    swap them at any moment, so the driver copies your data into a small pinned
    buffer of its own and transfers from there. Two steps instead of one, and
    the first is a synchronous copy on the CPU.

    That is the whole reason to pin, and the whole reason pinning has a cost:
    pages the kernel cannot move are pages the rest of the machine cannot have.
    Pinned RAM is the one place in this table with a budget. Pageable RAM has
    none at all.

!!! note "The page cache row is stranger than it looks"
    ComfyUI memory maps checkpoints, so a model can be resident in RAM without
    ComfyUI having allocated anything. The kernel counts those pages as
    available, and the caches in row three ask the kernel how much is available
    before deciding whether to evict. The process uses RAM it is already using
    to authorise using more.

## The five kinds

| # | Kind | What it is | Lives in | Held until |
|---|---|---|---|---|
| 1 | **Weights** | the models themselves | VRAM, or anywhere in the table above | something needs the space |
| 2 | **Work** | activations, attention buffers, tiling accumulators | VRAM, and RAM when tiling | the pass ends |
| 3 | **Results** | what each node returned, kept in case you run again | RAM, and VRAM if a node returned a GPU tensor | the next prompt, and only if system RAM is short |
| | | *above this line, something is watching and will act when memory runs short* | | |
| 4 | **State** | what a node kept on itself between runs | RAM | you submit a different workflow |
| 5 | **Everything else** | imports, native libraries, fragmentation | both | you restart ComfyUI |

Each row is here because it is held until something different happens. That is
the whole cut, and it is checkable: if you can name a sixth thing that releases
memory in a running ComfyUI, this list is wrong.

The line between rows three and four is the point of the table. Above it, code
exists that notices you are short and acts. Below it, nothing is watching, and
the memory stays until you take the process away.

!!! note "The order is not size order"
    It descends by how readily the memory comes back, not by how much of it there
    is. In an ordinary install the largest single consumer is row three.

The rest of this page takes the five in order. One thing cuts across all of
them, the number they all read, and that comes last.

## First: which memory manager are you on?

Row one has two possible answers, decided at startup, and ComfyUI tells you
which in the log.

| Log line | Manager |
|---|---|
| `DynamicVRAM support detected and enabled` | **aimdo**. Weights are paged in per layer. [Details](comfyui-aimdo.md) |
| `No working comfy-aimdo install detected... Falling back to legacy ModelPatcher.` | **the ledger**. Whole models, evicted whole |

aimdo is the default. `main.py:272` picks it unless one of four things stops it:
a flag (`--disable-dynamic-vram`, `--highvram`, `--gpu-only`, `--novram`,
`--cpu`), an unsupported GPU (NVIDIA, or AMD on ROCm 7.14 and later), torch below
2.8, or a failed init.

!!! warning "The fallback is being removed"
    Pass `--disable-dynamic-vram` and ComfyUI prints *"this argument will be
    removed soon."* The ledger is the minority path. It is not dead code: a CPU
    load device, `get_non_dynamic_delegate()`, `clone(disable_dynamic=True)` and
    multi GPU deepclones all still use it on a default install.

---

## 1. Weights

The models. This is the row everyone means when they say ComfyUI manages memory,
and it is the only row with a manager whose whole job is managing it.

### On the aimdo path, a loaded model is mostly a promise

Weights get virtual address space, which is free, and no physical pages. When a
layer runs, `fault()` commits VRAM for that weight; `unpin()` afterwards lets it
be reclaimed. Faulting a high priority weight evicts lower priority ones, and
priority is address order, so the application lays weights out in the order it
will need them.

An eviction sets a watermark. Faults above it fail fast, so a model does not
re-fault everything on every iteration. A failed fault is not an error: the layer
allocates a temporary, copies the weight in, and runs slower.

The point is that the application stops doing admission control. It asks every
time and checks the answer. [Full mechanism](comfyui-aimdo.md).

### On the ledger path, whole models, by budget

`load_models_gpu` computes a per model byte budget called
`lowvram_model_memory`, then hands it to the model. A model need not be all in or
all out: `partially_load` loads layers until the budget is spent and streams the
rest during the forward pass. That is all "lowvram mode" ever was, a small
budget rather than a separate mode.

!!! warning "Two magic numbers"
    | Value | Means |
    |---|---|
    | `0` | **Load everything.** Rewritten to `1e32`. |
    | `0.1` | **Load essentially nothing.** Set when the computed budget came out as zero, and under `NO_VRAM`. |

    The smallest possible request and the largest possible request are `0.1` and
    `0`, adjacent numbers with opposite meanings. Code that does
    `int(extra_memory)` turns `0.1` into `0` and inverts the instruction.

### Making room

When the budget does not fit, `free_memory` walks the ledger and unloads until it
does. The order it picks victims:

```python
can_unload.append((-shift_model.model_offloaded_memory(),
                   sys.getrefcount(shift_model.model),
                   shift_model.model_memory(), i))
```

Ascending, so the first victim is the one already most offloaded, since finishing
that eviction is the cheapest way to free the next byte. Ties break on refcount,
then size, then list position. It recomputes free memory every iteration, so it
is a feedback loop rather than a plan, and it stops as soon as there is room.

!!! note "Two consequences of that sort key"
    Refcount as a tiebreaker means row three is a term in row one's eviction
    policy. How many references a model has depends on what the results cache is
    holding. These are not independent systems.

    The last tiebreaker is list index, where index zero is the most recently
    loaded model. At the bottom of the sort, the policy is anti LRU.

!!! danger "On the default path this loop declines to act"
    ```python
    if current_loaded_models[i].model.is_dynamic() and for_dynamic:
        memory_required -= current_loaded_models[i].model.loaded_size()
        memory_to_free = 0
    ```
    `for_dynamic` is true when every model in the request is dynamic, which is
    the normal case. The loop evicts nothing, because aimdo is already doing it
    per page. This is where the ledger stops applying.

### Where evicted weights go

Down the table in [Where memory lives](#where-memory-lives), one rung at a time.
`--fast-disk` makes the bottom rung preferred over pageable RAM, so a weight can
go from the card to the file and come back without ever occupying host memory.

On a full unload the ledger entry is popped, so nothing tracks the copy that
lands in RAM.

Pinned RAM is the one place with a real budget: `MAX_PINNED_MEMORY`, with
`ensure_pin_budget` gating each new pin.

!!! warning "Three ways that budget is softer than it looks"
    * `--high-ram` returns `True` before reading it. It is off. The same flag
      also switches row three to classic mode, so one flag has two effects.
    * In the default configuration the branch taken never consults
      `MAX_PINNED_MEMORY` at all. It probes system available RAM instead.
    * `ensure_pin_registerable()` returns a value its callers discard, then pin
      anyway.

!!! warning "Nothing budgets pageable RAM"
    `free_memory` takes a `ram_required` parameter. It appears in one log string
    and no caller in the tree passes it. VRAM pressure converts into RAM pressure
    with no check that the RAM exists.

---

## 2. Work

Everything a forward pass allocates and then drops: activations, attention
workspaces, the buffers a cast needs, the accumulator a tiled VAE decode writes
into. It is freed by the pass ending, which means refcounting, which means
nothing decides anything.

Nothing measures it either. ComfyUI holds a constant back and estimates the rest.
The constant is `EXTRA_RESERVED_VRAM`, 400 MB, or 600 MB on Windows, plus 100 MB
more on Windows cards over 15 GB. `minimum_inference_memory()` adds 0.8 GB. On a
16 GB Windows card roughly 1.5 GB is spoken for before a single weight loads.

The knob differs by manager, which catches people out:

| Path | Knob | What it does |
|---|---|---|
| aimdo | `--vram-headroom` | keeps this much free, *"even counting VRAM from other apps"* |
| ledger | `--reserve-vram` | replaces `EXTRA_RESERVED_VRAM` |

On top of the constant sits an estimate, `area × dtype × memory_usage_factor`,
where the factor is one of roughly 55 hand tuned per architecture constants from
0.03 to 11.6, a dozen of them carrying a `#TODO`.

!!! danger "The estimator disagrees with itself"
    One of its two branches has no dtype term while the other scales by it. The
    branch test knows about xformers and pytorch flash attention but not sage or
    flash attn, so `--use-sage-attention` gets the quadratic attention estimate
    for a kernel that is linear in memory, and over reserves accordingly.

    Text encoders are budgeted at zero. Two of roughly forty implement an
    estimator, and their compute is forced to fp32.

That constant is a guess, so the guess is sometimes wrong. Thirteen places
outside the memory manager read free memory at runtime and change what they
compute: VAE batch size, CFG batching, attention chunk sizes, even the choice
between GPU and CPU. Ten more catch an out of memory error and retry smaller.
None of them tell the manager anything, and most of them discard the setting that
worked, so a fifty step workflow can rediscover the same fallback fifty times.

!!! warning "The fallback allocates more RAM than the thing it replaced"
    Tiled VAE decode writes into a float32 accumulator sized for the whole
    output. The untiled path honours `--fp16-intermediates`. The path you take
    when you are already out of memory does not.

---

## 3. Results

What each node returned, kept in case you run the graph again. In an ordinary
install this is the largest thing in the process, larger than any model.

It is freed by the next prompt, and only if system RAM is short: the cache polls
available RAM and drops entries by a score until it clears a target. That is a
real byte budget, which is more than rows four and five get.

!!! warning "Three sharp edges"
    * **A cached CUDA tensor counts as 0.05 bytes.** The accounting has a branch
      for CPU tensors and none for GPU tensors. There is a release callback for
      RAM and no equivalent for VRAM, so if cached results are your VRAM
      shortfall, nothing can act on it.
    * **`--cache-lru N` bounds item count, not bytes.** A hundred entries of
      unbounded size.
    * **The poll reads the host's memory inside a container.** Nothing in the tree
      reads a cgroup limit, so under `docker --memory` it sees the whole
      machine's RAM and never evicts.

---

## 4. State

What a node kept on itself. A core LoRA loader parks the whole state dict on
`self`, several gigabytes of it, and a hook loader does the same with a
checkpoint's patch weights.

Nothing polls this. It has no size accounting and no eviction under any mode. It
is freed when you submit a different workflow, which clears the entry, and not
before.

The practical shape of that: fill your RAM, keep running the same workflow, and
row three drops entries while row four does not move. The results cache reacts to
pressure. State does not react to anything except a change of graph.

!!! note "V1 and V3 nodes differ here"
    V3 nodes get a fresh class clone per call, so they cannot accumulate state
    across executions the way a V1 node can.

---

## 5. Everything else

Freed by restarting ComfyUI, and by nothing short of that.

* **Custom node imports.** Arbitrary code at import time, interned in
  `sys.modules` forever. There is no unload path anywhere in the tree, and
  exactly one function is protected against monkeypatching. See
  [Import time side effects](import-side-effects.md).
* **Allocations outside torch.** OpenGL framebuffers on the same card, FFmpeg
  decoder buffers, scipy trees, cuBLAS workspaces created by `--deterministic`.
  Invisible to every accounting scheme in the codebase.
* **Class level caches holding GPU tensors**, which outlive every workflow.
* **CUDA graph pools**, which `empty_cache()` cannot reclaim.
* **Fragmentation.** Free and unusable, measured nowhere.

---

## The instrument all five rows read

Every decision above is computed from one number:

```python
mem_free_cuda, _ = torch.cuda.mem_get_info(dev)
mem_free_torch  = mem_reserved - mem_active     # torch's own cache
mem_free_total  = mem_free_cuda + mem_free_torch
```

That second term is VRAM torch has reserved and is not using.

!!! danger "It is counted as free. It is not reliably returnable."
    `reserved` minus `active` is the sum of free blocks, which may all be too
    small and too scattered to serve the next allocation. Measured on an RTX
    3090: after freeing every other block the number reported 124 MB,
    `empty_cache()` released nothing, and a contiguous 128 MB allocation still
    went to the driver.

    When every block is dead it releases everything. When live tensors pin every
    segment it releases nothing. Fragmentation is what decides, not the allocator
    backend. And it can never release row one on the aimdo path, because those
    weights were never in the caching allocator to begin with.

There is also more than one of this function. `ModelPatcher.get_free_memory`
returns the above plus what aimdo could reclaim on demand. Both are used, in the
same file, in the same batching decision.

## Why comfy-env has to care

comfy-env runs node code in separate processes, so a worker's models are row one
memory that ComfyUI's ledger cannot see. It registers a stand in so upstream can
evict a worker's model the way it evicts its own. The mechanism is in
[ADR-0036](adr/0036-mirroring-comfyui-memory-management.md), and the arithmetic
around it is [Sharing one GPU](sharing-one-gpu.md).

!!! note "The bridge works, and the ground has shifted under it"
    The stand in reports itself as non dynamic on purpose, so the bypass above
    does not skip it. But on a default install every host model *is* dynamic and
    therefore protected by that same branch, which leaves the worker's model as
    the only entry upstream can actually evict.

## How this page goes stale

* **`--disable-dynamic-vram` is removed.** Upstream says it will be. Half of row
  one becomes history rather than an alternative.
* **The aimdo init protocol changes again.** `main.py` already carries three
  versions behind fallbacks.
* **`current_loaded_models` stops being a list.** Everything comfy-env does
  registers into it.
