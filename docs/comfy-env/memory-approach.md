# comfy-env's memory management

ComfyUI manages RAM and VRAM to optimize for speed and stability on all kinds of hardware.

Unfortunately, every bit of its current strategy assumes that everything is running in one process.

comfy-env isolated nodepacks instead run in separate subprocesses, and models occupy the same RAM and GPU/accelerator memory as ComfyUI's. Neither side can see the other's allocations directly.

Two rules were meant to keep this honest:

1. **Do not patch the host.** Replace no function, hook no class. Upstream
   ships a file whose entire job is to undo custom node patching, and it
   runs on a timer. Build on that and you have built on sand.
2. **Do not pretend to be something you are not.** No object of ours
   impersonating one of theirs. Anything that works by imitating an
   interface nobody wrote down is stable until they change their minds,
   correct until you have to lie about one field, and maintainable until
   the person who wrote it moves on.

Unfortunately while we were able to keep the first rule, we could not avoid breaking the second one to guarantee a modicum of usability for comfy-env.

Nothing outside a process can free that process's memory. The host has to be
able to ask, and the only place ComfyUI asks anything is the list it walks in
`free_memory`. So there is one object of ours in that list per worker model.
It holds no weights. When ComfyUI evicts it, it forwards the request over IPC
and the worker does the real unload.

That is the compromise, it is the only part of comfy-env's design that we truly dislike, and the next section is the case against it.

### Why that second rule is a compromise, and what would replace it

The stand-in is not a design we would choose. It is what is available to
guarantee functioning, and it fails all three of the tests in rule two.

**It is not stable.** It has to answer eighteen attributes of ComfyUI's
internals, none of which upstream ever promised to keep stable, and both of comfy-env's
user-visible breakages in a year were a new attribute read landing on it
during someone's workflow. The eviction loop grew a whole new branch when
comfy-aimdo landed; `loaded_size` was reimplemented for the paged patcher;
the pinned-memory tuple layout it must not touch moved twice in one year.
None of those were breaking changes to anyone else, because none of it is an
interface.

**It is not correct.** It has to answer for memory it does not hold, and
three of its answers are not true: (1) it tells ComfyUI a paged model is not
paged, to stay out of the pinned-memory paths where the churn lives; (2) it
answers three different size questions from one measured number; and (3) on
Linux the size it reports is already counted in the host's own free figure.
Each is deliberate, each has a cost, and they are worked through in
[why the system is imperfect](why-imperfect.md).

**It is not easily maintainable.** There is no contract to check against, so
the way we track upstream is a test that greps ComfyUI's source for the
places it reads a list entry and fails when that set moves. That catches a
change once we have the new ComfyUI in front of us. It cannot catch it
before a user does, which is exactly how both breakages were found: not by
our test suite, which passes against the version it was written for, but by
somebody's workflow stopping mid-run.

We went looking for alternatives properly, and the search is closed:

* `load_models_gpu` and `free_memory` call out to exactly three things:
  entries in that list, the pinned-memory helpers, and `logging`. There is
  no callback, no event and no registry on either path.
* The operating system cannot substitute. There is no push notification for
  device memory anywhere: NVML's event API has no memory bit, CUDA has no
  callback, and VRAM is charged to no cgroup, so kernel pressure primitives
  never see it. Polling device free is a lagging indicator, and per-process
  attribution, which is what makes it usable at all, is unavailable on
  Windows.
* Host-side precursors are too late. On ComfyUI's real loading path, host
  RSS leads the device allocation by 0.1 s, because the copy to the card is
  what faults the pages.

**What we actually want is a hook in ComfyUI.**
Something small: a way for software outside the process to say how much of the card it needs kept
free, and to be asked to give memory back when the host runs short. Two
methods, a registry, no knowledge of subprocesses in core, and no object
pretending to be a model. `free_memory` would consult registered holders
after its own models, exactly where it consults the pinned-memory helpers
today. Upstream has accepted this shape before, for the results cache and
for external pinned-memory pressure.

Until that exists, the stand-in stays, because the alternative is that a
pack's memory cannot be reclaimed at all. The rest of this page is largely
about keeping it small enough to survive upstream changing things
underneath it.

## ComfyUI background

The (currently unattainable) aim of comfy-env is to let the already optimized and tested ComfyUI memory code manage RAM and VRAM in custom nodepacks subprocesses as it already does for its own host process.

The rest of this page assumes that the user is already familiar with native ComfyUI memory management.

**[If you're not, please read this page first](comfyui-memory.md)**.

ComfyUI's memory management can be summarised as a module-level
list of loaded models and cached results on RAM and VRAM, plus arithmetic about what gets thrown out of it when we run out of RAM/VRAM.

```python
current_loaded_models = []   # comfy/model_management.py
```

Everything else hangs off that one fact:

- ComfyUI streams weights per layer when a
model does not fit
- It holds back a reserve for work it cannot size in
advance
- It pins host RAM so transfers are fast
- It runs paging through comfy-aimdo
- It caches what every node produced so a re-run skips the work.
- ....

It is truly good code, well tested and runs on all operating systems on hardware ranging from a shitty laptop to an H100 server.
Within its category, ComfyUI's memory management is SOTA.

Unfortunately for us, it also assumes that there is ever only exactly one process.

**Two processes do not share an address space by default.** Sharing bytes is
possible in principle and comfy-env does it for CPU tensors with
`share_memory_()`. For GPU tensors it mostly cannot: ComfyUI turns on
PyTorch's async CUDA allocator by default and a worker inherits that, and
handles from that allocator will not export, so a GPU tensor crossing the
boundary is copied.

What does not survive the boundary is bookkeeping.

One thing dominates everything else:

- **Making room means walking `current_loaded_models` and asking each entry
  to unload.** That is the main job memory management has. A pack's models
  live in another process, so unless something of theirs is in that list,
  the host can decline to take memory it does not have, which is useful, but
  it cannot take memory back, which is the half that matters when the card
  is already full.

  So comfy-env puts something in the list: one stand-in per worker model,
  which forwards the unload over IPC and the worker performs it. That is why
  the Free button works, why the out-of-memory handler reaches packs, and
  why a host load can evict a pack's model instead of failing. It is also
  the only part of comfy-env that upstream can break by changing something
  unrelated, which it has done twice.

Two more need machinery that does not exist, which is a different claim from
impossible:

- **The node output cache can only cache what it can reach.** There is one
  cache and it lives in the host; workers do not run the execution engine at
  all. A pack's results therefore have to be copied across the boundary to be
  cached, so the bytes exist twice. And the eviction trigger reads
  machine-wide free RAM (`psutil.virtual_memory().available`), so memory a worker holds might make the host evict its own
  cached results, while the host can evict nothing the worker holds. The
  signal is global and the lever is local.

- **Clone weight sharing needs plumbing nobody has written.** Two nodes using
  one checkpoint pay once in-process because clones point at the same tensors
  and are tracked by a shared id. Across processes the mapping is mechanically
  available, but ComfyUI's loaders read checkpoints from disk into
  process-local tensors and nothing tells a worker the host already has those
  weights mapped. The harder half is not the mapping: clones exist so each can
  be patched differently, and a shared mapping makes one clone's patch visible
  to the other unless something coordinates it.

The rest merely go wrong, which is a different and far more tractable
problem: two processes each keeping their own reserve, each ranking evictions
against a list missing half the models, each computing a pinning budget from
the same global free-RAM figure. Wrong, but wrong in ways you can measure and
correct. That is what the rest of this page is about.

<!--
Kept for reference: the same material as a per-feature list with collapsible
detail. Superseded by the summary above, which leads with the data structure
rather than the feature set. Restore if the breadth is wanted back.

ComfyUI memory management brings several optimizations. Open any of them for
what a process boundary does to it, because that is the whole subject of this
page. The three marked **cannot cross a boundary** are not merely degraded:
they lose their meaning entirely.

??? note "It streams weights on and off the card while the graph runs"

    A model too large for your VRAM still runs. ComfyUI keeps as much on the
    GPU as fits and pulls the rest across per step.

    **Across processes:** each side sizes that against its own view of free
    VRAM, and neither knows the other is about to do the same with the same
    bytes.

??? note "It holds room back for work whose size it cannot predict"

    Attention scratch, cast buffers and activations are not knowable in
    advance, so a reserve is kept free.

    **Across processes:** both sides keep their own reserve, so the same
    headroom is either booked twice or by neither.

??? note "It evicts the least useful model when it needs room"

    Everything resident sits in one list, ordered so the cheapest thing to
    lose goes first.

    **Across processes:** that ordering can only rank what it can see. A pack
    holding 6 GB is invisible, so ComfyUI discards one of its own models that
    was more useful.

??? danger "It remembers what every node produced, so a re-run skips the work &mdash; cannot cross a boundary"

    Results are cached and dropped again when RAM gets tight. RAM-pressure
    caching is the default (`--cache-ram`, `cli_args.py:140`).

    **Across processes:** a pack's outputs live in the pack's process and
    have to be copied across the boundary to be cached at all. Worse, "is RAM
    tight" is measured per process while the RAM is shared, so every process
    independently concludes it has room.

??? danger "It shares weights between clones of the same model &mdash; cannot cross a boundary"

    Two nodes using one checkpoint pay for it once. Clones are tracked by a
    shared id (`clone_base_uuid`).

    **Across processes:** a clone in another process cannot point at the same
    tensor. There is no one tensor. The checkpoint is paid for twice.

??? danger "It recovers when it runs out of memory &mdash; cannot cross a boundary"

    On an OOM it dumps every loaded model and tells you what happened
    (`execution.py:641`). Note that it does not retry: recovery here means
    clearing the decks, not doing the work in smaller pieces.

    **Across processes:** `unload_all_models()` frees ComfyUI's own models.
    The pack whose allocation caused the failure is not in that list and
    keeps everything it holds.

??? note "It pins host RAM so weights reach the GPU faster"

    Pinned memory transfers to the card far faster than pageable memory, and
    ComfyUI budgets how much it may pin against the machine's free RAM.

    **Across processes:** every process computes that budget from the same
    global free-RAM figure, so each one separately concludes it may take it.

??? note "comfy-aimdo pages weights a layer at a time"

    A virtual address reservation lets a model be "loaded" while only a slice
    is physically resident, which is how a 20 GB model runs on a 12 GB card
    without the streaming penalty.

    **Across processes:** its accounting is per process and its headroom is
    fixed when it starts, so it cannot be told that something else just took
    the card.

??? note "comfy-kitchen supplies the fused kernels and fp8 paths"

    The rest of the memory strategy assumes these exist: quantised weights
    are what make the budgets work out on consumer cards.

    **Across processes:** also per process, and a pack environment without it
    cannot even import ComfyUI, which is why comfy-env installs it into every
    worker whether the pack asked for it or not.
-->

## What ComfyUI's memory management does, ranked

Everything above is the shape of the problem. This is the inventory: every
global behaviour of ComfyUI's memory management, ordered by how much it
matters when a subprocess is invisible to it, with what comfy-env can reach
today and what it could reach with more coupling. It was compiled by three
independent reads of `comfy/model_management.py`, `model_patcher.py`,
`execution.py`, `server.py` and `comfy_execution/` (ComfyUI `bab6ee5f`,
2026-08-24), merged and rechecked line by line.

**Ordering rule.** How often it fires in a normal workflow, times how many
bytes it decides, times how bad it is when a subprocess is invisible to it.
Rank 1 fires on every load and moves gigabytes; rank 27 moves nothing.

**The three reachability columns:**

| Column | Meaning |
|---|---|
| **Today** | Everything comfy-env ships by default: it reads ComfyUI's values, publishes one number into `EXTRA_RESERVED_VRAM` and forwards the same reserve to the pager, calls `mm.free_memory`, translates a worker OOM into the host's real exception class, asks idle workers to shrink when the card is tight, and registers a stand-in for every worker model in `current_loaded_models` so ComfyUI's own eviction reaches it. |
| **How exposed that leaves us** | The stand-in above is a fake model in ComfyUI's own list. It is how the host reclaims from a worker at all, and it is also the single most fragile thing comfy-env does: ComfyUI reads whatever it likes off anything in that list, and both of comfy-env's loud breaks in twelve months were a new attribute read landing on it. This column says how exposed each row is. |
| **Upstream** | ComfyUI merges an external memory holder interface (a shrinker consulted by `free_memory` after its own models, plus a reserve provider registry) or full model registration. |

Cells are yes, partial or no, with at most one clause of reason. Exposure is stable, fragile or broke: stable means nothing reads the stand-in on that path, fragile means the host reads internals off it that upstream has already changed once, broke means a read on that path has taken comfy-env down before.

<!-- Styling for the ranked table lives in the page rather than in a stylesheet on
     purpose: browsers cache extra_css across mkdocs serve rebuilds, the page HTML
     they do not. -->
<style>
/* Reachability verdicts in the ranked memory table.
 *
 * Each verdict word is wrapped in <span class="v v-yes|v-partial|v-no">.
 * The cell background follows the BEST verdict in the cell (a cell that
 * says "no for the listener; yes for a holder" is reachable, so it reads
 * green), because the later rule wins; the words themselves keep their
 * own colour so a mixed cell still shows both. Muted on purpose.
 */
.md-typeset .reach-table td:has(.v-no)      { background: #f3e3e1; }
.md-typeset .reach-table td:has(.v-partial) { background: #f5edd6; }
.md-typeset .reach-table td:has(.v-yes)     { background: #e3efe1; }
.md-typeset .reach-table .v { font-weight: 600; }
.md-typeset .reach-table .v-no      { color: #8e3b33; }
.md-typeset .reach-table .v-partial { color: #7d5d12; }
.md-typeset .reach-table .v-yes     { color: #2f6b34; }

[data-md-color-scheme="slate"] .md-typeset .reach-table td:has(.v-no)      { background: rgba(190, 80, 70, 0.20); }
[data-md-color-scheme="slate"] .md-typeset .reach-table td:has(.v-partial) { background: rgba(200, 160, 50, 0.18); }
[data-md-color-scheme="slate"] .md-typeset .reach-table td:has(.v-yes)     { background: rgba(80, 170, 90, 0.18); }
[data-md-color-scheme="slate"] .md-typeset .reach-table .v-no      { color: #e9a39a; }
[data-md-color-scheme="slate"] .md-typeset .reach-table .v-partial { color: #e5c46b; }
[data-md-color-scheme="slate"] .md-typeset .reach-table .v-yes     { color: #9fd8a3; }

/* Seven columns do not fit Material's 61rem content column. On any page that
 * carries the ranked table, let the grid use the whole window. Scoped with
 * :has so every other page keeps the default width. */
.md-main:has(.reach-table) .md-grid,
.md-header:has(+ .md-container .reach-table) .md-grid {
  max-width: 100%;
}
.md-typeset .reach-table table {
  font-size: 0.62rem;
}

/* The number column: num-col already collapses its width, but Material's
 * 1.25em cell padding still makes it wide. Tighten the padding here. */
.md-typeset .reach-table th:first-child,
.md-typeset .reach-table td:first-child {
  padding-left: 0.4em;
  padding-right: 0.4em;
}

/* Same pages: pull the two sidebars in and trim the content gutters so the
 * table gets the width, not the whitespace around it. */
@media screen and (min-width: 76.25em) {
  .md-main:has(.reach-table) .md-sidebar {
    width: 10.5rem;
  }
  .md-main:has(.reach-table) .md-content__inner {
    margin-left: 0.8rem;
    margin-right: 0.8rem;
  }
  .md-main:has(.reach-table) .md-sidebar .md-nav--primary > .md-nav__title { padding-left: 0.4rem; }
  .md-main:has(.reach-table) .md-sidebar--primary .md-sidebar__scrollwrap,
  .md-main:has(.reach-table) .md-sidebar--secondary .md-sidebar__scrollwrap {
    margin: 0;
  }
}
</style>

<div class="reach-table num-col" markdown>

| # | What ComfyUI does | Today | How exposed that leaves us | Upstream |
|---|---|---|---|---|
| 1 | `free_memory` eviction ladder: when the card is short, the host walks its list of loaded models oldest first and asks each to leave until there is room. A model it never listed is never asked. | <span class="v v-yes">yes</span>: ComfyUI's own eviction loop reaches the stand-in we register for each worker model, and the worker unloads. This is the mechanism, and the whole of it: nothing else lets the host take memory from another process | <span class="v v-partial">fragile</span>: every pass reads `.device`, `.is_dead()`, `.model_offloaded_memory()`, `.model_memory()`, `.currently_used`, `.model.is_dynamic()` and `.model_unload()` on the fake; the loop body grew a dynamic branch (`:884-888`) when aimdo landed | <span class="v v-yes">yes</span> |
| 2 | `load_models_gpu` admission: before loading a model, the host adds up model size plus 10 percent plus the reserve and frees that much first. | <span class="v v-yes">yes</span>: on Linux both sides read the same device wide free figure, so the sum is right without anything being declared; on Windows comfy-env supplies what the host cannot see. The stand-in is not read here, the sum is over incoming models | <span class="v v-yes">stable</span>: nothing reads the fake here | <span class="v v-yes">yes</span> |
| 3 | The reserve, `EXTRA_RESERVED_VRAM` and `--reserve-vram`: how much of the card the host must always leave alone. Read on every load; the pager reads it only once, at startup. | <span class="v v-yes">yes</span>, and mostly not needed: on Linux the host already sees what packs hold, so comfy-env publishes only the operator's own `--reserve-vram`. On Windows, where the host sees nothing of a pack, it publishes what each pack holds now. Preventive on the legacy path, where the partial load budget shrinks with it; forwarded to the pager (row 8) for the paged one | <span class="v v-yes">stable</span>: not a list path | <span class="v v-yes">yes</span>, plus a runtime headroom setter for the paged half |
| 4 | `get_free_memory`: "how much room is left", which also sizes batches. Driver free plus torch's idle cache; on Linux it covers the whole card, on Windows only the calling process. | <span class="v v-yes">yes</span> to read, not modifiable; via the registered stand-in, the fake's size never enters this number. Eviction targets are `required minus free`, and free already includes what the worker holds | <span class="v v-yes">stable</span>: ledger sizes only order the eviction candidates; the one way to double count is row 23, a node handing the fake back to `load_models_gpu` | <span class="v v-partial">partial</span>: upstream must choose free-side or ledger-side, never both |
| 5 | `unload_all_models` and the Free button: an eviction ask for an absurd number (1e30) sent to every listed model between prompts. | <span class="v v-yes">yes</span>: the 1e30 ask reaches the stand-in and the worker releases. The button works | <span class="v v-yes">stable</span>: one method call with one argument; the sentinel value is a convention, not an API | <span class="v v-yes">yes</span> |
| 6 | Partial load budget (`lowvram_model_memory`): load only as much of a model as fits after the reserve and keep the rest in RAM. The pager ignores this and decides page by page. | <span class="v v-yes">yes</span> on legacy: the host computes a budget for the stand-in and calls `partially_load`, which the worker performs. <span class="v v-no">no</span> under aimdo, where the pager ignores the budget and decides at fault time | <span class="v v-partial">fragile</span>: seven reads on the fake (`model_patches_to`, `model_dtype`, `partially_load`, `model_loaded_memory`, `load_device`, `is_dynamic`, `loaded_ram_size`); the last two arrived with aimdo in 2026 | <span class="v v-yes">yes</span> |
| 7 | `LoadedModel` size questions: how the host asks each listed model how big it is and how much is on the card. The legacy count reads 0 for a paged model; only the pager's own count is right. | <span class="v v-partial">partial</span>: the stand-in answers one scalar, the max of aimdo and torch, never their sum. On Linux that scalar is already in the driver free figure, so nothing is declared from it; on Windows, where it is not, it is what gets declared | <span class="v v-partial">fragile</span>: `model_size`, `loaded_size`, `current_loaded_device`; `loaded_size` was reimplemented for the dynamic patcher (`mp.py:1809`) and the legacy one reads 0 for a paged model | <span class="v v-yes">yes</span> |
| 8 | aimdo headroom: each process's pager keeps a safety margin, and ComfyUI seeds it once at startup from `--reserve-vram` and never touches it again. The setter itself is live: changing it steers the next page fault. | <span class="v v-yes">yes</span> now: comfy-env forwards its published reserve into the pager's headroom at runtime, which is live at the next fault (measured: 6016 to 3456 MiB). ComfyUI itself still seeds it once at startup and never again | n/a | <span class="v v-partial">partial</span>: ComfyUI should forward its own reserve too, rather than leaving it to us |
| 9 | Per-layer fault and aimdo's C-side eviction: each layer is fetched onto the card when needed and the pager decides for itself what to drop, from device-wide pressure. Torch never sees these pages. | <span class="v v-yes">yes</span>, with no coordination and none possible from Python | n/a | <span class="v v-partial">partial</span>: needs a cross-process priority signal nobody has proposed |
| 10 | `model_unload` partial versus full: ask a model to shrink by the shortfall, else throw it out entirely. Returns True even if nothing was freed. | <span class="v v-yes">yes</span>: the stand-in implements `loaded_size`, `partially_unload` returning bytes actually moved, and `detach`. A short return escalates to detach, which is upstream's own contract | <span class="v v-partial">fragile</span>: `partially_unload` has a return contract (a short answer escalates to `detach`) and a second dynamic implementation via `vbar_free_memory`, both 2026 | <span class="v v-yes">yes</span> |
| 11 | OOM branch in `execution.py`: on out-of-memory the host logs a summary, clears every model and stops the run. No retry. | <span class="v v-partial">partial</span>: a worker OOM crosses as the real class so the branch fires; host models are freed, worker models are not; via the registered stand-in, same call as row 5 | <span class="v v-yes">stable</span>: same call as row 5 | <span class="v v-partial">partial</span>: only a holder adds its own line to the summary |
| 12 | `/free` with `free_memory`: the stronger button also throws away every remembered step result, including results a worker sent back. | <span class="v v-partial">partial</span>: the stand-in hears the unload half of the button, never the cache reset. Worker outputs cached in the host survive it | <span class="v v-yes">stable</span>: for the half it hears | <span class="v v-partial">partial</span>: needs a cache reset hook |
| 13 | `cleanup_models` prune: whenever a model object dies, the host erases every list entry whose `real_model()` is gone. | <span class="v v-yes">yes</span>, nothing to do; via the registered stand-in, the fake's wrapper carries a `real_model` weakref | <span class="v v-partial">fragile</span>: the prune assumes every entry went through `model_load`; comfy-env sets `real_model` and `model_finalizer` by hand (`pool.py:1318-1324`), matching internals upstream never promised | <span class="v v-yes">yes</span> |
| 14 | Prompt boundary signals: the prompt id in `comfy_execution.progress`, and the merged cache provider hooks for job start and end. | <span class="v v-yes">yes</span> for the id; the provider is merged and comfy-env registers none | n/a | <span class="v v-yes">yes</span>, already merged |
| 15 | `model_load` and the finalizer tripwire: loading a listed model also plants a weakref that erases its entry when the model dies. | <span class="v v-yes">yes</span>: the host calls `partially_load` on the stand-in and takes a weakref on `.model.model`, which comfy-env keeps alive for it | <span class="v v-partial">fragile</span>: `.model.model` must be a stable weakref-able object forever; `model_patches_to` and `model_dtype` are read on the way | <span class="v v-yes">yes</span> |
| 16 | Entry identity and the dead-entry sweep: the list holds a weak grip on each model; if the owner vanishes while weights remain, the host runs a full garbage sweep. | <span class="v v-partial">partial</span>: effect yes, visibility no; via the registered stand-in, comfy-env must hold the fake's patcher strongly or the sweep reports it dead on every load | <span class="v v-partial">fragile</span>: `__eq__` is `.model` identity, `is_dead` reads the weakref, and `_switch_parent` rebinds to `.parent` when a clone dies, all 2026 | <span class="v v-yes">yes</span> |
| 17 | Clone dedup and `is_clone` probing: before every load the host checks whether this model or a twin is already listed and throws out the twin. | <span class="v v-yes">yes</span>: `is_clone` answers False and `__eq__` never matches, so a worker model is never mistaken for a twin of a host one | <span class="v v-no">broke</span>: this is where the proxy broke twice; every load runs `is_clone`, `__eq__` and `model_patches_models()` against the fake first, so any new read lands here | <span class="v v-yes">yes</span> |
| 18 | `is_dynamic` gate and the per-node ledger walk: a yes or no tag deciding whether the host digs into a model's pinned-RAM internals after every step. | <span class="v v-yes">yes</span>: the worker resets its own; via the registered stand-in, the fake answers `is_dynamic()` False and nothing deeper is read | <span class="v v-no">broke</span>: answering True means faking six positional tuples in `dynamic_pins`, a layout that moved twice this year (2026-05, 2026-07) | <span class="v v-yes">yes</span> |
| 19 | Pin eviction ladder: when machine RAM gets tight, listed models let go of their locked RAM, models not used by this job first. Reads machine-wide available RAM. | <span class="v v-partial">partial</span>: worker pins are invisible, but the worker stops at the same 2 GiB floor; via the registered stand-in, only by faking that tuple layout | <span class="v v-no">broke</span>: the most churned surface in the file | <span class="v v-partial">partial</span>: needs a pin facet nobody has described |
| 20 | `--disable-smart-memory`: forget every model after each run; every eviction ask becomes 1e32. | <span class="v v-yes">yes</span> to read; the prompt-end unload is invisible; via the registered stand-in, every ask is 1e32 and the fake unloads every time, which is what the flag means | <span class="v v-yes">stable</span> | <span class="v v-yes">yes</span> |
| 21 | Node output cache and RAM-pressure release: the host keeps every step's results and drops the oldest and biggest when RAM runs low. Worker results are in that pile. | <span class="v v-yes">yes</span> for outputs | n/a | <span class="v v-partial">partial</span> |
| 22 | Allocator cache release (`soft_empty_cache`): hand the driver back the memory torch kept in its pocket, after an eviction, after a run, before a retry. | <span class="v v-yes">yes</span>: every floor `free_memory` call reaches it; via the registered stand-in, no entry reads | <span class="v v-yes">stable</span> | <span class="v v-yes">yes</span> |
| 23 | `loaded_models()` leak into node code: controlnet and a few extras nodes borrow the list and hand it straight back to `load_models_gpu`, so anything in it is treated as a real model. | <span class="v v-partial">partial</span>: safe only while `currently_used` stays False. True, and controlnet or three extras nodes hand the stand-in back into `load_models_gpu`; `multigpu.py` reads `load_device` and `clone_base_uuid` and calls `clone()` on it | <span class="v v-no">broke</span>: four callers outside `model_management.py` read the list unfiltered, and node code can read anything | <span class="v v-yes">yes</span> |
| 24 | Interrupt flag: the stop button, checked before every node and every cast. It returns memory mid-step by unwinding. | <span class="v v-partial">partial</span>: forwarded at progress callbacks only | n/a | <span class="v v-no">no</span>: it is a call into the worker, not a holder interface |
| 25 | `unload_model_and_clones`: throw out one model and its copies but keep everything else, using the same 1e30 as the button. | <span class="v v-yes">yes</span>: the stand-in's `clone_base_uuid` is a private sentinel, so it stays in the keep list. None would MATCH a target whose own uuid is None and free it on someone else's eviction | <span class="v v-partial">fragile</span>: `clone_base_uuid` is an internal identity two callers compare directly | <span class="v v-yes">yes</span> |
| 26 | `GET /system_stats`: the numbers the UI gauge shows. Worker allocations show as used, never as reclaimable. | <span class="v v-partial">partial</span> | n/a | <span class="v v-partial">partial</span> |
| 27 | `MAX_PINNED_MEMORY` and hostbuf ceilings: every process assumes it may lock most of the machine's RAM, so N processes promise N times the RAM. | <span class="v v-partial">partial</span>: mirrored, but not what binds | n/a | <span class="v v-partial">partial</span>: needs a coordinator |

</div>

Three things fall out of the table:

- **The stand-in is the mechanism, not an option.** Rows 1, 5, 10, 11 and 15
  are reachable only because a fake model for each worker model sits in
  ComfyUI's list. Without it the host can decline to take memory it does not
  have, which is useful, but it cannot take memory back, which is the half
  that matters when the card is already full.
- **That is also the whole of the risk.** Rows 17, 18, 19 and 23 are marked
  broke: the host runs those reads against the stand-in on every load, and
  that is where both historical breaks landed. Everything else comfy-env
  does is reading values and publishing one number.
- **Only upstream fixes** rows 4, 8, 9, 12, 19, 26 and 27. Row 14 is the
  counterexample: the cache provider is already merged and comfy-env does
  not register one.

??? note "Twenty-three more behaviours already work inside a worker and need no bridge"

    These are per process by nature. A worker runs the same manager on the
    same tree, so it already gets them right for its own models. Listed so
    the reader can see what a bridge does *not* have to carry.

    | # | Behaviour | ELI5 | Why it already works in a worker |
    |---|---|---|---|
    | 1 | `ModelPatcher.clone` | A second remote control for the same TV, not a second TV | A MODEL never crosses the boundary, so the host never clones a worker model |
    | 2 | LoRA patching, baked or applied per forward | Stickers on the weights | Entirely inside the process that owns the module |
    | 3 | Legacy load, partial load, partial unload, unpatch | Put as much on the card as fits, move layers back when told | The worker runs the real code on its own models |
    | 4 | `ModelPatcherDynamic.load` and per-layer paging | Reserve the seats, walk people in only when called | Each process owns its vbars and its aimdo context |
    | 5 | aimdo's own accounting (`get_total_vram_usage`, `loaded_size`) | The pager's own scoreboard, the only one right for a paged model | The worker reads its own context and publishes one scalar |
    | 6 | Pinned RAM registration | Lock the weights' RAM pages so the card can pull them fast | The worker pins into its own ledger and its own hostbufs |
    | 7 | `ensure_pin_budget` against machine-wide available RAM | Before locking more RAM, check the whole machine still has 2 GB spare | One machine-wide number, so every process stops at the same floor with no coordination |
    | 8 | `set_ram_cache_release_state` | Before locking RAM, ask the results cache to make space first | Per process; a worker can set its own headroom without touching the host |
    | 9 | Model files through the page cache (`load_safetensors`, `safe_open`) | Point at the file instead of copying it; two programs pointing at one file share one copy | The kernel shares pristine pages across processes under both mappings |
    | 10 | `read_tensor_file_slice_into` | Read the layer straight from the file into the fast lane | Per process |
    | 11 | Cast buffers and their per-node reset | Scratch space on the card for the layer being copied, wiped after every step | The worker runs its own reset at its node boundary |
    | 12 | `PromptModelTracker` marks | A sticky note "in use by this job" so the RAM bouncer picks someone else first | The worker reads the prompt id at its call boundary and marks its own models |
    | 13 | Dtype decisions (`should_use_fp16`, `unet_dtype`) | Whether a model is stored at full, half or quarter size | All dtype flags are mirrored as resolved values |
    | 14 | `vram_state` (`--lowvram`, `--highvram`, ...) | The big dial for how aggressively models stay on the card | Crosses per call on the budget request; the three vram flags are deliberately not mirrored |
    | 15 | Placement (`unet_offload_device`, `vae_device`, ...) | Where each kind of model is born and parked | Per process, and Linux free memory already sees worker VRAM |
    | 16 | `get_total_memory` | How big the card is | Each process reads the same card |
    | 17 | aimdo enable and pressure flags | Whether paging is on and how it senses pressure | Exported to workers as env vars; the worker follows the host's resolved state |
    | 18 | Stream, pinning, mmap and allocator flags | How many lanes copy weights, whether RAM is locked, which allocator is used | Mirrored as resolved values; `cudaMallocAsync` is inherited |
    | 19 | Cache type flags (`--cache-ram`, `--cache-none`, ...) | How much the host remembers between runs | Executor side; workers run no queue |
    | 20 | OOM recognition and retry smaller (VAE tiling, attention halving) | Recognise "out of memory" and retry at a smaller size | Runs on the worker's own allocator, which is the right process |
    | 21 | Interrupt checks inside node loops | Long loops peek at the Cancel flag | The code runs unchanged on the worker's own flag; the host never sets it (see row 24 above) |
    | 22 | `set_cudnn_benchmark` after node import | Undo plugins that turned on a memory-hungry speed setting | The worker sets its own policy at import |
    | 23 | `hook_breaker` save and restore | ComfyUI repeatedly undoes a specific kind of plugin tampering | Not a worker behaviour; listed because a planted list entry is data rather than a patched function, so it is immune, while a wrap is not |

## Question: are we able to exactly replicate ComfyUI memory management across comfy-env isolated processes?

No. Not on Windows, and not with the interfaces available to us.

Why can't we do it? Four reasons, none of which comfy-env can engineer
around from outside:

1. **The bookkeeping does not cross, even though the bytes can,** as above.
   Eviction and OOM recovery reach a worker only through an object we put in
   ComfyUI's list, and the output cache and clone sharing would each need
   machinery nobody has built.

2. **The numbers each side reads do not mean the same thing.** On Linux the
   free-VRAM figure covers the whole device, so each process already sees
   the other's models. On Windows it covers only the calling process, so
   neither does. Any accounting has to branch on that, and a wrong branch
   is a silent double count.

3. **Every hook ComfyUI has is an in-process hook.** There is no event, no
   callback and no registry for "something outside just took VRAM", so the
   only way in is from inside the list, which is exactly the coupling that
   broke comfy-env twice. An upstream holder interface would replace it;
   until then this is the seam and it has to be watched.

4. **Upstream removes custom-node patches on purpose.** ComfyUI ships a
   file whose job is to restore functions that custom nodes replaced, and
   it runs on a timer. Anything built by patching is built on sand.

And what stays broken no matter what we build:

- We cannot see memory a pack allocates outside PyTorch.
- We cannot detect the failure this whole system exists to prevent.
- A third process can invalidate our measurement between taking it and
  acting on it.

Those are in [What this does not fix](#what-this-does-not-fix). Read that
section before treating any of this as finished.

---

## Part 1 — How ComfyUI decides

`current_loaded_models` is the list from the top of this page. When something
needs room, ComfyUI calls `free_memory(memory_required, device)`. Stripped to
its bones (`model_management.py:863-894`):

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

comfy-env lets ComfyUI evict a pack's model through a stand-in in its own
list, asks ComfyUI to free its own models when a pack needs room, asks idle
packs to shrink when the card is tight, and tells ComfyUI about the memory
it cannot see for itself.

That last part is smaller than it sounds, and deliberately so. On Linux the
host's free-memory reading covers the whole card, so it already sees every
byte a pack holds, models and CUDA context alike, and comfy-env declares
**nothing**: the operator's own `--reserve-vram` is published unchanged. On
Windows the same reading is the calling process's private budget and shows
nothing of a pack, so comfy-env declares what each pack holds right now.

**A reserve is a measurement here, never a prediction.** There used to be a
forecast: a pack that had once held 6 GB had 6 GB held for it against the
next time. It was one observation extrapolated, wrong when a pack spiked
once on a big input and wrong again when a pack was about to need far more
than it ever had. It is gone. The host takes memory back when it needs it,
so it does not have to be stopped from taking it in advance.

It patches nothing. It reads values ComfyUI already exposes, writes two
numbers that exist for this purpose (`EXTRA_RESERVED_VRAM` and the pager's
own headroom, both of which `--reserve-vram` sets at launch), calls public
functions, and registers one object per worker model.

## What an operator can switch

Nothing, by default, and that is deliberate: the floor is either right or it
is a bug, not a preference. Two switches exist.

`COMFY_ENV_MEMORY_OBSERVER=on` adds a read-only listener to ComfyUI's loaded
model list, which reports holding nothing and exists only to hear the calls
that never leave the host process. It is off because it is a second object
of ours in that list, and objects in that list are what broke comfy-env
twice.

`COMFY_ENV_WORKER_AIMDO=0` stops a worker from paging even when the host
does. A worker follows its host by default, because the two share a card and
must decide residency the same way.

There used to be a third, `COMFY_ENV_MEMORY_MANAGEMENT`, an ordered level
from `off` to `shared`. It was deleted in favour of saying so here: nothing
read it. What it claimed to gate is decided instead where it can actually be
known, by each worker, from the manager and the library versions it resolved
to at startup.

## Why a worker on the legacy ledger is fine

A worker follows its host, so this is the case where the host itself is not
paging. It is not simply "paging minus the paging":

* **Zero pinned system RAM.** Paging costs roughly twice the model size in
  host RAM for pinned buffers. On a RAM-poor machine that is the dominant
  cost, and the ledger avoids it entirely.
* **Big models still run.** ComfyUI's own low-VRAM streaming loads what fits
  and pulls the rest per step. Paging buys residency and speed, not
  feasibility.
* **The widest compatibility**, by about eighteen months.
* **The reserve is preventive there**, which it is not on the paged path:
  ComfyUI's partial-load budget shrinks with the reserve, while the pager
  decides residency at fault time and has to be told separately. On Linux
  there is usually no added reserve to be preventive with, since the host
  can see the packs, and reclaim does the work instead.

## The optional observer

```
COMFY_ENV_MEMORY_OBSERVER=on         # default: off
```

This is narrower than it used to be. The Free button and the
out-of-memory handler already reach packs through the stand-ins, so the
observer is not what makes those work.

What it adds is one case: hearing that the host is under pressure when there
is no worker model registered to hear it, for instance a pack holding memory
outside a model comfy-env tracks. It reports holding nothing, which is true,
so ComfyUI asks it, gets zero, and moves on.

It is off by default because it is a *second* object of ours in that list
for a case the first one usually covers, and objects in that list are what
broke comfy-env twice. Turn it on if you have packs whose memory the host
cannot see; leave it off otherwise.

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
| `admission tight env=... need=... true_free=...` | A pack asked for more than was free; the host was asked to evict |
| `idle release: <pack> gave back N GB` | A quiet pack returned its VRAM |
| `PIN REGRESSION env=... active_evicted=...` | Pins were taken from a model that was still in use. Should never appear; report it |
| `admission ask: <pack> gave back N GB of M GB asked` | The host had evicted everything of its own and was still short, so an idle pack was asked to shrink |
| `pager headroom N GB` | The reserve was forwarded to comfy-aimdo, which is the only way it reaches the paged path |
| `NOTE: <pack> maps two majors of libX` | Two versions of one CUDA library in one worker. Costs private RAM and is not something comfy-env can prevent; see below |
| `<pack> contract: <symbol> missing: <why>` | A symbol comfy-env relies on is absent in that worker, with what it costs |
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

**Nothing is held for a pack before it needs it.** comfy-env declares only
what the host cannot see, so on Linux a pack that is about to load gets no
space held in advance. The host may take that space first, and the pack then
takes it back by evicting host models, which costs a reload rather than an
error. On a card that is full either way, a workflow alternating between
host nodes and pack nodes can pay that reload repeatedly. The fix is not a
forecast, which was tried and removed; it is a pack declaring its own
envelope in its manifest. None does yet.

**The stand-in is a compromise, and it is the thing most likely to break.**
comfy-env answers eighteen attributes of ComfyUI's internals from an object
that is not a model, because there is no other way for the host to ask a
subprocess for memory back. Both user-visible breakages in a year came
through it. It survives upstream changes by being watched, not by being
safe: a test greps ComfyUI for the real access sites and fails when the set
moves. The fix is not on our side. It is a small hook upstream, and the case
for it is at the top of this page.

**Multi-GPU accounting is wrong, not merely absent.** The budget callback,
the stand-ins' load device, the device-total probe and the pager headroom
are all the primary device or process-global, so a pack on the second card
charges its memory against the first. On one GPU this is invisible. On two
it is a bug, and it is the largest single piece of unfinished work here.

**Host RAM is not addressed.** Worker models offload into the worker's own
RAM, outside ComfyUI's `ram_required` / pinned-memory accounting. This is
less dangerous than the VRAM equivalent — the pin budget measures against a
system-wide `psutil` figure that already sees worker RAM, so it backs off on
its own — but ComfyUI can never ask a worker to release host memory, only
decline to pin more itself.

**Cross-worker eviction still has a lock-ordering hazard.** When a pack's
load comes up short, the host asks idle siblings to shrink, over IPC, from
inside the budget callback. Snapshotting the patcher dict and staying clear
of the pool lock are necessary but not sufficient; a real single-flight or
ordered-lock discipline is still owed, and until it exists two packs loading
at once can both be told yes for the same bytes.

**Host RAM pressure does not reach worker pins.** ComfyUI's pin eviction
walks its own list; a worker's pinned RAM is not in it, and nothing asks the
worker either. The machine-wide free-RAM figure means both processes back
off on their own, so this fails in the safe direction, but the host cannot
ask for pinned RAM back.

**Cancel does not reach a node that is already running.** The interrupt flag
is per process and nothing sets it in the worker, so a pack that does not
report progress cannot be stopped mid-node. It finishes, then the queue
stops.

---

## The ask, if you are reading this from upstream

Two methods and a registry, modelled on `set_ram_cache_release_state` and
the cache provider registry, both of which already live in the tree:

```python
class MemoryHolder:
    def reserved_memory(self, device) -> int: ...   # keep this much free
    def release_memory(self, device) -> None: ...   # give it back now
```

`load_models_gpu` sums the reserves once per load and adds them to what it
already computes. `free_memory` asks registered holders after its own
models, where it already asks the pinned-memory helpers. Nothing changes
when nobody registers, core learns nothing about subprocesses, and
comfy-env deletes the stand-in and the eighteen attributes with it.

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
