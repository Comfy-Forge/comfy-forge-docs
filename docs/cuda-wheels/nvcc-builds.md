# nvcc builds and what we can change in the settings

A CUDA compile on a hosted runner is a memory-budgeting problem.

GitHub's standard runners give you **4 vCPUs and 16GB of
RAM** (Linux and Windows alike).

A single `cicc` instance (nvcc's device-code front end) is usually small
(0.3-1GB) but peaks at **7-11GB** on heavy template code — measured, not
guessed: a real natten build showed one cicc at **10.6GB**, and two
simultaneous 7.5GB ciccs took a 16GB runner to 0.1GB free and 3GB into
swap at `max_jobs: 2`. The knobs below decide how many of those processes
exist at once.

## The knobs that control memory usage.

Two knobs multiply — and they are NOT interchangeable:
- **`MAX_JOBS`** — how many translation units torch's `cpp_extension`
  compiles in parallel (it hands this to ninja as `-j`).
  When unset, torch sizes
  it from `cpu_count()`.
- **`NVCC_THREADS`** (`nvcc --threads N`) — within *one* translation unit,
  how many target architectures nvcc compiles concurrently.
  Setting it to 4 means that 4 arches (sm_80, sm_86, sm_10...) get compiled at once.

The farm pins **`NVCC_THREADS=1`**. The reason: `--threads` parallelizes
the *same* file's per-arch passes, which have near-identical memory
profiles — their peaks land at the same instant (2 threads = 2× peak).
Two ninja jobs compile *different* files, so their peaks hit at different
times. Per GB of RAM, MAX_JOBS-parallelism is strictly better than
threads-parallelism; spend the budget there.

## What the farm sets, and where

Both knobs are **first-class package.yml fields**: `nvcc_threads`
(farm default **1**) and `max_jobs` (farm default **3** — "unset" used to
mean ninja -j6, gsplat's self-set 10, mmcv's 32, or serial, depending on
which setup.py you asked). They flow generate_matrix → build.yml →
build-wheel, which enforces threads as a **trailing `--threads=N` on
`NVCC_APPEND_FLAGS`** — nvcc applies append-flags last and the last
`--threads` wins, so the knob reaches every build system and overrides
setup.py hardcodes (nunchaku, torchao, sageattn3). `max_jobs` is also
mirrored into `CMAKE_BUILD_PARALLEL_LEVEL` for the CMake family
(pyg_lib, llama_cpp_python) that never sees ninja `-j`. One-off override:
the `max_jobs` dispatch input.

**Swap: avoid using it like the plague — and CI enforces that.** An 8GB
swapfile exists purely as a safety net (a brief peak becomes a slow minute
instead of a SIGKILL at hour 3). The resource monitor tracks a swap
high-water mark and a MemAvailable low-water mark, and after the compile a
gate rules on both. It has **three** outcomes, not two:

- **Already at the floor** (`max_jobs: 1`, `nvcc_threads: 1`) and swap was
  touched at all -> **warn**. This branch is tested first, whatever the
  magnitude: there is nothing left to trim, so the parachute did its job.
- **Peak swap above ~1GB AND available memory bottomed out below ~2GB** ->
  **fail**, with an annotation naming the cell, the peak and the low-water.
  Both conditions, not either.
- **Anything else** -> **warn**. Swap touched while memory stayed
  comfortable is the kernel paging out idle pages, not an over-committed
  compile. A green cumesh build was once rejected over 0.30GB of swap with
  10GB still free; that is why the second condition exists.

## The escalation ladder

When a package OOMs or overruns, escalate in this order — each step costs
more machinery than the last:

1. **Trim parallelism**: `max_jobs: 1` is where the heaviest packages ended
   up (natten and flash_attn both), because sharding changes the arithmetic
   -- a shard holds few enough translation units that one job at a time is
   the right setting, and anything more re-introduces the peak.
2. **Trim the arch list -- NO. Do not do this.**

    This rung used to read "if the package has a real floor, an
    `arch_override.yml` is both correctness *and* memory relief". Narrowing
    an arch list to survive a build is banned, for a reason that is easy to
    miss: the pre-upload arch check compares the wheel against the
    *resolved* list, so trimming moves both sides of the comparison at once
    and **the build goes green having silently dropped GPUs**. spconv lost
    its Blackwell cubins exactly that way, under a comment asserting the
    trim changed nothing about the shipped wheel; a later edit to the same
    file dropped Ada and nobody noticed for two days.

    If a package genuinely cannot compile an arch, fix it at the source: an
    `__CUDA_ARCH__` guard with a real fallback, the documented `atomicCAS`
    shim for `atomicAdd(double*)` below sm_60, or pinning a vendored
    dependency below its new floor. See
    [How a cell gets its arch list](arch-selection.md).

3. **Shard** (`sharding: N`): not only `cpp_extension` packages -- natten is
   a cmake build and spconv is pccm, and both shard in production. There are
   three knobs, not one (`sharding`, `shard_filter`, `sharding_platforms`),
   and picking the wrong filter is a *silent* slow build. See
   [Sharding](sharding.md).
(A fourth rung -- sequential checkpointing -- was removed 2026-08-21
after measurement showed nothing needed it; see the build-process page.)

## Upstream setup.py estimators lie

Some packages auto-size their own parallelism from free RAM, with baked-in
assumptions that break on our matrix. flash-attention divides free memory by
9GB assuming **2 arches per job**; on cu128+ we compile 4, so its own
estimate would OOM.

The farm's patch scales the divisor by the actual arch
count (`packages/flash_attn/patches/flash_attn.py`). When adding a package
that "auto-tunes", read its estimator against the multiplication above
before trusting it.
