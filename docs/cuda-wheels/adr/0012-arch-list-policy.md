# CW-ADR-0012: Arch lists are a per-CUDA policy, clamped by torch runnability

**Status:** proposed (supersedes the arch-list half of [CW-ADR-0005](0005-shared-grid-and-arch-list-policy.md))

## Decision

> **The arch list is keyed on CUDA version alone — five owned rows, each
> with a written reason — then mechanically clamped to what the toolkit
> can emit and what the paired torch can execute. PyTorch's lists are an
> input to the clamp, never the policy. `+PTX` is carried per major
> family, not on the numerically highest arch.**

```
effective(cuda, torch) = policy[cuda]
    ∩ nvcc_supported(cuda)              # hard toolkit constraint
    ∩ torch_runnable(cuda, torch)       # floor/ceiling from torch's own build
  + PTX on the highest arch of each major family present
```

Per-package overrides (`arch_list_by_cuda` / `arch_list`) still win over the
policy row — kernel floors are properties of the package, not the farm — but
they pass through the same clamp, so an override that a combo's torch cannot
execute collapses to empty and **fails loudly** instead of building dead code.

## Context

CW-ADR-0005 said "arch lists mirror PyTorch's own build scripts." Four
independent reviews (a Torvalds-style design review, a ComfyUI-population
review, an SRE review, and a CUDA-compilation review with measurements)
converged on the same finding: that policy hand-copies an upstream cost
decision while inheriting none of its rationale.

What the audit of the 21 hand-copied rows found:

- **21 rows encode 3 distinct lists.** The torch axis carried almost no
  information — it was transcription, not decision.
- **The variation it did carry was invisible churn.** sm_70 (Volta) appeared
  on cu128 for torch 2.8–2.10 but not 2.7 or 2.11. A V100 user got a working
  or broken wheel depending on a torch version — with no recorded reason.
  nvcc 12.8 compiles sm_70 fine; the flicker is PyTorch's support policy
  (pytorch#157517).
- **The source being mirrored is not an interface.** PyTorch's lists live in
  three files in two languages (`build_cuda.sh`, deleted in 2.13;
  `build_env_setup.py`, its replacement; `cuda_config.bat` for Windows,
  which genuinely differs — it adds 6.1 on cu126). The farm's fetcher
  downloaded and *executed* the bash variant to recover a 40-character
  constant.

## Which axes are real

| Axis | Constraint? | Why |
|---|---|---|
| CUDA toolkit | **hard** | nvcc's supported set is fixed per release; CUDA 13 dropped sm_50/60/70. This is the policy key. |
| Package kernels | **hard** | FP8 needs 8.9+, FP4/tcgen05 needs Blackwell, bf16/cp.async needs 8.0. Stays a per-package override. |
| torch version | **bound only** | Our wheels run inside a torch process. If that torch ships no SASS a device can load and no PTX at or below it, the device cannot run torch — cubins for it are unreachable. Torch contributes a floor and (when it ships no PTX) a ceiling; it never contributes the list. |
| Python version | none | ABI tag; zero relationship to GPU codegen. |
| OS | none | nvcc targets the same arches everywhere. PyTorch's Windows 6.1 entry is a performance tune for its own kernels, already covered by 6.0 via same-major compat. |

The torch bounds come from a **committed snapshot** of PyTorch's build
tables — parsed (`ast.literal_eval` for ≥ 2.13's Python dict, regex for the
older bash), never executed, refreshed by a non-deploying workflow whose
diffs arrive as PRs. Build output is a function of the git SHA; there is no
network fetch at build time.

## The coverage rule (what a list actually promises)

CUDA binary compatibility is **per major version**: a cubin for sm_X.y runs
on sm_X.z, z ≥ y — and never across majors. PTX for compute_X JIT-compiles
on any device ≥ X — and never below. Therefore:

```
coverage(list) = ∪ per major M present: [lowest cubin in M → end of M]
              ∪ [lowest PTX arch → ∞)
```

Two consequences, both verified with ptxas during review:

1. **"+PTX on the highest arch" was broken as a forward-compat promise.**
   cu128+ wheels carried compute_120 PTX, which *cannot* JIT for sm_100/103
   (B200/B300) or sm_110 (Thor) — ptxas rejects a .target above the device.
   The promise held only for CC ≥ 12.1. Hence PTX **per major family**.
2. **A major with no cubin, below the PTX arch, is a dead device** — it does
   not "fall back", it gets `cudaErrorNoKernelImageForDevice` at first
   kernel launch (lazy loading defers it past import). Exclusions are
   therefore computed and printed (on the P.A.M page), never implied.

## The policy rows

| CUDA | Policy | Notes |
|---|---|---|
| cu124, cu126 | `6.0;7.0;7.5;8.0;8.6;9.0+PTX` | drops 5.0 (Maxwell ≈ 0 users, no bf16); keeps 6.0 — GTX 10-series rides it same-major |
| cu128, cu129 | `7.0;7.5;8.0;8.6;9.0+PTX;10.0;12.0+PTX` | clamp trims 7.0 exactly where the paired torch cannot execute on Volta |
| cu130+ | `7.5;8.0;8.6;9.0+PTX;10.0;12.0+PTX` | 7.0 impossible — toolkit floor |

Covered by same-major compat, hence deliberately absent as slots: 6.1, 8.7,
8.8, **8.9**, 10.3, 12.1. Declared uncovered: **sm_110 (Thor)** everywhere,
Maxwell after the 5.0 drop — embedded/automotive majors are out of scope for
a desktop farm.

**8.9 (Ada) stays opt-in.** The review compiled fp16/bf16 attention kernels
for sm_86 and sm_89: byte-identical SASS. Absent FP8 there is nothing to
gain, and the five FP8 packages (natten, nunchaku, sageattention, torchao,
natten_sequential) already declare 8.9. The associated hazard — source gated
on `__CUDA_ARCH__ >= 890` compiling to stubs that dispatch selects at
runtime — is handled by a lint (CW-ADR-0013), not by paying +1 arch on 40
packages that don't use it.

**10.0 (B200) stays in.** It looks like datacenter dead weight, but it is a
different major: without it, Blackwell datacenter is not "slower", it is
unsupported — sm_120 cubins won't load there and compute_120 PTX won't JIT
down.

## Costs, measured

Per-arch SASS compile cost is exactly linear (93 s for one arch, 373 s for
four, measured with nvcc 13.0). Carrying PTX is ~free at build time (the
front end emits it anyway) and ~8 KB per compressed fatbin. The expensive
case is a device actually *hitting* the PTX path: JIT is per module, ~0.3 s
per kernel measured, minutes for a flash_attn-class library, and the driver
JIT cache is invalidated by every driver upgrade. PTX is insurance, not a
delivery mechanism.

## Alternatives rejected

- **Mirror PyTorch (status quo).** Imports their deprecation calendar, not a
  constraint; three-source scrape; executed upstream bash in CI.
- **Key on (cuda, torch) by hand.** 21 rows, 3 values; drift and decisions
  indistinguishable; every torch release needs a human transcription.
- **Key on CUDA only, no clamp.** Ships cubins for device×torch pairs where
  torch itself cannot run — wasted hours and a false coverage claim.
- **Per-OS or per-Python lists.** No mechanism by which either axis affects
  device codegen.

## Consequences

- `_defaults.yml` combination rows lose their `arch_list:`; a five-row
  `arch_policy` table replaces them. `resolve_arch_list` gains the clamp;
  the live `build_cuda.sh` fetch/execute path is deleted.
- The Volta flicker becomes rational: same policy everywhere, trimmed
  per-combo by a recorded bound rather than by transcription.
- Dropping 5.0 is a **coverage regression** and ships as its own change,
  revertible independently of the keying change.
- One open check before trusting the cu124/126 row: flash_attn's sm_80
  cubin — a kernel requesting > 99 KB dynamic shared memory cannot launch
  on 8.6/8.9 despite same-major compat. Verify its smem caps.
