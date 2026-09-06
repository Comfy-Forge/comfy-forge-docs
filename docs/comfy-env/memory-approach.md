# comfy-env's memory management

ComfyUI manages RAM and VRAM to optimize for speed and stability on all kinds
of hardware. Every bit of that strategy assumes everything runs in one
process. comfy-env's isolated nodepacks run in separate ones, on the same
card, and neither side can see the other's allocations directly.

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

## comfy-env's approach

While pursuing our stated aim, two rules were meant to be followed:

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

## Comprehensive list of everything ComfyUI's memory management does, ranked by importance

**Ordering rule.** How often it fires in a normal workflow, times how many
bytes it decides, times how bad it is when a subprocess is invisible to it.
Rank 1 fires on every load and moves gigabytes; rank 27 moves nothing.

**What "legacy" means here.** It does not mean aimdo is absent. `main.py`
imports `comfy_aimdo.control` unconditionally, so the pager is always
installed; what is conditional is `init()`, gated on `enables_dynamic_vram()`,
which is on by default and turned off by `--highvram`, `--novram`,
`--gpu-only`, `--cpu` or `--disable-dynamic-vram`. So the legacy rows describe
a pager that is present and idle, and every row saying comfy-env forwards
something to the pager is a no op in that configuration, not a fallback.

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
| 1 | `free_memory` eviction ladder: when the card is short, the host ranks its loaded models and asks them to leave until there is room. The rank is a four key sort (`mm.py:875-882`): most already offloaded first, then lowest `sys.getrefcount`, then smallest, and only as a final tiebreak the list index, which is newest first because `load_models_gpu` inserts at 0. A model it never listed is never asked. | <span class="v v-yes">yes</span>: ComfyUI's own eviction loop reaches the stand-in we register for each worker model, and the worker unloads. It is the only path by which *upstream's own code* takes memory from another process. comfy-env has two more of its own that never touch the list: an admission time ask to idle workers (`pool.py:927`) and a node boundary release (`pool.py:1609`) | <span class="v v-partial">fragile</span>: every pass runs `__eq__`, `.device`, `.is_dead()`, `.model_offloaded_memory()`, `.model_memory()`, `sys.getrefcount(.model)`, `.model_unload()`, `.model.loaded_size()` and `.model.model.__class__.__name__` (the last inside an eagerly built log string, so it runs regardless of log level). `.currently_used` is *written* here, never read; its only reader is `loaded_models()`. The loop body grew a dynamic branch (`:888-892`) when aimdo landed | <span class="v v-yes">yes</span> |
| 2 | `load_models_gpu` admission: before loading a model, the host adds up model size plus 10 percent plus the reserve and frees that much first. | <span class="v v-yes">yes</span>: on Linux both sides read the same device wide free figure, so the sum is right without anything being declared; on Windows comfy-env supplies what the host cannot see. The sum itself is over incoming models and reads nothing off the stand-in | <span class="v v-partial">fragile</span>: the *arithmetic* does not read the fake, but the function does. `current_loaded_models.index()` runs `__eq__` against every entry, and `is_clone` is called with the fake as its argument. Row 17's reads all land inside this function | <span class="v v-yes">yes</span> |
| 3 | The reserve, `EXTRA_RESERVED_VRAM` and `--reserve-vram`: how much of the card the host must always leave alone. Read on every load. The pager never reads it at all: what ComfyUI seeds once at startup is the pager's own headroom (row 8), from `--reserve-vram`. | <span class="v v-yes">yes</span>, and mostly not needed: on Linux the host already sees what packs hold, so comfy-env publishes only the operator's own `--reserve-vram`. On Windows, where the host sees nothing of a pack, it publishes what each pack holds now. Preventive on the legacy path, where the partial load budget shrinks with it; forwarded to the pager (row 8) for the paged one | <span class="v v-yes">stable</span>: not a list path | <span class="v v-yes">yes</span>, plus a runtime headroom setter for the paged half |
| 4 | `get_free_memory`: "how much room is left", which also sizes batches. Driver free plus torch's idle cache; on Linux it covers the whole card, on Windows [only the calling process](windows-blind-spot.md). | <span class="v v-yes">yes</span> to read, not modifiable; via the registered stand-in, the fake's size never enters this number. Eviction targets are `required minus free`. On Linux free already includes what the worker holds, so the target is right. On Windows it does not, and the target is systematically too small, which is the whole reason row 3 declares anything | <span class="v v-yes">stable</span>: ledger sizes only order the eviction candidates; the one way to double count is row 23, a node handing the fake back to `load_models_gpu` | <span class="v v-partial">partial</span>: upstream must choose free-side or ledger-side, never both |
| 5 | `unload_all_models` and the Free button: an eviction ask for an absurd number (1e30) sent to every listed model between prompts. | <span class="v v-yes">yes</span>: the button reaches the stand-in and the worker releases. It does not arrive as 1e30, though: `model_unload` compares the ask against `loaded_size()`, 1e30 loses, and the stand-in is called with `detach(True)` (`mm.py:809-815`). Only a bare list entry implementing `model_unload` itself ever sees the sentinel | <span class="v v-yes">stable</span>: one method call, no argument that matters | <span class="v v-yes">yes</span> |
| 6 | Partial load budget (`lowvram_model_memory`): load only as much of a model as fits after the reserve and keep the rest in RAM. The pager ignores this and decides page by page. | <span class="v v-yes">yes</span> on legacy: the host computes a budget for the stand-in and calls `partially_load`, which the worker performs. <span class="v v-no">no</span> under aimdo, where the pager ignores the budget and decides at fault time | <span class="v v-partial">fragile</span>: six reads on the fake (`model_patches_to`, `model_dtype`, `partially_load`, `loaded_size`, `load_device`, `is_dynamic`); `is_dynamic` arrived with aimdo in 2026-01. `loaded_ram_size` (2026-05) is the read we are one False away from: `mm.py:1010` gates it behind `is_dynamic()`, and it is not on the stand-in's surface, so answering True would raise rather than lie | <span class="v v-yes">yes</span> |
| 7 | `LoadedModel` size questions: how the host asks each listed model how big it is and how much is on the card. The legacy count reads 0 for a paged model; only the pager's own count is right. | <span class="v v-partial">partial</span>: the stand-in answers one scalar, the max of aimdo and torch, never their sum. Max was chosen from a sample where the two overlapped (4.02 against 4.03 GiB), but they are separate pools in general, and a worker paging 6 GB through aimdo while torch holds 2 GB of activations reports 6. On Windows that under report is the reserve. On Linux that scalar is already in the driver free figure, so nothing is declared from it; on Windows, where it is not, it is what gets declared | <span class="v v-partial">fragile</span>: `model_size`, `loaded_size`, `current_loaded_device`; `loaded_size` was reimplemented for the dynamic patcher (`mp.py:1809`) and the legacy one reads 0 for a paged model | <span class="v v-yes">yes</span> |
| 8 | aimdo headroom: each process's pager keeps a safety margin, and ComfyUI seeds it once at startup from `--reserve-vram` and never touches it again. The setter itself is live: changing it steers the next page fault. | <span class="v v-yes">yes</span> now, on the two aimdo cells: comfy-env forwards `seed + (published - base)` into the pager's headroom at runtime, live at the next fault (measured: 6016 to 3456 MiB). It steers the process the setter runs in, which is the host, and no worker's pager. It is also only half the pager's arithmetic: `simple_vram_headroom` appears in the per process cap term, while the cross process term uses the compile time 256 MiB constant. ComfyUI itself still seeds it once at startup and never again | n/a | <span class="v v-partial">partial</span>: ComfyUI should forward its own reserve too, rather than leaving it to us |
| 9 | Per-layer fault and aimdo's C-side eviction: each layer is fetched onto the card when needed and the pager decides for itself what to drop, from device-wide pressure. Torch never sees these pages. | <span class="v v-yes">yes</span>, with no coordination and none possible from Python. The pager's own pressure reading is device wide in all four cells, including Windows, where it polls NVML rather than the process local figure ComfyUI uses | n/a | <span class="v v-partial">partial</span>: needs a cross-process priority signal nobody has proposed |
| 10 | `model_unload` partial versus full: ask a model to shrink by the shortfall, else throw it out entirely. Returns True even if nothing was freed. | <span class="v v-yes">yes</span>: the stand-in implements `loaded_size`, `partially_unload` returning bytes actually moved, and `detach`. A short return escalates to detach, which is upstream's own contract | <span class="v v-partial">fragile</span>: `partially_unload` has a return contract (a short answer escalates to `detach`) and a second dynamic implementation via `vbar_free_memory`, both 2026 | <span class="v v-yes">yes</span> |
| 11 | OOM branch in `execution.py`: on out-of-memory the host logs a summary, clears every model and stops the run. No retry. | <span class="v v-yes">yes</span>: a worker OOM crosses as the real class (the worker stamps it from ComfyUI's own `is_oom`, the host rebuilds `torch.cuda.OutOfMemoryError`) so the branch fires, and it calls `unload_all_models`, so worker models are freed by the same path as row 5 | <span class="v v-yes">stable</span>: same call as row 5 | <span class="v v-partial">partial</span>: only a holder adds its own line to the summary |
| 12 | `/free` with `free_memory`: the stronger button also throws away every remembered step result, including results a worker sent back. | <span class="v v-partial">partial</span>: the stand-in hears the unload half of the button. The cache reset it never hears about is not a leak, though: `e.reset()` rebuilds the whole `CacheSet`, so host side copies of worker results are dropped with everything else. What survives is what the worker still holds in its own process, which nobody asked it to drop | <span class="v v-yes">stable</span>: for the half it hears | <span class="v v-partial">partial</span>: needs a cache reset hook |
| 13 | `cleanup_models` prune: whenever a model object dies, the host erases every list entry whose `real_model()` is gone. | <span class="v v-yes">yes</span>, nothing to do; via the registered stand-in, the fake's wrapper carries a `real_model` weakref | <span class="v v-partial">fragile</span>: the prune assumes every entry went through `model_load`; comfy-env sets `real_model` and `model_finalizer` by hand (`pool.py:1478-1481`), matching internals upstream never promised | <span class="v v-yes">yes</span> |
| 14 | Prompt boundary signals: the prompt id in `comfy_execution.progress`, and the merged cache provider hooks for job start and end. | <span class="v v-yes">yes</span> for the id; the provider is merged and comfy-env registers none | n/a | <span class="v v-yes">yes</span>, already merged |
| 15 | `model_load` and the finalizer tripwire: loading a listed model also plants a weakref that erases its entry when the model dies. | <span class="v v-yes">yes</span>, and not because the host did it: comfy-env inserts the entry directly rather than through `load_models_gpu`, so it plants `real_model` and `model_finalizer` by hand (`pool.py:1478-1481`) to do upstream's job for it. The host only reaches `model_load` on a stand-in through row 23's leak | <span class="v v-partial">fragile</span>: `.model.model` must be a stable weakref-able object forever; `model_patches_to` and `model_dtype` are read on the way | <span class="v v-yes">yes</span> |
| 16 | Entry identity and the dead-entry sweep: the list holds a weak grip on each model; if the owner vanishes while weights remain, the host runs a full garbage sweep. | <span class="v v-partial">partial</span>: effect yes, visibility no; comfy-env must hold the fake's patcher strongly, and the consequence of not doing so is quieter than a sweep. `real_model` is a weakref to the same object, so both die together, `is_dead()` stays False, and `cleanup_models` simply prunes the entry with no signal at all | <span class="v v-partial">fragile</span>: `__eq__` is `.model` identity (2023-08), `is_dead` reads the weakref and `_switch_parent` rebinds to `.parent` when a clone dies (both 2024-12). `_switch_parent` cannot fire on a stand-in at all: it is armed only when `model.parent` is not None, and the fake sets it None | <span class="v v-yes">yes</span> |
| 17 | Clone dedup and `is_clone` probing: before every load the host checks whether this model or a twin is already listed and throws out the twin. | <span class="v v-yes">yes</span>: `is_clone` answers False and `__eq__` never matches, so a worker model is never mistaken for a twin of a host one | <span class="v v-no">broke</span>: this is where the proxy broke twice, though only one of the three runs on the fake. `__eq__` does, via `index()`, on every load. `is_clone` runs on the *incoming* model with the fake as its argument, so what reads the fake is upstream's `hasattr(other, 'model')`. `model_patches_models()` never touches a list entry. Any new read in this function still lands here | <span class="v v-yes">yes</span> |
| 18 | `is_dynamic` gate and the per-node ledger walk: a yes or no tag deciding whether the host digs into a model's pinned-RAM internals after every step. | <span class="v v-yes">yes</span>: the worker resets its own; via the registered stand-in, the fake answers `is_dynamic()` False and nothing deeper is read | <span class="v v-no">broke</span>: answering True means faking `dynamic_pins`, a dict of four six element positional tuples plus four scalar flags, of which `reset_cast_buffers` rebuilds two. The layout moved four times this year (2026-05-21, 05-25, 05-31, 07-29) | <span class="v v-yes">yes</span> |
| 19 | Pin eviction ladder: when machine RAM gets tight, listed models let go of their locked RAM, models not used by this job first. Reads machine-wide available RAM. | <span class="v v-no">no</span>: worker pins are invisible, and the two sides do not even stop at the same floor. The default branch is `max(RAM_CACHE_HEADROOM / 2, 2 GiB)`, and `RAM_CACHE_HEADROOM` is set by the executor for the duration of each prompt (`execution.py:748`, `min(10, max(2, total_ram * 0.10))` GB). A worker runs no executor, so its headroom stays 0 and its floor is exactly 2 GiB while the host's is higher on any machine above roughly 40 GB. Under `--fast-disk` the test is not a floor at all but the per process `MAX_PINNED_MEMORY` ceiling, and under `--high-ram` there is no test | <span class="v v-yes">stable</span>: `models_for_pin_eviction` skips anything answering `is_dynamic()` False before it reads `dynamic_pins`, so the churned tuple layout is row 18's exposure, not this one | <span class="v v-partial">partial</span>: needs a pin facet nobody has described |
| 20 | `--disable-smart-memory`: forget every model after each run; every eviction ask becomes 1e32. | <span class="v v-yes">yes</span>: the flag is read, and the prompt-end unload is not invisible either. `execution.py` calls `unload_all_models` when it is set, which walks the list and reaches the stand-in exactly as in row 5, so the worker forgets its model after every run, which is what the flag means | <span class="v v-yes">stable</span> | <span class="v v-yes">yes</span> |
| 21 | Node output cache and RAM-pressure release: the host keeps every step's results and drops the oldest and biggest when RAM runs low. Worker results are in that pile. | <span class="v v-yes">yes</span> for outputs, with upstream's own bug attached: the release pops the *largest* tuple, so on a score tie it evicts the most recently touched entry rather than the oldest, contradicting the comment above it | n/a | <span class="v v-partial">partial</span> |
| 22 | Allocator cache release (`soft_empty_cache`): hand the driver back the memory torch kept in its pocket, after an eviction, after a run, before a retry. | <span class="v v-partial">partial</span>: not every call reaches it. `free_memory` runs `soft_empty_cache` only if something was actually unloaded, or if torch's idle cache exceeds a quarter of free and `vram_state` is not `HIGH_VRAM`. A pass that evicts nothing under `--highvram` never gets there. No entry reads | <span class="v v-yes">stable</span> | <span class="v v-yes">yes</span> |
| 23 | `loaded_models()` leak into node code: controlnet and a few extras nodes borrow the list and hand it straight back to `load_models_gpu`, so anything in it is treated as a real model. | <span class="v v-partial">partial</span>: it happens, and the arithmetic survives it for a reason that is not ours. Controlnet and three extras nodes do hand the stand-in back into `load_models_gpu`, but `model_memory_required` asks for the offloaded remainder of a model already on the target device, and a resident worker model has none, so it adds zero. What it does cost is a re-fault: `load_models_gpu` calls `model_load` on the stand-in, so a host node can make a worker reload what it had let go. `multigpu.py` reads `load_device` and `clone_base_uuid` and calls `clone()`, which the stand-in raises on | <span class="v v-no">broke</span>: seven call sites in five files outside `model_management.py` read the list, and node code can read anything. Only `multigpu.py` reads it unfiltered | <span class="v v-yes">yes</span> |
| 24 | Interrupt flag: the stop button, checked before every node and every cast. It returns memory mid-step by unwinding. | <span class="v v-partial">partial</span>: forwarded at progress callbacks only, and the forward consumes the flag. `throw_exception_if_processing_interrupted` clears it before raising, so it is a one shot handoff to whichever side checks first, not a mirror | n/a | <span class="v v-no">no</span>: it is a call into the worker, not a holder interface |
| 25 | `unload_model_and_clones`: throw out one model and its copies but keep everything else, using the same 1e30 as the button. | <span class="v v-partial">partial</span>: the stand-in's `clone_base_uuid` is `None` (`isolation/model_patcher.py:134`), and `None == None`, so it would match a target whose own uuid is `None` and be freed on someone else's eviction. It is safe today only because upstream assigns `uuid.uuid4()` in `ModelPatcher.__init__` (`model_patcher.py:385`), so no real target ever carries `None`. The observer already uses a private sentinel for exactly this reason; the stand-in should too | <span class="v v-partial">fragile</span>: `clone_base_uuid` is an internal identity two callers compare directly | <span class="v v-yes">yes</span> |
| 26 | `GET /system_stats`: the numbers the UI gauge shows. Worker allocations show as used, never as reclaimable. | <span class="v v-partial">partial</span> on Linux. On Windows it is worse than the header says: `vram_free` comes from `get_free_memory`, so worker allocations are not shown as used either, they are absent, and the gauge reads the card as freer than it is | n/a | <span class="v v-partial">partial</span> |
| 27 | `MAX_PINNED_MEMORY` and hostbuf ceilings: every process assumes it may lock most of the machine's RAM, so N processes promise N times the RAM. | <span class="v v-partial">partial</span>: mirrored, and not what binds by default, where the pin test measures machine wide available RAM instead. Under `--fast-disk` it is exactly what binds, and `fast_disk` and `high_ram` are both mirrored to workers, so host and worker take that branch together and each promises its own share of the same RAM | n/a | <span class="v v-partial">partial</span>: needs a coordinator |

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

* [Why the system is imperfect](why-imperfect.md) is the case against the
  stand-in, one answer at a time.
* [Why Windows needs its own branch](windows-blind-spot.md) is the
  measurement behind every platform branch in the memory code.
* [ADR-0038](adr/0038-the-memory-floor.md) is the decision record: what was
  decided, what was measured, and what it supersedes.
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
