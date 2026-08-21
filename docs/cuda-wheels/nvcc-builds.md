# nvcc builds and what we can change in the settings

A CUDA compile on a hosted runner is a memory-budgeting problem wearing a
compiler costume. GitHub's standard runners give you **4 vCPUs and 16GB of
RAM** (Linux and Windows alike), and a single `cicc` instance — nvcc's
device-code front end — peaks at **roughly 4GB** on heavy template code
(CUTLASS instantiations are the usual offender). The knobs below decide how
many of those 4GB processes exist at once.

## The multiplication that OOMs you

```text
peak memory ≈ MAX_JOBS  ×  NVCC_THREADS  ×  ~4GB
              (parallel      (arches compiled
               .cu files)     in parallel per file)
```

Two knobs multiply, and both default to "more parallelism":

- **`MAX_JOBS`** — how many translation units torch's `cpp_extension`
  compiles in parallel (it hands this to ninja as `-j`). Unset, torch sizes
  it from `cpu_count()`.
- **`NVCC_THREADS`** (`nvcc --threads N`) — within *one* translation unit,
  how many target architectures nvcc compiles concurrently. Each arch in
  `TORCH_CUDA_ARCH_LIST` is its own cicc pass; `--threads 4` runs four of
  them at once.

So `MAX_JOBS=4 × NVCC_THREADS=4 × 4GB = 64GB` of theoretical peak on a
16GB machine. That is why "just let it default" ends in a SIGKILL around
minute three.

## What the farm sets, and where

| Knob | Where | What the farm does |
|---|---|---|
| `MAX_JOBS` | `package.yml: max_jobs`, or the `max_jobs` dispatch input | Unset by default (torch uses `cpu_count()` = 4). **`0` means unset, not unlimited** — an old guard exported `MAX_JOBS=0`, which ninja reads as `-j INT_MAX`; that bug is why several packages once carried hand caps they no longer need. natten still legitimately caps at `2`. |
| `NVCC_THREADS` | auto-tuned in `build-wheel` (both platforms) | `< 20GB` free RAM → `2`, else `4`. Each cicc ≈ 4GB; the auto-tune keeps the multiplication inside the machine. Overridable via env. |
| `nvcc_flags` | `package.yml`, exported as `NVCC_APPEND_FLAGS` | Appended flags **win over earlier ones**, so `--threads=1` here overrides the auto-tuned `--threads $NVCC_THREADS` that cpp_extension emits — natten uses exactly this to bound per-process memory. Also the home of warning suppressions. |
| the arch list | `defaults/arch_policy.yml` + per-package `arch_override.yml` | Every extra arch is one more cicc pass per file (and a fatter fatbin). A 7-arch policy row costs real memory and minutes; packages with narrow floors (flash_attn: 4 arches) compile measurably lighter. `+PTX` adds a pass too. |
| swap | the "Add swap space" step | 8GB swapfile on Linux builds (and unconditionally for chain-mode packages). Swap turns an OOM kill into slow-but-alive; cheap insurance when a single link spikes. |
| the resource monitor | `build-wheel` background step | Logs `mem / swap / load / top-3 processes` every interval, so a build that died at minute 40 leaves a memory trace instead of a mystery. |

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
estimate would OOM. The farm's patch scales the divisor by the actual arch
count (`packages/flash_attn/patches/flash_attn.py`). When adding a package
that "auto-tunes", read its estimator against the multiplication above
before trusting it.
