# How a cell gets its arch list

*Last verified against `cuda-wheels-forge` @ `17a3d32` (2026-08-26).*

Every build cell exports one environment variable that decides which GPUs the
resulting wheel can run on:

```
TORCH_CUDA_ARCH_LIST="8.0 8.6 8.9 9.0+PTX 10.0 12.0+PTX"
```

This page is the whole story of where that string comes from, because almost
every coverage bug the farm has shipped was a bug in this string — and the
nastiest property of an arch bug is that **it does not fail the build**. A wheel
built for the wrong GPUs compiles, links, imports, and uploads. The user finds
out.

---

## The one-paragraph version

There is a **policy file** with one arch list per CUDA version. A package may
**override** it. x86 and ARM are resolved by **two different functions reading
two different tables**. Whatever comes out is then forced to carry a `+PTX`
tail, normalised to spaces, and handed to the compiler. After the build, the
verify gate re-derives the *same* list and checks the wheel against it.

That last sentence is the trap the rest of this page keeps returning to: the
gate compares the wheel to the list the package **asked for**, not to the list
of GPUs that exist. Narrow the ask and the gate goes quiet.

---

## Step 1 — the policy row

`defaults/arch_policy.yml` holds three tables:

| table | keyed by | used for |
|---|---|---|
| `arch_policy` | CUDA version | Linux x86-64 and Windows |
| `arch_exceptions` | `cuda/torch-minor` | narrow, hand-maintained additions |
| `arch_policy_aarch64` | CUDA version | Linux aarch64 |

`policy_arch_list()` (`scripts/generate_matrix.py:93`) picks one:

- If the platform is `linux_aarch64`, it reads `arch_policy_aarch64[cuda]` and
  **returns immediately**. It never looks at `arch_exceptions`.
- Otherwise it tries `arch_exceptions["<cuda>/<torch minor>"]` first, then falls
  back to `arch_policy[cuda]`.
- A missing CUDA key is a **hard error**, deliberately. Silence here is how the
  farm used to guess by mirroring PyTorch.

!!! warning "The exceptions table adds, it does not subtract"
    `arch_exceptions` was **inverted on 2026-08-22**. It used to record archs
    being *dropped*; it now records the closed set of old torch minors that
    still shipped `sm_70`, so a Volta cubin stays reachable inside those torch
    processes. The policy rows carry the *current* floor. New torch minors
    inherit the policy row and this table never grows again.

    If you read it as a drop-list you will reach exactly the wrong conclusion
    about what a cell builds.

### Why a policy at all, instead of mirroring torch

Mirroring upstream was the previous approach, and it was abandoned. The farm's
arch list is its **own** policy. Two reasons:

1. Torch's own arch list is chosen for torch's binary size budget, not for
   which GPUs people own. It rotates `+PTX` off a maturing toolchain, which
   would silently remove the farm's forward-compat tail.
2. Half these packages have their own floors (natten is sm_80+, spconv drags
   cumm's `supported_arches` table behind it). A mirror cannot express that.

---

## Step 2 — the package override

`packages/<pkg>/arch_override.yml` has **four** fields:

| field | lane | shape |
|---|---|---|
| `arch_list` | x86 / Windows | one string, all CUDA versions |
| `arch_list_by_cuda` | x86 / Windows | map, CUDA version → string |
| `arch_list_aarch64` | ARM | one string |
| `arch_list_by_cuda_aarch64` | ARM | map, CUDA version → string |

### x86 / Windows priority

`resolve_arch_list()` (`generate_matrix.py:207`), highest wins:

1. a per-combo `arch_list` inside the package's own `build_matrix.combinations`
2. `arch_list_by_cuda[cuda]`
3. `arch_list`
4. the policy row

Nothing resolved → `KeyError`, not a default.

### ARM priority — and the trap

`resolve_aarch64_arch_list()` (`generate_matrix.py:157`) is a **separate
function**:

1. `arch_list_by_cuda_aarch64[cuda]`
2. `arch_list_aarch64`
3. `arch_policy_aarch64[cuda]`

!!! danger "The x86 fields are deliberately invisible to the ARM lane"
    A package with a carefully reasoned `arch_list_by_cuda` gets **none of it**
    on ARM. That is intentional — an `sm_86`/`sm_89` floor describes consumer
    x86 GPUs and means nothing on SBSA — but it means:

    - ARM is a **default platform** on every package, so this path runs for all
      of them, not for a pilot subset.
    - A package whose gap is genuinely platform-independent (upstream ships no
      `sm_100` kernel *at all*) must say so **twice**, in the x86 field and in
      the aarch64 field.
    - Fixing a coverage bug on x86 does not fix it on ARM, and vice versa. Ask
      "which of the four fields did I just edit?" every single time.

---

## Step 3 — the two normalisations

Both resolvers end with the same two calls, in this order.

### `_ensure_ptx_on_highest_base()` — the forward-compat tail

Finds the highest non-`a` token and appends `+PTX` if it is missing. Idempotent.

The **reason** is GPUs that do not exist yet. A cubin runs only on the arch it
was built for; PTX is intermediate code the driver JITs at load time onto a
*newer* arch. Without a PTX tail, a wheel is dead the day a new GPU ships.

Two things this is *not*:

!!! warning "PTX JITs forward only — measured"
    `compute_120` PTX on an `sm_86` device gives
    `cudaErrorNoKernelImageForDevice`. A PTX tail is not a substitute for the
    cubins below it, only insurance above them.

!!! warning "`9.0+PTX` can silently become arch-conditional PTX"
    Tokens ending in `a` (`sm_90a`, `sm_100a`) enable arch-specific features
    and **load only on that exact arch**. A `compute_90a` PTX tail is dead
    bytes and a false forward-compat promise. `_ensure_ptx_on_highest_base`
    skips `a` tokens when choosing where to put the tail, but a package that
    compiles `a` targets (sageattention `compute_90a`, natten `90a-real` /
    `100a-real`, torchao `_C_cutlass_90a`, sageattn3 `sm_100a`/`120a`) should
    **drop `+PTX` from its override** rather than ship one.

    flash_attn is the counter-example and the reason the distinction matters:
    `csrc/flash_attn` is one CUTLASS-2.x kernel family with zero
    `__CUDA_ARCH__ <` guards, its `.target sm_120` carries no suffix, so its
    PTX genuinely is portable. Verified by compiling it: 32 entries, 15360
    `mma.sync`, byte-identical entry names to `compute_80`.

    Cost, measured: +1.4% compile time, **+25% wheel size** (244MB → 304MB) —
    nvcc stores PTX pre-compressed, so it barely compresses in the zip while
    cubins compress ~3.9:1.

### `_normalize_arch_list()` — spaces, not semicolons

Torch documents `TORCH_CUDA_ARCH_LIST` as space-separated. But
`arch_policy.yml` writes `;`-separated rows while every `arch_override.yml`
writes space-separated ones — so **which separator a build saw depended on
whether the package happened to have an override**.

That asymmetry shipped a real bug: flash_attn's ARM lane has no aarch64
override, so it got the policy form, its `setup.py` parsed it with a plain
`.split()`, got **one token**, and emitted `arch=compute_80;90` — which nvcc
rejected outright (2026-08-26). Before a later patch made that fatal, the same
bug silently built for nvcc's default arch only.

Fixed centrally because the inconsistency is the farm's, not the packages'.

---

## Step 4 — the package's `setup.py` gets a vote you did not grant it

This is the failure mode that costs the most CI hours, because the farm's side
is *correct* and the wheel is still wrong.

`TORCH_CUDA_ARCH_LIST` is a **request**. What a package does with it is entirely
up to the package.

!!! danger "flash_attn: a hardcoded if-ladder"
    Upstream `setup.py:179-191` gates every gencode on an exact token test:

    ```python
    if "80"  in cuda_archs(): ... arch=compute_80,code=sm_80
    if "90"  in cuda_archs(): ...
    if "100" in cuda_archs(): ...
    if "120" in cuda_archs(): ...
    ```

    Any arch outside that set matches no branch and is **silently dropped** —
    no cubin, no PTX, no diagnostic. `arch_policy_aarch64`'s 13.x row asks for
    `11.0` (Thor), so the cu13.0 ARM wheel shipped without `sm_110` and only
    C7 caught it, three hours in. The fix is a `cuda_extra_archs()` helper that
    emits `code=sm_X` for whatever the ladder does not handle.

!!! danger "sageattention: a capability whitelist that `continue`s"
    Its `setup.py` walks the requested arches, checks each against its own
    whitelist, and silently `continue`s on anything it does not recognise. The
    farm can ask for `sm_100` all day; the wheel will not contain it.

!!! danger "sageattn3: cubins with no PTX"
    Ships `sm_100a`/`sm_120a` cubins and no PTX at all — dead on everything
    below Blackwell *and* on everything above it.

**How to tell in advance:** read the package's `setup.py` for the string
`TORCH_CUDA_ARCH_LIST` and for `-gencode`. If the arch list is consumed by
`torch.utils.cpp_extension` you are fine. If the package parses it itself, read
that parser.

---

## Step 5 — the gate, and why it is not the safety net you think

`scripts/verify_wheel.py` check **C7 `arch_sass`** runs `cuobjdump` over every
extension in the built wheel and compares against the cell's arch list.

It does four things, in order:

1. **Family check** — a missing arch *family* (`sm_1x` entirely absent) fails.
2. **Exact archs** — `expected - actual - waived`. This used to be a warning,
   which meant you could drop every consumer Ampere and Ada cubin and still
   exit 0 as long as one `sm_80` survived, because the family check compares
   majors. Promoted to `fail`.
3. **PTX** — `expected_ptx - ptx - waived`. The `+PTX` marker used to be
   discarded before comparison, so a wheel that declared forward-compat and
   shipped none looked identical to one that shipped it.
4. Suffix folding — `sm_90a` and `sm_100f` normalise to `sm_90`/`sm_100`
   before comparison, or every Hopper wheel reads as "missing sm_90".

### Two ways this gate is quiet when it should not be

!!! danger "It compares against the *resolved* list"
    C7 re-derives the arch list from the same YAML the build used. Narrowing an
    override to match a coverage gap moves **both sides of the comparison** and
    converts a real gap into a green check **with no residual signal anywhere**.
    This is the mechanical reason arch-narrowing is banned as a fix.

!!! danger "A wheel with no device code at all only warns"
    ```python
    if not sass and not ptx:
        rep.add("arch_sass", "warn", "UNVERIFIED: no SASS/PTX visible ...")
        return
    ```
    Zero SASS **and** zero PTX returns early as a **warning**, before any of the
    four checks above run. C3 `binary_census` is satisfied by the `.so` merely
    existing, and C8 `import` succeeds because an empty extension still imports —
    kernels only fail at launch. So a wheel containing no GPU code can pass the
    whole gate. This is a known hole, not a design decision; the proposed fix is
    to make `UNVERIFIED` fail unless the package sets `allow_pure_python`.

### The waiver

`verify.allow_missing_archs` is the only sanctioned way to accept a gap, and it
is for **documented upstream absences only** — upstream ships no kernel for that
arch, full stop. It is not for "the build was slow" or "it did not compile".

---

## The standing rule, and what to do instead

**Never narrow an arch list to make a build pass.**

The rule exists because of the C7 property above: narrowing is the one class of
"fix" that removes both the problem and the evidence. It has bitten twice —
spconv lost its Blackwell cubins under a comment asserting the trim changed
nothing about the shipped wheel, and a later edit to the same file dropped Ada
and nobody noticed for two days.

When an arch genuinely will not compile, the alternatives that preserve
coverage:

| symptom | real fix |
|---|---|
| `cg::labeled_partition` unsupported | `__CUDA_ARCH__ >= 700` guard with a fallback (`patch_lib.guard_labeled_partition`) |
| `atomicAdd(double*)` unsupported below sm_60 | the documented `atomicCAS` shim |
| a vendored dep raised its floor | pin the dep below the bump |
| upstream's arch table lacks the token | patch the table (`cumm`'s `supported_arches`) or emit the gencode yourself (flash_attn's `cuda_extra_archs`) |
| MSVC cannot parse an arch's templates | strip that arch **on Windows only**, in the patch, and say so in `sharding_platforms`/the patch comment |
| upstream genuinely has no kernel | `verify.allow_missing_archs` with the evidence in the comment |

---

## Reading a real arch list

```
8.0 8.6 8.9 9.0 10.0 12.0+PTX
```

| token | GPU |
|---|---|
| `7.5` | Turing (RTX 20xx, T4) |
| `8.0` | Ampere DC (A100) |
| `8.6` | Ampere consumer (RTX 30xx) |
| `8.9` | Ada (RTX 40xx, L40S) |
| `9.0` | Hopper (H100) |
| `10.0` | Blackwell DC (B200) |
| `11.0` | Thor (SBSA only — this is why the ARM table has a row x86 does not) |
| `12.0` | Blackwell consumer (RTX 50xx) |

`+PTX` on the last token = JIT path onto whatever comes next. A **major**
version bump breaks binary compatibility: `compute_100` PTX cannot JIT onto
`sm_110`, and `compute_120` PTX cannot either. That is why `11.0` needs its own
cubin and cannot be covered by its neighbours.

---

## Checklist before you touch an arch list

1. Which lane? x86, Windows and ARM read **different fields**.
2. Are you widening or narrowing? Narrowing needs a reason that is not "the
   build failed".
3. Does the package's `setup.py` actually honour `TORCH_CUDA_ARCH_LIST`?
4. Will `_ensure_ptx_on_highest_base` put a `+PTX` tail on an `a` token?
5. After the build, read C7's `data` block — `expected` / `sass` / `ptx` /
   `source`. `source: none` means nothing was measured. See
   [Step 5 -- the gate](#step-5-the-gate-and-why-it-is-not-the-safety-net-you-think).
