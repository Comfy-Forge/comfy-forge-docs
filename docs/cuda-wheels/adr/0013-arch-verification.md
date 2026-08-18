# CW-ADR-0013: The arch list is asserted, not assumed

**Status:** proposed

## Decision

> **Every built wheel's fatbin is inspected with `cuobjdump` before upload,
> and the extracted arch set must equal the requested one. A source lint
> catches FP8/arch-gated code in packages that don't declare the matching
> arch. Wheels record the arch list they were built with.**

## Context

Two independent reviews named the same worst failure mode in the farm: the
arch list is a wish nobody checks. `TORCH_CUDA_ARCH_LIST` is exported into
the build environment and nothing downstream verifies the produced binary —
`scripts/audit_wheel_archs.py` exists but is wired to no workflow, and
`packages/pointnet2_ops.yml` documents a package whose setup.py silently
overrode the variable and shipped; a human caught it by luck. The
user-visible symptom of this failure class is
`cudaErrorNoKernelImageForDevice` on someone's GPU, months later, with ~6800
published assets and no way to tell which were built with which list.

The audit script's `UNVERIFIED` state exists because it byte-scans for
`sm_XX` strings, and CUDA 12.8+ LZ4-compresses fatbin entries
(`-compress-mode=size`). Review measurements showed byte-scanning fails in
**both** directions: on a compressed 4-arch `.so` it found nothing; on an
uncompressed one it found 21 arches — including sm_35/37/50 that were never
targeted, from cudart's string tables.

## The mechanism

**1. Post-build assertion (in `build-wheel`, ~10 lines).** After the build,
before upload:

```
cuobjdump --list-elf <ext>.so    # SASS entries, decompressed transparently
cuobjdump --list-ptx <ext>.so    # PTX tail entries
```

Dedupe the `sm_`/`compute_` tokens (multi-TU builds list one entry per
translation unit) and diff against the requested list. Mismatch → job fails.
The toolchain and the wheel are both already present in the job; this costs
seconds. `nvdisasm` is the wrong tool here — it takes a bare cubin, not a
host `.so`. This closes CW-ADR-0009's pattern: the same place that asserts
vendored libraries now asserts arches.

**2. The FP8 / arch-gate lint (matrix generation time).** Fail any package
whose sources match `__CUDA_ARCH__ >= 890`, `e4m3`/`e5m2`, or explicit
`sm_89`/`sm_100a`/`sm_120a` targets without declaring the matching arch.
This converts the sageattention stub trap — Ada-gated code compiling to
empty stubs that host dispatch still selects at runtime, yielding
`cudaErrorLaunchFailure` on real hardware — from a runtime incident into a
config error. It is also what makes CW-ADR-0012's "8.9 is opt-in" safe: the
lint finds the packages that must opt in.

**3. Provenance.** Each wheel carries a `_build_info.json` (arch list,
`_defaults.yml` hash, run id); a committed ledger maps wheel filename →
the same. Today the question "which arch list was this 2024 wheel built
with?" is unanswerable, and the moment the policy changes, an audit that
compares binaries against *current* config reports every previously-correct
wheel as a mismatch. Provenance separates "config moved" from "wheel wrong".

A `build_epoch` integer in `_defaults.yml`, included in the exists-check,
makes a deliberate arch-policy change actually trigger rebuilds — today
`wheel_exists` matches on version alone, so an arch change rebuilds nothing
and old-arch wheels are served forever.

## Alternatives rejected

- **Trusting the build system.** pointnet2_ops already disproved it;
  setup.py files routinely mutate `TORCH_CUDA_ARCH_LIST`.
- **Byte-scanning harder.** Wrong in both directions (above); the compressed
  format is not an accident to work around but a format with a real reader.
- **Auditing after publication only.** The audit stays useful as a fleet
  sweep, but the assertion belongs before upload, where failing is cheap.

## Consequences

- A package that silently narrows or widens its arch list fails its build
  instead of shipping.
- `audit_wheel_archs.py` replaces its scan with `cuobjdump --list-elf` /
  `--list-ptx`; the `UNVERIFIED` state is deleted.
- The lint adds a per-package source scan to matrix generation; false
  positives (e.g. `e4m3` in a comment) are silenced by declaring the arch or
  an explicit `lint_ignore` key, both of which are visible in review.
- Provenance is backfillable for existing wheels only by inspection, and is
  then marked `inferred`, never presented as fact.
