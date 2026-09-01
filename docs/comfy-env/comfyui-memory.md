# ComfyUI memory management background

*What a running ComfyUI holds on to, where it holds it, and what has to happen
before it lets go.*

*Last verified against ComfyUI `b133e483` (2026-08-26) with comfy-aimdo 0.4.15.*

Start here. You need this page before [Sharing one GPU](sharing-one-gpu.md).

If page faults, swap, pinning or memory mapping are unfamiliar, take the detour
through [How operating systems manage memory](os-memory.md), which defines every
term this page borrows. Otherwise carry on.

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

These memory optimizations are crucial to the good functioning of ComfyUI, especially on consumer hardware, and comfy-env aims to maintain them all.

## Where memory lives

Before the six, the places. People say "RAM and VRAM" and that is close enough
until you start moving things, at which point it matters that RAM is really three
places and there is a fourth below it.

| # | Place | What it is | Getting a weight back from here costs |
|---|---|---|---|
| 1 | **VRAM** | on the card | nothing. It is already there |
| 2 | **Pinned RAM** | host memory the OS has promised not to move | the transfer, and nothing else |
| 3 | **Pageable RAM** | ordinary host memory | a copy into the driver's own pinned buffer, still in RAM, then the transfer |
| 4 | **Page cache** | the checkpoint file, still in RAM because the kernel kept it | the same as pageable, unless the kernel has dropped the page, in which case it is read from disk first |
| 5 | **Swap** | pageable memory the OS moved to disk without asking | reading it back off disk, then everything pageable costs |
| 6 | **The file** | the safetensors on disk | reading it, through a small pinned window rather than into a full copy in RAM |

Numbered by cost, cheapest first.
Rows 1 to 4 are in memory; 5 and 6 are on a disk.

This is why eviction is a move and not a delete. Pushing a model out of VRAM
puts it somewhere in that list, and where it lands decides what it costs to
bring back. It is also why VRAM pressure becomes RAM pressure: the bytes have to
go somewhere, and the somewhere is your machine's memory.

Pinned and pageable are both real RAM. The difference is whether the kernel has
promised not to move them, which is what lets the card read them directly rather
than through a staging copy.
[The background page](os-memory.md#pinned-memory) covers why.

Pinned RAM is the one place in this table with a budget, because pages the
kernel cannot move are pages the rest of the machine cannot have. Pageable RAM
has no budget at all.

## The six kinds

Everything ComfyUI holds falls into one of six, and each row is here because it
is released by something the other five are not.

| # | Kind | What it is | Where it lives | Released when | What manages it, and how |
|---|---|---|---|---|---|
| 1 | **Weights** | the models themselves | VRAM, or anywhere in the table above | pressure arrives | **A real manager**, and this is the one people mean. It is either **comfy-aimdo** or the **legacy ledger**, [decided at startup](#first-which-memory-manager-are-you-on), never both. Either way it reads free VRAM, free host RAM, a pinned quota, and on Windows the pagefile, then picks victims and evicts them. aimdo evicts a layer at a time, the ledger a whole model |
| 2 | **Work** | activations, attention buffers, tiling accumulators | VRAM, and RAM when tiling | the pass ends | **No manager, but not passive.** Thirteen sites read free memory and shrink what they compute: batch sizes, attention chunk sizes, tile sizes. Eleven more catch an out of memory error and retry smaller |
| 3 | **Carry** | cast staging buffers, CUDA graph pools, the static tensors a sampler reuses between steps | VRAM | the node returns | **No policy.** Released at the node boundary unconditionally, pressure or not |
| 4 | **Results** | what each node returned, kept in case you run again | RAM, and VRAM if a node returned a GPU tensor | the next prompt starts | **A second, separate manager**, in the execution engine. Reads free system RAM, drops cached outputs by a score |
| 5 | **State** | what a node kept on itself between runs | RAM | you submit a different workflow | **Nothing** |
| 6 | **Everything else** | imports, native libraries, fragmentation | both | you restart ComfyUI | **Nothing** |

### The last two columns are the whole story

**Released when.** Rows two to six are nested boundaries, each wider than the
last:

```
a forward pass  <  a node  <  a prompt  <  a workflow  <  the process
```

Memory survives until its boundary closes, and the boundaries contain one
another, so anything freed by a wider one was already safe from every narrower
one. Row one is the exception and the only one: weights are tied to no boundary
at all, and pressure can arrive at any moment inside any of them.

**What manages it.** Only two of the six have anything that measures, decides
and acts, and they are different managers with different sensors. Row one's is
either comfy-aimdo or the legacy ledger, which are two implementations of one
job rather than two jobs, and the next section is how you tell which is running.
Everything
else either reacts locally without keeping a total, or has no policy at all. So
"ComfyUI's memory manager" is accurate about row one and misleading about the
rest.

!!! danger "The two managers cannot see each other"
    Row one reads VRAM and evicts models. Row four reads system RAM and drops
    outputs. Neither can act on the other's memory, which is why a cached output
    holding VRAM is a problem nothing in the system can solve: the manager that
    can see VRAM cannot evict it, and the manager that owns it cannot see VRAM.

!!! note "Row one's several sensors are still one trigger"
    Free VRAM, free host RAM, a pinned quota and the Windows pagefile, through a
    four tier eviction ladder. Those are four measurements of one question, "is
    something short". Multiplying sensors does not multiply rows.

!!! note "Row four's poll has two rules, not one"
    Cached outputs from an **earlier prompt** are dropped after every node
    regardless of pressure, because the target gating that sweep is set to the
    machine's total RAM and can never be satisfied. Outputs from the **current
    prompt** are dropped only when free RAM falls below roughly two to ten
    gigabytes, and on the first pass only if the entry is at least half a
    gigabyte. So it reads like a pressure system and behaves, for stale entries,
    like a collector that runs at every node boundary.

!!! warning "Anyone can short circuit rows 1, 4 and 5 at any time"
    `POST /free` unloads every model and rebuilds both caches. The stock
    interface has a button for it. So "released when" says the **latest** a thing
    survives, not the earliest: an explicit request releases weights, results and
    node state immediately, with no pressure, no next prompt and no change of
    workflow.

!!! note "Two flags move a boundary"
    `--disable-smart-memory` retires weights at the end of every prompt rather
    than under pressure, which moves row one onto row four's boundary. And a V3
    node gets a fresh class clone per call, so row five does not apply to it at
    all: its state cannot outlive a single node.

The rest of this page takes the six in order. One thing cuts across all of them,
the number they all read, and that comes last.

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
layer runs, `fault()` asks for VRAM to be committed for that weight, and
`unpin()` afterwards says it is no longer in use so the memory may be taken back.
Faulting a high priority weight evicts lower priority ones, and priority is
address order, so the application lays weights out in the order it will need
them.

!!! note "Neither word means what it means elsewhere on these pages"
    An aimdo **fault** is a call the application makes on purpose, not the
    processor trapping because you touched unmapped memory. An aimdo **pin** is
    about whether VRAM is currently in use, not about locking host pages against
    swapping. The two operations also cost nothing alike: committing VRAM takes
    microseconds, while locking host pages runs at a couple of gigabytes per
    second. [The full comparison](comfyui-aimdo.md) is on the aimdo page.

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

Down the table in [Where memory lives](#where-memory-lives), one place at a time.
`--fast-disk` makes place six preferred over place three, so a weight is re-read
from the file rather than kept in host memory between uses.

!!! note "This is not GPUDirect Storage"
    The bytes still pass through host RAM. What changes is that they pass through
    a small ring of pinned buffers rather than landing in a full size copy in
    ordinary memory: the reader `pread`s a window into a pinned slot, sends that
    slot to the card, and rotates slots so reading and sending overlap. There is
    no `cuFile` and no `O_DIRECT` anywhere in the implementation.

    So it trades a persistent host copy for repeated reads. Worth it on fast
    storage, which is what the flag's help text says.

On a full unload the ledger entry is popped, so nothing tracks the copy that
lands in RAM.

Place two is the only one with a real budget: `MAX_PINNED_MEMORY`, with
`ensure_pin_budget` gating each new pin.

!!! warning "Three ways that budget is softer than it looks"
    * `--high-ram` returns `True` before reading it. It is off. The same flag
      also switches row three to classic mode, so one flag has two effects.
    * In the default configuration the branch taken never consults
      `MAX_PINNED_MEMORY` at all. It probes system available RAM instead.
    * `ensure_pin_registerable()` returns a value its callers discard, then pin
      anyway.

!!! warning "Nothing budgets place three"
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

## 3. Carry

The buffers a model reuses *between* forward passes, which is why they are not
row two. A sampler running fifty steps allocates these once and keeps them for
all fifty.

* **Weight cast staging buffers**, one per offload stream, each sized to the
  largest weight in the model. Two streams by default on NVIDIA and AMD.
* **A 16 GiB VRAM reservation** taken by the dynamic memory manager for the same
  purpose.
* **CUDA graph private pools.** A captured graph owns its intermediates for its
  whole lifetime and `empty_cache()` cannot reach them.
* **Static tensors a model parks on itself to survive steps**, such as the
  reused input buffers and rotary tables in the text encoders, or the device
  tensors an autoregressive audio decoder keeps for the whole loop.
* **Pinned host memory holding patch weights**, and prefetched weights whose
  pages are currently held resident.

All of it is released together, in a `finally` block wrapped around **one node's
execution**, so it survives every pass inside that node and nothing else.

!!! note "This row only exists on the aimdo path"
    The block is guarded on the dynamic memory manager being active, which is
    the default. On the fallback path most of these structures are not created
    at all, and the ones that are live until the process exits.

!!! warning "One caller, and no other way back"
    The function that releases these has exactly one call site in the whole
    tree. There is no pressure path to it and no periodic sweep. If a node never
    returns, none of this is ever freed, and a sixteen gibibyte reservation is
    not a rounding error.

## 4. Results

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

## 5. State

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

## 6. Everything else

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

## The instrument all six rows read

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
