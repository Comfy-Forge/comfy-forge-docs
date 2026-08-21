# nvcc builds and what we can change in the settings

A CUDA compile on a hosted runner is a memory-budgeting problem.

GitHub's standard runners give you **4 vCPUs and 16GB of
RAM** (Linux and Windows alike).

A single `cicc` instance (nvcc's device-code front end) peaks at **roughly 4GB** on heavy template code.
The knobs below decide how many of those 4GB processes exist at once.

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

`NVCC_THREADS` is pinned to **1** in `build-wheel` (both platforms;
overridable via env). `MAX_JOBS` is unset by default (ninja then runs
≈ cores+2 = 6). Override per package with `max_jobs:` in `package.yml` or
the `max_jobs` dispatch input for a one-off run. Heavy packages already
do: natten, mmcv, sageattention and gsplat_maskgaussian cap `max_jobs: 2`;
flash_attn's patched setup.py sizes its own MAX_JOBS from free RAM ÷ arch
count.

**Swap: avoid using it like the plague.** An 8GB swapfile exists purely as
a safety net — it turns a brief memory peak into a slow minute instead of
a SIGKILL at hour 3. If the resource monitor shows swap in *active* use,
the compile is misconfigured: the fix is fewer jobs (`max_jobs`), never
more swap — thrashing through a swapfile is slower than running narrower.

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
   ([the 6-hour cap](build-process.md#what-if-a-compile-takes-longer-than-6-hours)).
4. **Sequential checkpoint** (`sequential_checkpoint: <seconds>`): CMake/
   ninja trees that sharding can't touch — 3h links, up to 6 per platform.

## Upstream setup.py estimators lie

Some packages auto-size their own parallelism from free RAM, with baked-in
assumptions that break on our matrix. flash-attention divides free memory by
9GB assuming **2 arches per job**; on cu128+ we compile 4, so its own
estimate would OOM.

The farm's patch scales the divisor by the actual arch
count (`packages/flash_attn/patches/flash_attn.py`). When adding a package
that "auto-tunes", read its estimator against the multiplication above
before trusting it.
