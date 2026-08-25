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
high-water mark, and after the compile a gate rules on it: **any swap use
fails the build** with an error annotation naming the cell and its peak
swap GB — unless the package is already at the `max_jobs: 1`,
`nvcc_threads: 1` floor, where swap merely warns (nothing left to trim;
the package is at the runner's memory ceiling).

## The escalation ladder

When a package OOMs or overruns, escalate in this order — each step costs
more machinery than the last:

1. **Trim parallelism**: `max_jobs: 2` (natten's proven setting:
   `max_jobs: 4` OOM'd ~3 minutes in; `2` with single-threaded nvcc held
   stable for full 3h links).
2. **Trim the arch list**: if the package has a real floor (Ampere-only
   kernels), an `arch_override.yml` is both correctness *and* memory relief.
3. **Shard** (`sharding: N`): torch `cpp_extension` packages only — split
   the translation units across N parallel jobs
   ([the 6-hour cap](build-process.md#sequential-and-sharded-compiles)).
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
