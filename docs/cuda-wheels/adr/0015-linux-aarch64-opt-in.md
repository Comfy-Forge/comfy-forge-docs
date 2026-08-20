# CW-ADR-0015: linux_aarch64 as an opt-in platform

**Status:** accepted; piloted end-to-end on cc_torch (112/112 cells green)

## Decision

> **aarch64 is a third platform value, opted into per package** via
> `build_matrix.platforms: [linux, windows, linux_aarch64]` -- the global
> default stays x86+Windows, so no package grows ARM cells by accident.
> ARM gets **its own arch policy table** (`arch_policy_aarch64`), because
> server ARM shares no hardware history with x86: no pre-Ampere parts,
> Grace pairs with Hopper, and 13.x adds Thor (sm_110) as a *native*
> target. A CUDA line absent from that table is deliberately not built
> for ARM.

## Mechanism

- Builds run on GitHub's hosted `ubuntu-24.04-arm` runners (free for
  public repos); the toolkit installs from NVIDIA's `ubuntu2404/sbsa` apt
  repo (the Linux path derives package names from the version; only the
  repo URL is arch-switched).
- `_defaults.yml` gains **no rows**: the same cell grid multiplies against
  the extra platform, and upstream's ragged ARM coverage (cu126 skipped
  torch 2.7/2.8 on ARM entirely) becomes generated phantom cells -- the
  same CW-ADR-0007 mechanism that masks cu129's missing Windows.
- auditwheel repairs to `manylinux_2_39_aarch64` -- the ARM runner's
  honest glibc floor. **Known consequence:** that excludes Ubuntu
  22.04-based Grace hosts and JetPack 6; containerised 2_28 builds are
  future work if anyone asks.
- Per-package x86 arch overrides are deliberately ignored on ARM
  (sm_86/sm_89 floors mean nothing on SBSA); an ARM-specific override
  field can be added when a package needs one.

## Constraints discovered by the pilot

- **cu124 is unbuildable on ARM**: the ubuntu2404/sbsa repo starts at
  CUDA 12.5. Absent from the ARM policy table, documented, six cells
  dropped -- the audience (torch 2.4/2.5 on Grace) is negligible.
- Recent ARM torch wheels are `+cuXXX`-tagged like x86, so the standard
  torch pin works; the untagged-mirror quirk applies only to older lines.
- Mixed matching hazard fixed in `wheel_exists`: x86 "linux" matching now
  excludes aarch64 wheel names, and ARM cells match only aarch64 wheels.
