# ComfyUI memory management background

*What a running ComfyUI holds on to, where it holds it, and what has to happen
before it lets go.*

*Last verified against ComfyUI `b133e483` (2026-08-26) with comfy-aimdo 0.4.15.*

Start here. You need this page before [comfy-env's approach to memory management](memory-approach.md).

If page faults, swap, pinning or memory mapping are unfamiliar, take the detour
through [How operating systems manage memory](os-memory.md), which defines every
term this page borrows. Otherwise carry on.

## Why does ComfyUI manage memory?

ComfyUI runs a workflow as a graph.

Each node does its work, hands the result to the next one, and most of the time nothing about memory is worth thinking about.


It becomes worth thinking about when the graph is heavy.

A model checkpoint can be several gigabytes and a user's GPU/RAM might hold a fixed number of them: ask one workflow for a UNet, a VAE, a text encoder and two ControlNets and they might not all fit at once.

ComfyUI does things about it:

- It shuffles model weights on and off the GPU VRAM while
the graph runs.
- It keeps room back for work whose size it cannot predict.
- It remembers what each node produced, in case you run the graph again.
- It retries in smaller pieces when something fails.

These memory optimizations are crucial to the good functioning of ComfyUI, especially on consumer hardware, and comfy-env aims to maintain them all.

## Where memory lives

People say "RAM and VRAM" and that is close enough until you start moving
things, at which point it matters that there are three places and then a fork.

### Two kinds of bytes, and it decides everything below RAM

**Anonymous** bytes exist only in RAM. A tensor you built, a buffer you
allocated. Nothing on disk backs them, so if the OS wants that memory back it
cannot simply discard them. It has to put them somewhere first.

**File backed** bytes came off a disk and have not been modified since. Your
safetensors. Here the OS has it easy: to reclaim the memory it deletes the pages
and the file still holds a perfect copy.

Same RAM, same speed, opposite fate under pressure.

### The places

<div class="num-col" markdown>

| # | Place | What it is | Getting a weight back from here costs |
|---|---|---|---|
| 1 | **VRAM** | GPU memory | nothing. It is already there |
| 2 | **Pinned RAM** | host RAM the OS has promised not to move | the RAM to VRAM transfer, and nothing else |
| 3a | **Pageable RAM, anonymous** | ordinary host RAM, holding bytes that exist nowhere else | a copy into the GPU driver's own pinned buffer, then the transfer |
| 3b | **Pageable RAM, file backed** | ordinary host RAM, holding a clean copy of bytes the disk also has | exactly the same as 3a |
| 4a | **Compressed** | 3a, squashed rather than written out. Still RAM, unreadable until decompressed | decompressing, then everything 3a costs |
| 4b | **Swap** | 3a, written out to a disk | reading it back off disk, then everything 3a costs |
| 4c | **The file** | what is left after 3b is dropped. The safetensors, where they always were | reading it, through a small pinned window rather than into a full copy in RAM |

</div>

### The fork

VRAM, pinned RAM and pageable RAM are where something is **put**. Compressed,
swap and the file are where it can **end up**.

!!! note "Compressed RAM is named once, in order to exclude it"
    `get_disk_swap_total()` sums `/proc/swaps` to size the pinned memory budget
    and skips any device whose name begins with `zram`
    (`model_management.py:1574`). Compressed RAM is not backing store, so
    counting it would let the budget promise pins against memory that cannot
    absorb a spill. It returns zero when `/proc/swaps` is absent, so this is
    Linux only by construction rather than by an OS check.

Pinned RAM is the one place in this table with a budget, because pages the
kernel cannot move are pages the rest of the machine cannot have. Pageable RAM
has no budget at all.

## The six kinds

Everything ComfyUI holds in memory falls into one of these six categories, and
each kind is here because it is released by something the other five are not.

This table is the index. You arrive knowing **what** the memory is, and what you
want is the **What frees it** column, which names a mechanism from the table
after this one.

**Present when** answers a different question: does this row describe your
install at all? Four flags and one hardware condition can delete a kind outright,
so two of these six do not exist on some perfectly ordinary setups. A row that
does not apply to you is worse than no row, because you will go looking for
memory that was never there.

<div class="num-col" markdown>

| # | Kind | What it is | Where it lives | What frees it | Present when |
|---|---|---|---|---|---|
| 1 | **Model weights** | the models themselves | VRAM, or anywhere in the places table above | **VRAM pressure** (M1) | always |
| 2 | **Work** | activations, attention buffers, tiling accumulators | VRAM, and RAM when tiling | **Refcount** (M2) | always |
| 3 | **Carry** | cast staging buffers, CUDA graph pools, the static tensors a sampler reuses between steps | VRAM | **Node end call** (M3) | the cast buffers need an offload stream, so NVIDIA or AMD, no `--disable-async-offload` and no `--cuda-malloc`. The 16 GiB reservation needs aimdo |
| 4 | **Results** | what each node returned, kept in case you run again | RAM, and VRAM if a node returned a GPU tensor | **Host RAM pressure** (M4) | not `--cache-none`, unless `--high-ram` silently overrides it |
| 5 | **State** | what a node kept on itself between runs | RAM | **Prompt start key sweep** (M5) | not `--cache-none`, unless `--high-ram` silently overrides it. Never for a V3 node |
| 6 | **Everything else** | imports, native libraries, fragmentation | both | **Nothing** (M6) | always |

</div>

## What frees it

The same facts, sorted the other way. Rows here are what **takes memory away**,
each one a function you can grep for.

<div class="num-col" markdown>

| # | Mechanism | Where it lives | What it frees |
|---|---|---|---|
| M1 | **VRAM pressure** | aimdo page eviction, or `free_memory` (`model_management.py:863`) | **1 Model weights** |
| M2 | **Refcount** | no call site. torch's allocator takes the blocks back into its own pool | **2 Work** |
| M3 | **Node end call** | `reset_cast_buffers` (`model_management.py:1418`) and `cleanup_prefetch_queues`, both from `execution.py:550` | **3 Carry**, plus Model weights' patch pins, plus part of Everything else |
| M4 | **Host RAM pressure** | `RAMPressureCache.ram_release` (`caching.py:550`), `free_pins` (`model_management.py:695`) | **4 Results**, and Model weights' pinned copies |
| M5 | **Prompt start key sweep** | `clean_unused` (`execution.py:756`) into `_clean_cache` (`caching.py:175`) | **5 State**. Results too, but only on the classic and LRU caches |
| M6 | **Nothing** | `sys.modules` interning at `nodes.py:2243` | **6 Everything else**, and `PromptQueue.history` |
| M7 | **Local LRU** | a feature's own free memory check, such as `model_animate2.py:149` | nothing in the six kinds |
| M8 | **Kernel reclaim** | no ComfyUI call site. the mmap at `utils.py:145` | the file backed half of **Model weights** |
| M9 | **On request** | `POST /free` into `main.py:424` | **Model weights**, **Results**, **State**, torch's pool |

</div>

**M1 to M6 line up with kinds 1 to 6.** Read either table and you can jump
straight to the matching row in the other. The numbering was chosen to make that
true, so M4 frees kind 4 and nothing else has to be remembered.

**M7, M8 and M9 fit no kind at all**, and that is where this page used to be
wrong:

* **M9 cuts across three kinds at once.** One request frees weights, results and
  node state, which is why it can only ever be a note beside the six and never a
  row among them.
* **M8 owns half of kind 1** and no ComfyUI code decides anything about it. A
  weight loaded under aimdo is a view into the mapped checkpoint, so the kernel
  reclaims those pages on its own schedule, and `MemAvailable` counts them as
  available. That half of a loaded model is invisible to every sensor on this
  page, which is why kind 1's manager is only half the story.
* **M7 frees nothing in the six.** It is a feature's private cache that behaves
  exactly like **Carry** and is released by its own check instead of the node
  boundary. No rule in the kinds table excludes it.

**M6 carries one thing the six kinds never mention.** `PromptQueue.history`
holds up to 10000 deep copied prompts (`MAXIMUM_HISTORY_SIZE`,
`execution.py:1249`), bounded by count rather than by bytes. `POST /free` does
not touch it, and `POST /history {"clear": true}` does.

!!! warning "Neither table is a partition, and that is why there are two"
    One kind is freed by several mechanisms: **Model weights** answers to M1, M4,
    M3, M8 and M9. One mechanism frees several kinds: M3 alone reaches Carry,
    Model weights' patch pins and part of Everything else, in forty lines.

    Sorting by kind tears M3 across four rows. Sorting by mechanism tears the
    pinned buffers across four rows. Whichever you pick, something is cut in
    half, so the page keeps both and lets you enter from whichever you have.

## Managed memory
Three of the six kinds of memory objects (**Model weights**, **Work** and **Results**) are concerned by memory conditions at all.
The other three are released without anything reading a number: **Carry** by M3,
**State** by M5, and **Everything else** by nothing at all, which is why M6 is a
row rather than a function. On a legacy install M3 never fires, so Carry has no
releaser there either.

Among the three, the split that matters is **central policy versus local
reaction**.

**Model weights** and **Results** each have a single thing that keeps a total,
measures it and decides.

**Work** has neither: it has sixteen call sites that
each read free memory and shrink their own work, and nine more that catch an
out of memory error and retry smaller, with nobody anywhere keeping a running
total. That is management, but it is management without a defined manager.

!!! note "M3 and M2 look alike from outside and are opposites"
    **Carry** is released **deliberately**: `reset_cast_buffers()` at
    `execution.py:550`, inside a `finally`, so it runs even when the node raises.

    **Work** is released by **nobody**. Nothing frees an activation when a pass
    ends. The tensors lose their last reference and torch's allocator reclaims
    the blocks into its own pool, so the memory stops being active, stays
    reserved, and never leaves the process. `get_free_memory` adds
    `mem_reserved - mem_active` back onto driver free to account for exactly
    this.

!!! warning "Carry is only released on the aimdo path"
    `reset_cast_buffers()` has one caller in the tree and it sits inside
    `if comfy.memory_management.aimdo_enabled` (`execution.py:547`). On the
    legacy ledger it never runs.

    That costs less than it sounds, and it costs something different for each
    thing the call would have cleared:

    * **Cast buffers** are held at their high water mark, not leaked.
      `get_cast_buffer` keeps one per offload stream and replaces it when a
      larger weight arrives, handing anything over 50 MB back to the driver at
      that moment. So the largest buffer ever needed stays resident for the life
      of the process instead of being freed at each node.
    * **Dirty mmaps** never accumulate at all. `_comfy_tensor_mmap_refs` is only
      set by `load_safetensors`, and `load_torch_file` only calls that when
      aimdo is enabled (`utils.py:164`). The legacy path loads through
      `safetensors.safe_open`, so `DIRTY_MMAPS` stays empty and there is nothing
      to bounce.
    * **Cross step state** persists on the legacy path. `_register_cross_step` is called
      from model code with no aimdo gate (`llama.py:876`, `gemma4.py:556`,
      `ar.py:267`) and nothing clears it. It is a `weakref.WeakSet`, so an entry
      does go away once its module is collected, but not at the node boundary.

!!! danger "The two managers share a channel for RAM and have none for VRAM"
    They do talk. Before it pins a tensor the **Model weights** manager calls the
    **Results** cache's release hook (`model_management.py:1628`,
    `pinned_memory.py:94`), and the cache's own eviction loop turns around and
    asks the weight manager to drop pins (`execution.py:805`). That channel
    carries host RAM in both directions.

    There is no equivalent for VRAM. So a cached output holding VRAM is the one
    shortfall nothing in the system can act on: the manager that can see VRAM
    cannot evict it, and the manager that owns it has no way to report it.

!!! note "Model weights has several sensors and one trigger"
    Free VRAM, free host RAM, a pinned quota and the Windows pagefile, through a
    four tier eviction order. Those are four measurements of one question, "is
    something short". Multiplying sensors does not multiply kinds.

!!! note "The Results poll has two rules, not one"
    Cached outputs from an **earlier prompt** are dropped after every node
    regardless of pressure, because the target gating that sweep is set to the
    machine's total RAM capped at 128 GB (`main.py:356`), so on any machine with
    less than that it can never be satisfied. Outputs from the **current
    prompt** are dropped only when free RAM falls below roughly two to ten
    gigabytes, and on the first pass only if the entry is at least half a
    gigabyte. So it reads like a pressure system and behaves, for stale entries,
    like a collector that runs at every node boundary.

!!! warning "Anyone can short circuit Model weights, Results and State at any time"
    `POST /free` unloads every model and rebuilds both caches. The stock
    interface has a button for it. So the **What frees it** column names the
    mechanism that acts on its own, not the only one that can act: M9 releases
    weights, results and node state on demand, with no pressure and no waiting
    for a node or a prompt to end.

!!! note "Five configurations move a kind onto a different mechanism"
    `--disable-smart-memory` retires weights at the end of every prompt rather
    than under pressure, moving **Model weights** off M1. And a V3
    node gets a fresh class clone per call, so **State** does not apply to it at
    all: its state cannot outlive a single node.

The rest of this page takes the six in order. One thing cuts across all of them,
the number they all read, and that comes last.

## Two eras of weight management

ComfyUI has had two approaches to **Model weights**, and the second arrived in a single
commit. Kind 3 and mechanisms M3 and M8 exist only under aimdo. Everything else on this
page is the same under both.

**The ledger came first.** `current_loaded_models` is a list of what is
resident, `ModelPatcher` wraps each model, and the unit of eviction is a **whole
model**. The manager estimates how much room a load needs, evicts entire other
models until that much is free, then loads.

**aimdo replaced the estimate with demand paging.** It landed on 2026-01-31 in
`f8acd9c4`, *"Reduce RAM usage, fix VRAM OOMs, and fix Windows shared memory
spilling with adaptive model loading"*. VRAM is reserved as address space, pages
are mapped when a layer faults, and the unit of eviction is a **page**. A loaded
model is mostly a promise. [How it works](comfyui-aimdo.md).

ComfyUI tells you which one you got, in the log:

| Log line | Manager |
|---|---|
| `DynamicVRAM support detected and enabled` | **aimdo**. Weights are paged in per layer |
| `No working comfy-aimdo install detected... Falling back to legacy ModelPatcher.` | **the ledger**. Whole models, evicted whole |

aimdo is the default. `main.py:272` picks it unless one of four things stops it:
a flag (`--disable-dynamic-vram`, `--highvram`, `--gpu-only`, `--novram`,
`--cpu`), an unsupported GPU (aimdo needs NVIDIA, or AMD on ROCm 7.14 and later), torch below
2.8, or a failed init.

!!! warning "The escape hatch is being removed. The ledger is not"
    `--disable-dynamic-vram` prints *"this argument will be removed soon"*
    (`main.py:599`). That notice is about the **flag**, not the code.

    The ledger stays, because ordinary workflows still reach it.
    `samplers.py:1204` calls `get_non_dynamic_delegate()` whenever a cond carries
    hooks, and `model_patcher.py:438` builds that delegate with
    `disable_dynamic=True`. A CPU load device
    and multi GPU deepclones take the same path.

    So "legacy" describes which era it belongs to, not whether it runs.

---

## 1. Model weights

The models. This is the kind everyone means when they say ComfyUI manages
memory. It and **Results** are the two kinds with a manager that keeps a total,
and it is the only one whose manager can move bytes rather than only drop them.

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
    `0`, adjacent numbers with opposite meanings. Any integer cast of the budget
    would collapse `0.1` to `0` and invert the instruction. Nothing in the tree
    does that cast today, and nothing prevents it either.

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
    Refcount as a tiebreaker means **Results** is a term in the **Model weights**
    eviction
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

On the ledger path, down the table in [Where memory lives](#where-memory-lives),
one place at a time. Under aimdo eviction has no destination: `vbar_free_memory`
unmaps the page and the bytes are gone. What it costs to get them back is set by
which host copy already exists, a pin, the mmap, or nothing and a reread.
`--fast-disk` makes the file (4c) preferred over a resident host copy, so a
weight is re-read from the file rather than kept in host memory between uses.

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

Pinned RAM is the only place with a real budget: `MAX_PINNED_MEMORY`, with
`ensure_pin_budget` gating each new pin.

!!! warning "Three ways that budget is softer than it looks"
    * `--high-ram` returns `True` before reading it. It is off. The same flag
      also switches **Results** to the classic cache, so one flag has two effects.
    * In the default configuration the branch taken never consults
      `MAX_PINNED_MEMORY` at all. It probes system available RAM instead.
    * `ensure_pin_registerable()` returns a value that four of its five callers
      discard and pin anyway. Only `pinned_memory.py:96` checks it.

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
| both | `--reserve-vram` | replaces `EXTRA_RESERVED_VRAM` on either path, and is handed to aimdo as its simple VRAM headroom (`main.py:71`) |

On top of the constant sits an estimate, `area × dtype × memory_usage_factor`,
where the factor is one of roughly 47 hand tuned per architecture constants from
0.03 to 11.6, a dozen of them carrying a `#TODO`.

!!! danger "The estimator disagrees with itself"
    One of its two branches has no dtype term while the other scales by it. The
    branch test names xformers and pytorch flash attention and nothing else, so
    which formula you get depends on whether `ENABLE_PYTORCH_ATTENTION` happens
    to be set rather than on which kernel will run. On NVIDIA it is set by
    default, so `--use-sage-attention` still takes the dtype scaled branch. Where
    pytorch attention is off, for example AMD without aotriton, every kernel gets
    the flat constant instead.

    Text encoders are budgeted at zero unless they define
    `memory_estimation_function`. Two files implement one, `ace15.py:366` and `lt.py:244`. Both
    estimate for fp32 and halve the constant when bf16 is available.

That constant is a guess, so the guess is sometimes wrong. Sixteen places
outside the memory manager read free memory at runtime and change what they
compute: VAE batch size, CFG batching, attention chunk sizes, even the choice
between GPU and CPU. Nine more catch an out of memory error and retry smaller, and two fall back to
a cheaper algorithm or to the CPU without shrinking the work.
None of them tell the manager anything, and most of them discard the setting that
worked, so a fifty step workflow can rediscover the same fallback fifty times.

!!! warning "The fallback allocates more RAM than the thing it replaced"
    Tiled VAE decode writes into a float32 accumulator sized for the whole
    output. The untiled path honours `--fp16-intermediates`. The path you take
    when you are already out of memory does not.

---

## 3. Carry

The buffers a model reuses *between* forward passes, which is why they are not
**Work**. A sampler running fifty steps allocates these once and keeps them for
all fifty.

* **Weight cast staging buffers**, one per offload stream, each sized to the
  largest weight in the model. Two streams by default on NVIDIA and AMD.
* **A 16 GiB VRAM address reservation** taken by aimdo for the same
  purpose.
* **Static tensors a model parks on itself to survive steps**, such as the
  reused input buffers and rotary tables in the text encoders, or the device
  tensors an autoregressive audio decoder keeps for the whole loop.
* **Pinned host memory holding patch weights**, and prefetched weights whose
  pages are currently held resident.

All of it is released together, in a `finally` block wrapped around **one node's
execution**, so it survives every pass inside that node. The same `finally` reaches further
than Carry: see M3.

!!! note "Carry needs aimdo and an offload stream"
    M3 is guarded on aimdo being active, which is the default. On the fallback
    path most of these structures are not created at all, and the ones that are
    outlive the node: cast buffers for the life of the process, at their high
    water mark, and cross step tensors for as long as the model that owns them.

    Separately, the cast buffers need an offload stream. With
    `--disable-async-offload` or on anything that is not NVIDIA or AMD,
    `get_offload_stream` returns `None` (`model_management.py:1461`) and the
    staging buffer becomes a per call `torch.empty` at `ops.py:158`, which is
    **Work**, not Carry.

!!! warning "One caller, and no other way back"
    The function that releases these has exactly one call site in the whole
    tree. There is no pressure path to it and no periodic sweep. If a node never
    returns, none of this is ever freed. The 16 GiB is a virtual reservation
    (`cuMemAddressReserve`), committed on demand in 16 MiB chunks by
    `vrambuf_grow`, so what is actually resident is the largest weight the node
    cast, rounded up, not 16 GiB.

## 4. Results

What each node returned, kept in case you run the graph again. In an ordinary
install this is the largest thing in the process, larger than any model.

On the default cache nothing frees a Results entry at the prompt boundary.
`RAMPressureCache.clean_unused` (`caching.py:535`) drops the parent's key sweep
and keeps only subcache cleanup, so an entry survives until host RAM pressure
picks it (M4) or you ask for it to go (M9). On `--cache-classic` and
`--cache-lru` the parent sweep runs and M5 applies instead. That is a
real byte budget, which is more than **State** and **Everything else** get.

!!! warning "Three sharp edges"
    * **A cache entry whose outputs are all CUDA tensors scores 0.05 bytes in
      total.** The size scan adds real bytes for CPU tensors and nothing for GPU
      tensors, so the entry keeps that baseline however much VRAM it holds, and
      that also puts it under the half gibibyte floor on the first pass. There is a release callback for
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

Nothing polls this and it has no size accounting. It is freed by the prompt start
key sweep (M5), and the key is the node id plus its class type, nothing else.
Changing a seed, a prompt string or any widget does not free it. Loading a
different workflow frees it only if no node in the new graph carries that same id
and class. What reliably clears it is `POST /free` with `{"free_memory": true}`,
which rebuilds the cache set (`execution.py:672`), or `--cache-none`, which makes
the objects cache a `NullCache` so no instance is retained at all.

The practical shape of that: fill your RAM, keep running the same workflow, and
**Results** drops entries while **State** does not move. The results cache reacts to
pressure. State does not react to anything except a change of graph.

!!! note "V1 and V3 nodes differ here"
    V3 nodes get a fresh class clone per call, so they cannot accumulate state
    across executions the way a V1 node can.

---

## 6. Everything else

Two of these are M6, freed by nothing: custom node imports and allocations made
outside torch. The other two are partly reached by the node end call (M3), whose
`soft_empty_cache` returns any segment with no live block on it. Fragmentation
that survives that is fragmentation live tensors are pinning, and nothing short
of a restart moves it.

* **Custom node imports.** Arbitrary code at import time, interned in
  `sys.modules` forever. There is no unload path anywhere in the tree, and
  exactly one function is protected against monkeypatching. See
  [Import time side effects](import-side-effects.md).
* **Allocations outside torch.** OpenGL framebuffers on the same card, FFmpeg
  decoder buffers, scipy trees, cuBLAS workspaces created by `--deterministic`.
  Invisible to every accounting scheme in the codebase.
* **Class level caches holding GPU tensors**, which no mechanism on this page reaches.
* **Fragmentation.** Free and unusable, measured nowhere.

---

## The number M1 and Work read

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
    backend. And it can never release **Model weights** on the aimdo path, because those
    weights were never in the caching allocator to begin with.

There is also more than one of this function. `ModelPatcher.get_free_memory`
returns the above plus what aimdo could reclaim on demand. Both are used, in the
same file, in the same batching decision.

## Why comfy-env has to care

comfy-env runs node code in separate processes, so a worker's models are
**Model weights** memory that ComfyUI's ledger cannot see. It registers a stand
in so upstream can evict a worker's model the way it evicts its own. The
mechanism is in [ADR-0036](adr/0036-mirroring-comfyui-memory-management.md), and
the arithmetic around it is [comfy-env's approach to memory management](memory-approach.md).

### Isolation is what costs you aimdo

A worker never runs `main.py`, and inside ComfyUI `aimdo_enabled` is set in
exactly one place: `main.py:300`, defaulting to `False` at
`memory_management.py:173`. Left alone, every isolated worker would therefore
resolve to the ledger. comfy-env closes that gap: `maybe_enable_aimdo`
initialises aimdo at worker start (`memory_manager.py:306`) whenever the wheel
imports and a CUDA device is visible. **A worker falls back to the ledger on
CPU, on a failed init, on a comfy-aimdo PROTOCOL difference against the host,
or when the level resolves below `paged`** -- see
[comfy-env's approach to memory management](memory-approach.md).

The wheel is there because comfy-env puts it there. It no longer waits for a
pack to declare `comfy-aimdo`: the host's ComfyUI imports `comfy_aimdo`
unguarded, so a worker without it cannot import `comfy.model_management` at
all, and the same is true of `comfy-kitchen`, which upstream imports unguarded
from four modules on the `comfy.model_patcher` chain. Both are injected into
every worker manifest at the host's own pin, whether or not the pack asked.
comfy-aimdo is skipped on CPU stacks, where it has no path; comfy-kitchen is
not, because `comfy/ldm/modules/attention.py` imports it on any stack.

Compatibility is judged on the PROTOCOL LEVEL the installed wheel supports,
read from `control.init`'s signature and `init_devices`' source, never on the
version string. comfy-aimdo ships about three releases a month while its
protocol moved twice in twelve, so exact-version equality dropped a worker to
the ledger on every host patch bump. Four of nineteen environments on the
development machine were in that state.

!!! warning "This is decided per pack, not per install"
    `wrap.py` falls back to plain in process import in five separate cases: no
    ComfyUI base found, no `comfy-env.toml`, an env stamp refusal, an
    unmaterialised env, and main process directories with no config. In that
    mode there is no worker at all, the node runs in the host process, and it
    gets whatever the host has.

    So one ComfyUI run can execute pack A under the ledger and pack B under
    aimdo, on the same device, because A's environment was built and B's was
    not. Nobody configures this. It follows from install state, and it can
    change between runs when someone materialises an env.

    comfy-env logs the resolved manager at worker start and states a mismatch
    once per process. There is no HTTP endpoint: registering one from
    `register_nodes` collides on the second pack, because ComfyUI flushes a
    single shared route table.

### What a worker never releases

Inside ComfyUI, `reset_cast_buffers` has one caller, `execution.py:550`, and a
worker does not run ComfyUI's executor. comfy-env therefore mirrors the release
at its own node boundary: `release_node_boundary` runs in a `finally` around
every worker call, and fires whenever aimdo is live in that worker, which is the
default. On a worker that fell back to the ledger nothing releases the cast
buffers, and they are held at their high water mark, `NUM_STREAMS` (2 on NVIDIA
and AMD) times the largest single weight, for the worker's life.

### Every process budgets pinned memory independently

`MAX_PINNED_MEMORY` is computed at import from total system RAM: 40% on Windows,
up to 90% elsewhere. Every process that imports `comfy.model_management`
computes its own and none of them knows about the others, so N workers plus the
host promise N plus one times that fraction of one machine's RAM. This is
unrelated to aimdo and it is true today.

### A CPU worker on the ledger is correct

aimdo has no CPU path. `ModelPatcherDynamic._vbar_get` returns `None` for a CPU
load device (`model_patcher.py:1797`) and `partially_unload` asserts a non CPU
device. So a worker started under `--cpu` resolves to the ledger because there
is nothing else it could resolve to.

That is the one difference between host and worker that is a fact rather than an
accident, and it is the reason comfy-env cannot simply follow ComfyUI in
treating aimdo as the only path.

!!! note "The bridge works, and the ground has shifted under it"
    The stand in reports itself as non dynamic on purpose, so the bypass above
    does not skip it. But on a default install every host model *is* dynamic and
    therefore protected by that same branch, which leaves the worker's model as
    the only entry upstream can actually evict.

    Flipping it would be worse, not better: a dynamic proxy gains that same
    protection, and `free_memory` then frees nothing at all. Over eviction is
    slow and visible. No eviction is an out of memory error with no cause in the
    log.

## How this page goes stale

* **`--disable-dynamic-vram` is removed.** Upstream says it will be, and issue
  #15285 is open asking them not to. Note what goes: the **flag**, not the
  ledger. A CPU load device has no aimdo path at all, so the ledger cannot be
  deleted without deleting CPU support.
* **`RAMPressureCache` stops overriding `clean_unused`.** One deleted method
  puts **Results** back on M5 and makes half of section 4 wrong.
* **The cache flag chain at `main.py:362` is reordered.** `--high-ram`
  overriding `--cache-none` is precedence, not intent, and the **Present when**
  column depends on it.
* **`current_loaded_models` stops being a list.** Everything comfy-env does
  registers into it.
* **The worker aimdo default changes again.** Injection and worker side
  initialisation both happen by default, and this section was rewritten
  against that. The stale risk now runs the other way: an install that
  resolves below `paged`, deliberately or because its ComfyUI is too old,
  behaves as the pre 2026-09 text described. That mode is documented in
  [comfy-env's approach to memory management](memory-approach.md) rather than here.
