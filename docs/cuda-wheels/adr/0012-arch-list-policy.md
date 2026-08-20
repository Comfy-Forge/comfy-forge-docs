# CW-ADR-0012: Arch lists are a per-CUDA policy, clamped by torch runnability

**Status:** accepted (supersedes the arch-list half of [CW-ADR-0005](0005-shared-grid-and-arch-list-policy.md))

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
  + PTX twice: on the highest arch overall (real codegen for future
    majors), and on the highest arch below any major gap (the
    compatibility net that catches devices we ship no cubin for)
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

**Implemented mechanism** (amended after implementation; an earlier draft
described a parsed-snapshot pipeline that was never built): the owned policy
lives in the arch policy file (`defaults/arch_policy.yml` in the Comfy-Forge
line; `packages/_arch_policy.yml` in the legacy layout) — per-CUDA rows plus a hand-maintained
`arch_exceptions` map encoding the torch clamp (e.g. no sm_70 row on
cu128/torch2.7, where torch ships none). `generate_matrix.py` reads that
file **directly at build time**; `_defaults.yml` carries cell axes only.
Arch changes go through this ADR process by the owner's explicit decision,
never through automation. The live `build_cuda.sh` fetch/execute fallback is
severed from the build path. Build output is a function of the git SHA;
there is no network fetch at build time.

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
2. **A major with no cubin and no PTX at or below it is a dead device** — it
   does not "fall back", it gets `cudaErrorNoKernelImageForDevice` at first
   kernel launch (lazy loading defers it past import). Exclusions are
   therefore computed and printed (on the P.A.M page), never implied.
3. **The same-major rule does not apply to arch-conditional targets.** An
   `sm_100a` cubin runs on CC 10.0 exactly — not 10.3; family-conditional
   `10.0f` (CUDA 12.9+) restores forward-compat within the major. So "10.3
   is covered by 10.0" holds for the *policy rows* (plain targets) but NOT
   for packages compiling `a`-suffixed CUTLASS kernels (e.g. sageattn3's
   FP4): those must declare every CC they serve, or use `f` targets. The
   coverage computation treats `a` entries as single points, not ranges.

## What PyTorch itself builds (the input)

Scraped from PyTorch's own build tables at every release tag it publishes
per CUDA index (Linux; snapshot 2026-08, refreshed by the P.A.M workflow):

| CUDA | PyTorch SASS (union over torch releases) | PTX | Varies across torch? |
|---|---|---|---|
| cu124 | 50,60,70,75,80,86,90 | none | no |
| cu126 | 50,60,70,75,80,86,90 | none | no |
| cu128 | 70,75,80,86,90,100,120 | 120 (2.7 only) | **sm_70 absent on torch 2.7 and 2.11** |
| cu129 | 70,75,80,86,90,100,120 | 120 (≤ 2.11) | **sm_70 absent on torch 2.11+** |
| cu130 | 75,80,86,90,100,120 | 120 (≤ 2.10) | no |
| cu132 | 75,80,86,90,100,120 | none | no |

(Windows differs once: cu126 adds 6.1 — a Pascal-consumer perf tune, covered
by the 6.0 cubin same-major, so not imported.)

Three facts this table settles:

- **cu124/cu126 torch ships no PTX at all** — torch itself cannot run on
  anything newer than sm_9x there. That is the clamp's ceiling, observed.
- **The only arch that varies across torch versions anywhere is sm_70** on
  cu128/cu129 — the entirety of PyTorch's per-torch "signal" is one Volta
  support decision, which the clamp reproduces without hand-copying 21 rows.
- **PyTorch has rotated PTX off its recent releases entirely** (cu129
  torch 2.12, cu130 torch 2.11+, cu132: SASS-only). Our +PTX rule is now a
  strict superset of upstream forward-compat, not a tweak to it.

## The policy rows (the decision)

Derivation rule, applied per CUDA: **start from PyTorch's union, drop
arches with no meaningful population for this farm's audience, never add a
SASS arch PyTorch's torch cannot execute on, and re-add PTX per major
family.**

| CUDA | PyTorch union | Our policy | Delta and why |
|---|---|---|---|
| cu124, cu126 | 50,60,70,75,80,86,90 | `6.0;7.0;7.5;8.0;8.6;9.0+PTX` | **−5.0** (Maxwell: below Steam's 0.1% cutoff, no bf16, 4 GB VRAM — decided, not open); **+PTX on 9.0** (upstream ships none; costs ~8 KB) |
| cu128, cu129 | 70,75,80,86,90,100,120 | `7.0;7.5;8.0;8.6;9.0+PTX;10.0;12.0+PTX` | identical SASS set; the clamp trims 7.0 exactly where the paired torch lacks it (2.7, 2.11+), so we never ship a Volta cubin torch cannot host; **PTX per major** instead of 120-only |
| cu130, cu132 | 75,80,86,90,100,120 | `7.5;8.0;8.6;9.0+PTX;10.0;12.0+PTX` | identical SASS set; PTX per major (upstream: none or 120-only) |

Coverage is three-tier, and the P.A.M page prints each combo's tiers:

- **Native** (cubin, same-major): 6.1, 8.7, 8.8, **8.9**, 10.3, 12.1 ride
  the listed cubins — deliberately not separate slots.
- **JIT-only** (caught by a PTX net, minutes-long first import, driver-cache
  fragile): **sm_110 (Thor)** rides `9.0+PTX` on cu128+. Functional, not
  supported; nobody should ship a Thor product on this farm's wheels.
- **Dead**: Maxwell after the 5.0 drop (no cubin, no PTX at or below 5.x).

Why two PTX entries and not one: the JIT loads the highest PTX ≤ the device,
and compute_90 PTX cannot express Blackwell ISA. `12.0+PTX` is the *quality*
path for future majors; `9.0+PTX` is the *compatibility* net for the sm_11x
gap. On cu124/126 a single `9.0+PTX` plays both roles.

**Why 7.0 stays while 5.0 goes, under the same population criterion:** the
population is not the same. Maxwell is 4 GB desktop cards that cannot run
current workflows at all. Volta is V100 16/32 GB — near-zero on Steam but
the cheapest usable VRAM on rental clouds (vast.ai / RunPod), where ComfyUI
demonstrably runs. One is dead hardware; the other is a live niche costing
one slot on two CUDA lines that PyTorch itself still builds.

A new CUDA index gets its row by running the same derivation against the
refreshed snapshot — a reviewed PR, not an automatic commit.

**8.9 (Ada) stays opt-in.** The review compiled fp16/bf16 attention kernels
for sm_86 and sm_89: byte-identical SASS. Scope honestly stated: that is one
representative kernel, and a library that selects tile sizes or smem budgets
via `__CUDA_ARCH__` gets Ampere-tuned configs on Ada even with no FP8 — a
performance delta the measurement cannot see. Such packages are exactly the
ones that should declare 8.9, which is the opt-in working as designed. The
five FP8 packages (natten, nunchaku, sageattention, torchao,
natten_sequential) already declare it. (Population figures here and above
are Steam-survey based — no ComfyUI hardware census exists; the VRAM-floor
argument is the sturdier leg.) The associated hazard — source gated
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

- `_defaults.yml` combination rows lose their `arch_list:` (done); the
  per-CUDA `arch_policy` table plus `arch_exceptions` replaces them (done);
  the live `build_cuda.sh` fetch is severed from the build path (done — the
  fetcher file survives only for the torch watcher).
- The Volta flicker becomes rational: same policy everywhere, trimmed
  per-combo by a recorded bound rather than by transcription.
- Dropping 5.0 is a **coverage regression**, decided here; it ships as its
  own commit so it stays revertible independently of the keying change.
- One open check before trusting the cu124/126 row: flash_attn's sm_80
  cubin — a kernel requesting > 99 KB dynamic shared memory cannot launch
  on 8.6/8.9 despite same-major compat. Verify its smem caps.
