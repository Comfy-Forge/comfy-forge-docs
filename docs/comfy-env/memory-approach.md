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
internals, none of which upstream ever promised to keep stable. Every defect
ever found in it came through that surface, though not in the shape this
paragraph used to claim: they were wrong numbers found by audit, not attribute
reads found by users. The eviction loop grew a whole new branch when
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

**It is not easily maintainable.** There is a contract, and it does not cover
this. `contract.py` checks sixteen symbols comfy-env reads OFF ComfyUI, at
startup, and refuses to start on a fatal gap. Every entry runs in that one
direction; not one describes what ComfyUI reads off us, which is the direction
that breaks. What covers this direction is a single test that greps ComfyUI's
source for the places it reads a list entry and fails when that set moves.
That catches a change once we have the new ComfyUI in front of us, never
before. It also spent its entire life never executing, unmarked in a lane with
no ComfyUI and deselected from the lane that had one, until it was wired up on
2026-09-06.

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
Rank 1 fires on every load and moves gigabytes; rank 39 moves nothing.

**What this list covers, and what it does not.** It used to stop at 27 and
call itself comprehensive while covering only `model_management.py`, which is
to say only the manager. Two things were missing and are now in: the memory
decisions made INSIDE models and nodes rather than by the manager (the batch
sizers at 5, the activation estimate at 17, hook and unpatch backups at 28 and
29), and three whole upstream modules that had no row at all, two of which
landed within the last 40 days (cgroup accounting at 6, cast buffers at 8, the
model compiler and CUDA graph capture at 12).

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
| **How exposed that leaves us** | The stand-in above is a fake model in ComfyUI's own list. It is how the host reclaims from a worker at all, and it is also the single most fragile thing comfy-env does: ComfyUI reads whatever it likes off anything in that list. This column says how exposed each row is. Read it as a forecast, not a history. comfy-env has been public since 2026-04-25, and in that time the stand-in has never raised on a user: every defect found in it so far has been a wrong number, found by review. The loud failure this column is organised around has not happened yet. |
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
| 1 | `free_memory` eviction ladder: when the card is short, the host ranks its loaded models and asks them to leave until there is room. The rank is a four key sort (`mm.py:875-882`): most already offloaded first, then lowest `sys.getrefcount`, then smallest, and only as a final tiebreak the list index, which is newest first because `load_models_gpu` inserts at 0. A model it never listed is never asked. | <span class="v v-yes">yes</span>: ComfyUI's own eviction loop reaches the stand-in we register for each worker model, and the worker unloads. It is the only path by which *upstream's own code* takes memory from another process. comfy-env has three more of its own that free memory the list cannot reach: an admission time ask to idle workers (`pool._ask_idle_workers`), a node boundary release (`pool._release_idle_workers`), and a pressure hook on the stand-in's own `partially_unload`, which is handed the exact shortfall and posts an ask to idle siblings without blocking this loop | <span class="v v-partial">fragile</span>: a pass reads `.device`, `.is_dead()`, `.model_offloaded_memory()`, `.model_memory()`, `sys.getrefcount(.model)` and `.model.is_dynamic()` on every candidate, then `.model_unload()` and `.model.model.__class__.__name__` on the ones it picks (the second inside an eagerly built log string, so it runs regardless of log level). `__eq__` is NOT read here: the `not in keep_loaded` test at `mm.py:878` runs against an empty list on every caller but `unload_model_and_clones`, and `x not in []` compares nothing. The every-load `__eq__` is row 2's. `.currently_used` is *written* here, never read; its only reader is `loaded_models()`. The loop body grew a dynamic branch (`:888-892`) when aimdo landed, we don't know if that might happen again in the future | <span class="v v-yes">yes</span> |
| 2 | `load_models_gpu` admission: before loading a model, the host adds up model size plus 10 percent, plus the larger of 0.8 GiB and the incoming estimate, plus the reserve, and frees that much first. | <span class="v v-yes">yes</span>: on Linux both sides read the same device wide free figure, so the sum is right without anything being declared; on Windows comfy-env supplies what the host cannot see. The sum itself is over incoming models and reads nothing off the stand-in. This row is also where the traffic runs the OTHER way, and it is the mechanism that makes most of row 3 unnecessary: a worker about to load calls back to the host (`request_vram_budget`), and the host runs ComfyUI's own `free_memory` on its behalf, re-deriving upstream's exact expression rather than a copy of it, and pre-compensating for its own blindness on Windows so the loop does not evaluate to "free nothing". The host may fill the card, because the worker can ask it to let go | <span class="v v-yes">stable</span>: two things touch the stand-in here and both are identity comparisons. `index()` runs upstream's `LoadedModel.__eq__`, which is `self.model is other.model` and reads `.model` off upstream's own wrapper. `is_clone` reads `model`, which is declared, and compares it with `is`. No value is read off the fake in this function. The `free_memory` call at the end is row 1's exposure, not a second one | <span class="v v-yes">yes</span> |
| 3 | The reserve, `EXTRA_RESERVED_VRAM` and `--reserve-vram`: how much of the card the host must always leave alone. Read on every load. The pager never reads it at all: what ComfyUI seeds once at startup is the pager's own headroom (row 11), from `--reserve-vram`. | <span class="v v-yes">yes</span>, and mostly not needed: on Linux the host already sees what packs hold, so comfy-env adds nothing of its own and leaves upstream's `EXTRA_RESERVED_VRAM` where it found it, which is 400 MiB by default and the operator's `--reserve-vram` only if they passed one. On Windows, where the host sees nothing of a pack, it publishes what each pack holds now, plus a 300 MiB floor per worker for the CUDA context and the cuBLAS and cuDNN handles. "What it holds" is one scalar per worker, `max(aimdo, torch)`, never their sum (the `held` census in `_persistent_worker`). Max was chosen from a sample where the two overlapped, 4.02 against 4.03 GiB for one 4 GiB model, so summing reserved 8 GiB for a 4 GiB worker. They are separate pools in general, though, so a worker paging 6 GB through aimdo while torch holds 2 GB of activations reports 6, and on Windows that under report is the reserve. The floor ADDS to residency rather than capping it: the context lives outside the caching allocator, which is exactly the memory `torch.cuda.memory_reserved` structurally cannot see, and the measured residency lives inside it, so the two partition cleanly and `max()` would under book by the smaller of them. Measured 276 to 300 MiB on Linux and an RTX 3090 (2026-09). On Linux the context is charged at zero along with everything else, because the device wide reading already includes it. The total is capped at three quarters of the card, so a pathological census cannot reserve the whole GPU. Preventive on the legacy path, where the partial load budget shrinks with it; forwarded to the pager (row 11) for the paged one | <span class="v v-yes">stable</span>: not a list path | <span class="v v-yes">yes</span>, plus a runtime headroom setter for the paged half |
| 4 | `get_free_memory`: "how much room is left", which also sizes batches. Driver free plus torch's idle cache; on Linux it covers the whole card, on Windows [only the calling process](windows-blind-spot.md). | <span class="v v-yes">yes</span> to read, not modifiable; via the registered stand-in, the fake's size never enters this number. Eviction targets are `required minus free`. On Linux free already includes what the worker holds, so the target is right. On Windows it does not, and the target is systematically too small, which is the whole reason row 3 declares anything | <span class="v v-yes">stable</span>: ledger sizes only order the eviction candidates, and the fake's size never enters this number at all. The one way it leaves the sort is row 35, and there it contributes the offloaded remainder, which is zero for a fully resident worker model | <span class="v v-partial">partial</span>: upstream must choose free-side or ledger-side, never both |
| 5 | Free memory as a BATCH SIZER, not an evictor. `ModelPatcher.get_free_memory` returns `mm.get_free_memory` plus `vbars_analyze`, everything the pager could still evict, and samplers and VAE encode/decode divide it by an activation estimate to choose a batch size (`samplers.py:273`, `sd.py:1231/1304/1373`). It fires several times per sampler step and decides gigabytes of activations. | <span class="v v-no">no</span>, and nothing can be done from here. The `vbars_analyze` term is strictly process local, so neither side counts what the other could give back. On Linux the `get_free_memory` term already counts what the other HOLDS. Net effect: both sides shrink their batches for each other and neither grows for the other. No stand-in is read; this number never passes through the list | <span class="v v-yes">stable</span>: no entry reads, and there is nothing to break. What it costs is throughput, silently | <span class="v v-partial">partial</span>: a holder would need to declare what it could RELEASE, not what it holds, which is a different interface from anything proposed |
| 6 | cgroup RAM accounting (`comfy/system_memory.py`, merged 2026-08-27). Clamps total and available RAM to the container's limit, and feeds `total_ram`, the pin ceiling, the pin budget floor, the Windows swap gate, CPU `get_free_memory` and both cache eviction targets. | <span class="v v-no">no</span>: comfy-env's own readings still come from `psutil` and see the machine. Inside a container the two sides now disagree, ComfyUI sizing against the cgroup and comfy-env against the host. Worse in kind than a wrong number: host and every worker share ONE cgroup, so each process independently sizes its pin ceiling and its cache headroom against the same single budget | <span class="v v-yes">stable</span>: no entry reads | <span class="v v-partial">partial</span>: upstream is right and comfy-env should follow it. The N processes sharing one cgroup problem is nobody's yet |
| 7 | `unload_all_models` and the Free button: an eviction ask for an absurd number (1e30) sent to every listed model between prompts. | <span class="v v-yes">yes</span>: the button reaches the stand-in and the worker releases. It does not arrive as 1e30, though: `model_unload` compares the ask against `loaded_size()`, 1e30 loses, and the stand-in is called with `detach(True)` (`mm.py:809-815`). Only a bare list entry implementing `model_unload` itself ever sees the sentinel | <span class="v v-yes">stable</span>: a `loaded_size()` read and one method call. The `unpatch_all` argument is honoured as of 2026-09-06, which only matters on row 35's path | <span class="v v-yes">yes</span> |
| 8 | Cast buffers. Per offload stream VRAM scratch, sized to the largest weight cast so far, plus an aimdo `VRAMBuffer` reserving 16 GiB of device address space per stream and committing in 16 MiB chunks. Allocated on the fault path, released only by `reset_cast_buffers` at the node boundary. | <span class="v v-partial">partial</span>: every process pays for its own and they are never shared. comfy-env books what the INCOMING load will want (`num_streams` times its largest tensor) into the admission ask, since those bytes exist neither in NVML nor in any measured field at admission time. What it cannot do is see or reclaim a sibling's. `torch.cuda.memory_reserved` sees the torch buffer and not the aimdo one, which is why the census takes `max(aimdo, torch)` | <span class="v v-yes">stable</span>: not a list path | <span class="v v-partial">partial</span>: needs a per device budget rather than a per process one |
| 9 | Partial load budget (`lowvram_model_memory`): load only as much of a model as fits after the reserve and keep the rest in RAM. The pager ignores this and decides page by page. | <span class="v v-no">no</span>, on both paths, for two unrelated reasons. On the legacy path the plumbing is complete and never invoked: the stand-in implements `partially_load`, the worker performs it, and nothing calls it. `partially_load` is reached only through `LoadedModel.model_use_more_vram`, whose callers are `model_load` and `use_more_memory`; the second is dead (one line in the tree, its own `def`) and the first runs only from `load_models_gpu`, which comfy-env deliberately never uses, inserting its entries by hand instead. The one route left was a node handing the stand-in back through row 35's leak, and that closed on 2026-09-06. Under aimdo the budget dies further downstream and would die anyway: it travels host to stand-in to IPC to the worker's real patcher, and `ModelPatcherDynamic.partially_load` accepts `extra_memory` and never passes it on (`mp.py:2141-2158`), calling `self.load(device_to)` and letting the pager decide residency page by page at fault time. So the budget is not a lever on either path; the lever is the pager's headroom, which is why row 11 exists. One thing runs the other way: upstream returns `None` from that method, with a comment saying it has no number to give, while the stand-in still returns a measured one, because the worker reads residency before and after rather than trusting the return | <span class="v v-partial">fragile</span>: six reads on the fake (`model_patches_to`, `model_dtype`, `partially_load`, `loaded_size`, `load_device`, `is_dynamic`); `is_dynamic` arrived with aimdo in 2026-01. `loaded_ram_size` (2026-05) is the read we are one False away from: `mm.py:1010` gates it behind `is_dynamic()`, and it is not on the stand-in's surface, so answering True would raise rather than lie | <span class="v v-yes">yes</span> |
| 10 | `LoadedModel` size questions: how the host asks each listed model how big it is and how much is on the card. The legacy count reads 0 for a paged model; only the pager's own count is right. | <span class="v v-partial">partial</span>: the stand-in answers all three, and the residency it answers with is measured correctly, by the worker calling its own real `loaded_size()` (`_resident_of` in `_persistent_worker`), which is right on both paths because the worker uses whichever implementation it has. What is partial is freshness. `loaded_size()` returns the last echo, not a live reading, and under aimdo the pager faults pages in and out between echoes with no message. So the honest gloss is "how much was on the card when we last talked". The lopsidedness is on a different number than you would guess: `apply_echo` writes the ledger `loaded_size()` answers with unconditionally, up or down, in every flag state. What may only rise mid-call is comfy-env's own admission peak, which ComfyUI never reads, because an idle worker cannot re-fault (faults are synchronous worker Python) and a busy one can | <span class="v v-partial">fragile</span>: `model_size`, `loaded_size`, `current_loaded_device`, and `loaded_size` means two different things upstream. Legacy (`mp.py:411`) returns `model_loaded_weight_memory`, which is 0 for a paged model; dynamic (`mp.py:1809`) returns `vbar.loaded_size()` plus that. The stand-in must pick one and answer one number, and the number is not decorative: `model_offloaded_memory()` is `model_size() - loaded_size()`, the PRIMARY sort key of the eviction loop. If upstream shifts what `loaded_size` means, nothing raises. The stand-in sorts into the wrong position, evicted when it should not be or never picked when the card is full. A number cannot throw, which makes this the one surface where drift is silent rather than loud | <span class="v v-yes">yes</span> |
| 11 | aimdo headroom: each process's pager keeps a safety margin, and ComfyUI seeds it once at startup from `--reserve-vram` and never touches it again. The setter itself is live: changing it steers the next page fault. | <span class="v v-yes">yes</span> now, on the two aimdo cells: comfy-env forwards `seed + (published - base)` into the pager's headroom at runtime, live at the next fault. That the setter is live is comfy-aimdo's contract as of #107, which documents it and ships a test asserting it; before that it was an undocumented C export and this repo's own experiment had concluded the opposite, because it used plain `nn.Linear` modules that never page. Worth knowing what the forward is worth per platform: on Linux the charge is 0, so published equals base, the added term is zero, and the value forwarded is the seed the pager already had. It moves a number only where the driver's free figure is process local. It steers the process the setter runs in, which is the host, and no worker's pager. It is also only half the pager's arithmetic: `simple_vram_headroom` appears in the per process cap term, while the cross process term uses the compile time 256 MiB constant. ComfyUI itself still seeds it once at startup and never again | n/a | <span class="v v-partial">partial</span>: ComfyUI should forward its own reserve too, rather than leaving it to us |
| 12 | Comfy model compiler, malloc graph and CUDA graphs (`comfy/model_prefetch.py`, landed 2026-08-13 and 2026-09-04). Per block weight prefetch doubles transient residency, the malloc graph records the allocation pattern for the pager, and CUDA graph capture holds a private allocator pool per module until cleanup. Capture performs a full `synchronize()` plus a device wide VBAR eviction while it records. | <span class="v v-no">no</span>, and this is the sharpest edge in the table. A sibling faulting during another process's capture window is coordinated by nothing, and capture's device wide eviction does not stop at the process boundary. Flags: `--disable-comfy-compiler`, `--disable-cuda-graphs` | <span class="v v-yes">stable</span> today: no entry reads. It runs at every node boundary and is the newest and hottest code in the file, so that is a statement about this week | <span class="v v-no">no</span>: nothing has been proposed, and cross process graph capture is not obviously solvable |
| 13 | The INACTIVE cache tier, drained at every node. A second headroom (`ram_inactive`, default all of RAM capped at 128 GB) is passed to `ram_release` after every node. Because available RAM is essentially never above that target, the previous workflow's cached outputs are evicted unconditionally, with no pressure test at all. | <span class="v v-yes">yes</span> for outputs, in the sense that it happens to host held worker results like any other cache entry. Nothing coordinates it with a worker | n/a | <span class="v v-partial">partial</span> |
| 14 | Per-layer fault and aimdo's C-side eviction: each layer is fetched onto the card when needed and the pager decides for itself what to drop, from device-wide pressure. Torch never sees these pages. | <span class="v v-yes">yes</span>, with no coordination and none possible from Python. The pager's pressure is `MAX` of a per process term and a polled device term, and only the second is cross process. On Windows that poll is NVML by default, so it sees siblings where ComfyUI's `get_free_memory` cannot, but `--disable-nvml-pressure` or a failed NVML init drops it back to the same process local figure. In the legacy cells nothing polls at all: with no `init_devices` there is no pager to read anything | n/a | <span class="v v-partial">partial</span>: needs a cross-process priority signal nobody has proposed |
| 15 | `model_unload` partial versus full: ask a model to shrink by the shortfall, else throw it out entirely. Returns True even if nothing was freed. | <span class="v v-yes">yes</span>: the stand-in implements `loaded_size`, `partially_unload` returning bytes actually moved, and `detach`. A short return escalates to detach, which is upstream's own contract | <span class="v v-partial">fragile</span>: `partially_unload` has a return contract (a short answer escalates to `detach`) and a second dynamic implementation via `vbar_free_memory`, the return contract since 2024-08, the dynamic implementation 2026-01 | <span class="v v-yes">yes</span> |
| 16 | OOM branch in `execution.py`: on out-of-memory the host logs a summary, clears every model and stops the run. No retry. | <span class="v v-yes">yes</span>: a worker OOM crosses as the real class (the worker stamps it from ComfyUI's own `is_oom`, the host rebuilds `torch.cuda.OutOfMemoryError`) so the branch fires, and it calls `unload_all_models`, so worker models are freed by the same path as row 7 | <span class="v v-yes">stable</span> in itself: same call as row 7. It inherits row 1's loop exposure, though, because that call is `free_memory` | <span class="v v-partial">partial</span>: only a holder adds its own line to the summary |
| 17 | The activation estimate itself. `BaseModel.memory_required` is area times dtype size times a per model constant, and the dynamic patcher adds 30 percent plus a flat 1 GiB. This is the `memory_required` that row 4 sums. | <span class="v v-yes">yes</span> to read, and it is a per model constant tuned on one process now sizing the reserve on two. A worker's estimate and the host's are computed independently from the same formula and never reconciled | <span class="v v-yes">stable</span>: not a list path | <span class="v v-partial">partial</span> |
| 18 | `/free` with `free_memory`: the stronger button also throws away every remembered step result, including results a worker sent back. | <span class="v v-partial">partial</span>: the stand-in hears the unload half of the button. The cache reset it never hears about is not a leak, though: `e.reset()` rebuilds the whole `CacheSet`, so host side copies of worker results are dropped with everything else. What survives is what the worker still holds in its own process, which nobody asked it to drop | <span class="v v-yes">stable</span>: for the half it hears | <span class="v v-partial">partial</span>: needs a cache reset hook |
| 19 | `cleanup_models` prune: whenever a model object dies, the host erases every list entry whose `real_model()` is gone. | <span class="v v-yes">yes</span>, nothing to do; via the registered stand-in, the fake's wrapper carries a `real_model` weakref | <span class="v v-partial">fragile</span>: the prune assumes every entry went through `model_load`; comfy-env sets `real_model` and `model_finalizer` by hand (`pool._insert_loaded_model`), matching internals upstream never promised. Getting it wrong is not a quiet wrong number: `cleanup_models` and `is_dead` CALL `real_model()`, and `cleanup_models_gc` is the first statement of both `free_memory` and `load_models_gpu`, so a single unplanted entry raises `TypeError` on every load and every free for the life of the process | <span class="v v-yes">yes</span> |
| 20 | Async offload streams. `NUM_STREAMS` defaults to 2, set by `--async-offload N` or disabled by `--disable-async-offload`. Each stream gets its own cast buffer. | <span class="v v-yes">yes</span>: mirrored to workers as a resolved value, and read LIVE rather than from the mirror when booking cast buffers, because it must be the number the cast path will actually use. It is the multiplier on the cast buffer row | n/a | <span class="v v-yes">yes</span> |
| 21 | `cudaMallocAsync` as the default allocator (`cuda_malloc.py`), unless `--disable-cuda-malloc`. | <span class="v v-yes">yes</span> to read, and it is why a GPU tensor crossing the process boundary is copied rather than shared: the async allocator's pool is not IPC exportable, so comfy-env's zero copy CUDA IPC tier is unavailable under it and the transport falls to a shared memory copy. It also changes what `empty_cache` gives back | n/a | n/a |
| 22 | Prompt boundary signals: the prompt id in `comfy_execution.progress`, and the merged cache provider hooks for job start and end. | <span class="v v-yes">yes</span> for the id; the provider is merged and comfy-env registers none | n/a | <span class="v v-yes">yes</span>, already merged |
| 23 | `model_load` and the finalizer tripwire: loading a listed model also plants a weakref that erases its entry when the model dies. | <span class="v v-yes">yes</span>, and not because the host did it: comfy-env inserts the entry directly rather than through `load_models_gpu`, so it plants `real_model` and `model_finalizer` by hand (`pool._insert_loaded_model`) to do upstream's job for it. The host only reaches `model_load` on a stand-in through row 35's leak | <span class="v v-partial">fragile</span>: `.model.model` must be a stable weakref-able object forever; `model_patches_to`, `model_dtype` and `partially_load` are all read on the way, which is row 9's surface arriving by this path | <span class="v v-yes">yes</span> |
| 24 | Dirty mmap bounce. `mark_mmap_dirty` records mmapped storages written through during a cast, and `reset_cast_buffers` bounces them at the node boundary, which is what stops a dirtied private page from being charged to this process forever. `--mmap-torch-files` / `--disable-mmap`. | <span class="v v-yes">yes</span>: mirrored, and this is the mechanism that makes shared page cache real rather than aspirational. Two workers loading the same checkpoint share its pages only while nothing dirties them | <span class="v v-yes">stable</span>: not a list path | <span class="v v-yes">yes</span> |
| 25 | Entry identity and the dead-entry sweep: the list holds a weak grip on each model; if the owner vanishes while weights remain, the host runs a full garbage sweep. | <span class="v v-partial">partial</span>: effect yes, visibility no; comfy-env must hold the fake's patcher strongly, and the consequence of not doing so is quieter than a sweep. `real_model` is a weakref to the fake's inner `SubprocessModel`, whose only strong reference is the patcher itself, so both die together, `is_dead()` stays False, and `cleanup_models` simply prunes the entry with no signal at all | <span class="v v-partial">fragile</span>: `__eq__` is `.model` identity (2023-08), `is_dead` reads the weakref and `_switch_parent` rebinds to `.parent` when a clone dies (both 2024-12). `_switch_parent` cannot fire on a stand-in at all: it is armed only when `model.parent` is not None, and the fake sets it None | <span class="v v-yes">yes</span> |
| 26 | Clone dedup and `is_clone` probing: before every load the host checks whether this model or a twin is already listed and throws out the twin. | <span class="v v-yes">yes</span> for the case that matters: upstream runs `is_clone` on the INCOMING host model with the fake as its argument, and answers False, so a worker model is never mistaken for a twin of a host one. It is not unconditional. The fake's own `is_clone` answers True for itself, so a stand-in arriving as an incoming model matches its own listed entry and is popped and detached. That was expensive until `detach` learned to honour `unpatch_all=False` on 2026-09-06, and unreachable since the same day, when the leak that delivered it closed | <span class="v v-no">broke</span>: this is where the proxy broke twice, though only one of the three runs on the fake. `__eq__` does, via `index()`, on every load. `is_clone` runs on the *incoming* model with the fake as its argument, so what reads the fake is upstream's `hasattr(other, 'model')`. `model_patches_models()` never touches a list entry. Any new read in this function still lands here | <span class="v v-yes">yes</span> |
| 27 | `is_dynamic` gate and the per-node ledger walk: a yes or no tag deciding whether the host digs into a model's pinned-RAM internals after every step. The walk itself is gated on `aimdo_enabled`, so in the legacy cells it does not run at all. | <span class="v v-yes">yes</span>: the worker resets its own; via the registered stand-in, the fake answers `is_dynamic()` False and nothing deeper is read | <span class="v v-no">broke</span>: answering True means faking `dynamic_pins`, a dict of four six element positional tuples plus four scalar flags, of which `reset_cast_buffers` rebuilds two. It landed 2026-05-21 and the layout has moved twice since: four element tuples became six on 05-31, and 07-29 added the two `-loaded` subsets and two more flags (05-25 relocated the construction without changing a field) | <span class="v v-yes">yes</span> |
| 28 | Hook weight caching. `patch_hooks` builds a `MemoryCounter` seeded with the WHOLE of `get_free_memory(load_device)` and keeps hook backups on the GPU until it runs out. | <span class="v v-no">no</span>: one number, taken once, spent against a card two processes share. Nothing declares it and nothing reclaims it. Fires only in workflows that use hooks | <span class="v v-yes">stable</span>: not a list path | <span class="v v-partial">partial</span> |
| 29 | The unpatch backup dict. `patch_weight_to_device` keeps a CPU copy of every patched weight for the life of the patch, cleared only on `unpatch_model`. A LoRA'd checkpoint costs its patched weights twice in host RAM. | <span class="v v-no">no</span>: per process, invisible, and it is host RAM rather than VRAM, so the pin ladder never sees it either | <span class="v v-yes">stable</span>: not a list path | <span class="v v-partial">partial</span> |
| 30 | Multigpu deepclones. `deepclone_multigpu` makes a full copy of a model per extra GPU, and `match_multigpu_clones` runs on every `_prepare_sampling`. | <span class="v v-no">no</span>: comfy-env is single device throughout, everything routing through one `get_torch_device()`. Rare, but the bytes are a whole model each | <span class="v v-partial">fragile</span>: `multigpu.py` is the one caller that reads `loaded_models()` UNFILTERED, so the stand-in is in the list it walks; it is filtered out by the `clone_base_uuid` mismatch two lines before `clone()` | <span class="v v-no">no</span>: multi GPU is a different design problem |
| 31 | Pin eviction ladder: when machine RAM gets tight, listed models let go of their locked RAM, models not used by this job first. Reads machine-wide available RAM. | <span class="v v-no">no</span>: worker pins are invisible, and until 2026-09-06 comfy-env was not merely absent from this ladder but the reason it ran. A stand-in in a leaked `loaded_models()` list answers `is_dynamic()` False, which adds its FULL `model_size()` to `total_pins_required` (`mm.py:970-971`) and flips `free_for_dynamic`; `free_memory` then spends that on `ensure_pin_budget`, whose only reachable victims are the host's own dynamic models. With the pager running the entire ask was phantom, because host models are dynamic and book nothing. Closed by registering every stand-in with `currently_used` False, which shuts six of the seven leak sites. The two sides also do not stop at the same floor. The default branch is `max(RAM_CACHE_HEADROOM / 2, 2 GiB)`, and `RAM_CACHE_HEADROOM` is set by the executor for the duration of each prompt (`execution.py:748`, `min(10, max(2, total_ram * 0.10))` GB). A worker runs no executor, so its headroom stays 0 and its floor is exactly 2 GiB while the host's is higher on any machine above roughly 40 GB. Under `--fast-disk` the test is not a floor at all but the per process `MAX_PINNED_MEMORY` ceiling, and under `--high-ram` there is no test | <span class="v v-yes">stable</span>: `models_for_pin_eviction` skips anything answering `is_dynamic()` False before it reads `dynamic_pins`, so the churned tuple layout is row 27's exposure, not this one | <span class="v v-partial">partial</span>: needs a pin facet nobody has described |
| 32 | `--disable-smart-memory`: forget every model after each run; every eviction ask becomes 1e32. | <span class="v v-yes">yes</span>: the flag is read, and the prompt-end unload is not invisible either. `execution.py` calls `unload_all_models` when it is set, which walks the list and reaches the stand-in exactly as in row 7, so the worker forgets its model after every run, which is what the flag means | <span class="v v-yes">stable</span> | <span class="v v-yes">yes</span> |
| 33 | Node output cache and RAM-pressure release: the host keeps every step's results and drops the oldest and biggest when RAM runs low. Worker results are in that pile. | <span class="v v-yes">yes</span> for outputs, with upstream's own bug attached: the release pops the *largest* tuple, so on a score tie it evicts the most recently touched entry rather than the oldest, contradicting the comment above it | n/a | <span class="v v-partial">partial</span> |
| 34 | Allocator cache release (`soft_empty_cache`): hand the driver back the memory torch kept in its pocket, after an eviction, after a run, before a retry. | <span class="v v-partial">partial</span>: not every call reaches it. `free_memory` runs `soft_empty_cache` only if something was actually unloaded, or if torch's idle cache exceeds a quarter of free and `vram_state` is not `HIGH_VRAM`. A pass that evicts nothing under `--highvram` never gets there. And none of it crosses the boundary in any case: `torch.cuda.empty_cache()` is per process, so the host calling it releases nothing a worker's allocator holds. comfy-env empties the worker's on its own schedule, which is not ComfyUI's. No entry reads | <span class="v v-yes">stable</span> | <span class="v v-yes">yes</span> |
| 35 | `loaded_models()` leak into node code: controlnet and a few extras nodes borrow the list and hand it straight back to `load_models_gpu`, so anything in it is treated as a real model. | <span class="v v-yes">yes</span> as of 2026-09-06: every stand-in is now registered with `currently_used` False, and that flag is the only thing `loaded_models(only_currently_used=True)` filters on, so six of the seven callers no longer see a worker model at all. Only `multigpu` reads the list unfiltered, and it never re-loads. What follows is what the leak cost while it was open, and what still applies to that seventh reader. Controlnet and three extras nodes do hand the stand-in back into `load_models_gpu`, but `model_memory_required` asks for the offloaded remainder of a model already on the target device, and a resident worker model has none, so it adds zero. The cost was never a mere re-fault, though. Before admission, the clone dedup loop runs, the stand-in's own `is_clone` matches ITSELF, and the entry is popped and detached: a full worker offload, then a full reload. That is fixed too, by honouring `unpatch_all=False` the way upstream does. `multigpu` reads `load_device` and `clone_base_uuid` and would call `clone()`, which the stand-in raises on, but it never gets there: the `clone_base_uuid` mismatch filters the fake out two lines earlier | <span class="v v-no">broke</span>: seven call sites in five files outside `model_management.py` read the list, and node code can read anything. Only `multigpu.py` reads it unfiltered | <span class="v v-yes">yes</span> |
| 36 | Interrupt flag: the stop button, checked before every node and every cast. It returns memory mid-step by unwinding. | <span class="v v-partial">partial</span>: forwarded at progress callbacks only, and the forward consumes the flag. `throw_exception_if_processing_interrupted` clears it before raising, so it is a one shot handoff to whichever side checks first, not a mirror | n/a | <span class="v v-no">no</span>: it is a call into the worker, not a holder interface |
| 37 | `unload_model_and_clones`: throw out one model and its copies but keep everything else, using the same 1e30 as the button. | <span class="v v-yes">yes</span>: the stand-in's `clone_base_uuid` is a private sentinel object, so it can never equal a uuid and never lands in the freed set. It was `None` until 2026-09-06, which worked for exactly one reason: upstream assigns `uuid.uuid4()` in `ModelPatcher.__init__`, so no real target carries `None`. Latent, not live, because the comparison is target against entry and never entry against entry. Shipping on somebody else's constructor was the wrong bet, and one `object()` retires it | <span class="v v-partial">fragile</span>: `clone_base_uuid` is an internal identity two callers compare directly | <span class="v v-yes">yes</span> |
| 38 | `GET /system_stats`: the numbers the UI gauge shows. Worker allocations show as used, never as reclaimable. | <span class="v v-partial">partial</span> on Linux. On Windows it is worse than the header says: `vram_free` comes from `get_free_memory`, so worker allocations are not shown as used either, they are absent, and the gauge reads the card as freer than it is. The RAM half of the same endpoint behaves the opposite way on both platforms: it comes from a machine wide figure, so worker RAM does count as used | n/a | <span class="v v-partial">partial</span> |
| 39 | `MAX_PINNED_MEMORY` and hostbuf ceilings: every process assumes it may lock most of the machine's RAM, so N processes promise N times the RAM. | <span class="v v-partial">partial</span>: mirrored, and it does bind by default, through `ensure_pin_registerable`, which compares `TOTAL_PINNED_MEMORY + size` against it on every pin, and through `pinned_hostbuf_size`, which caps each dynamic model's host buffer. What `--fast-disk` changes is the OTHER test, row 31's: it swaps that test's machine wide available RAM floor for this same per process ceiling. The share is not "most" everywhere either: 40 percent on Windows against up to 90 elsewhere, so two workers on Windows already overcommit the pool | n/a | <span class="v v-partial">partial</span>: needs a coordinator |

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
- **Only upstream fixes** rows 4, 11, 14, 18, 31, 38 and 39. Row 14 is the
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
    | 21 | Interrupt checks inside node loops | Long loops peek at the Cancel flag | The code runs unchanged on the worker's own flag; the host never sets it (see row 36 above) |
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
