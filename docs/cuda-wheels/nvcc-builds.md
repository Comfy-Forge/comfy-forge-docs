# nvcc builds and what we can change in the settings

A CUDA compile on a hosted runner is a memory-budgeting problem.

GitHub's standard runners give you **4 vCPUs and 16GB of
RAM** (Linux and Windows alike).

A single `cicc` instance (nvcc's device-code front end) peaks at **roughly 4GB** on heavy template code.
The knobs below decide how many of those 4GB processes exist at once.

## The knobs that control memory usage.

Two knobs multiply, and both default to "more parallelism":
- **`MAX_JOBS`** — how many translation units torch's `cpp_extension`
  compiles in parallel (it hands this to ninja as `-j`).
  When unset, torch sizes
  it from `cpu_count()`.
- **`NVCC_THREADS`** (`nvcc --threads N`) — within *one* translation unit,
  how many target architectures nvcc compiles concurrently.
  Setting it to 4 means that 4 arches (sm_80, sm_86, sm_10...) get compiled at once.

So `MAX_JOBS=4 × NVCC_THREADS=4 × 4GB = 64GB` of theoretical peak on a
16GB machine. That is why "just let it default" ends in a SIGKILL around
minute three.

## What the farm sets, and where

`NVCC_THREADS` is auto-tuned in `build-wheel` (< 20GB free RAM → 2, else 4
— hosted runners always get 2). `MAX_JOBS` is unset by default (ninja then
runs ≈ cores+2 = 6). Both are yours to override per package: `max_jobs:` in
`package.yml`, `nvcc_flags: --threads=N` (trailing flags win over the
auto-tune), or the `max_jobs` dispatch input for a one-off run. Heavy
packages already do: natten runs `2 × --threads=1`; mmcv, sageattention and
gsplat_maskgaussian cap `max_jobs: 2`; flash_attn's patched setup.py sizes
its own MAX_JOBS from free RAM ÷ arch count.

## The escalation ladder

When a package OOMs or overruns, escalate in this order — each step costs
more machinery than the last:

1. **Trim parallelism**: `max_jobs: 2`, or `nvcc_flags: --threads=1`
   (natten's proven pair: `max_jobs: 4` OOM'd ~3 minutes in; `2` +
   `--threads=1` + 8GB swap held stable for full 3h links).
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
