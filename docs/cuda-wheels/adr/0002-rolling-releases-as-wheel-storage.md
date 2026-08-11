# CW-ADR-0002: Rolling per-package GitHub Releases as wheel storage

**Status:** accepted (defects acknowledged; direction agreed)

## Decision

**One rolling GitHub Release per package**, tagged `<pkg>-latest`, holding
every wheel for that package across all combos and versions as release
assets. CI uploads with `--clobber`; the matrix generator and each build
job check existing assets first (`gh release view`), so re-runs build only
what is missing -- idempotent, resumable CI at wheel granularity.

## Context

Thousands of wheels (3,400+ at last count) need free, CDN-backed storage
with programmatic upload from CI. PyPI is out (local version tags like
`+cu128torch2.9` are forbidden there); object storage costs money and adds
credentials.

## Consequences

- Free, unlimited-ish, CDN-backed, zero credentials beyond the repo token.
- Skip-existing keeps daily/incremental builds cheap (the property
  CW-ADR-0008 relies on).
- **Verified defects (2026-08 audit), with agreed direction:**
    - *Mutability*: `--clobber` replaces same-named assets but old-version
      wheels accumulate; combined with the consumer's first-match index
      scan, comfy-env can deterministically install an old version where
      two versions cover the same combo (live example found). Direction:
      delete superseded assets on publish, and publish a machine-readable
      `packages.json` manifest so consumers resolve by data, not HTML
      order.
    - *Version-blind skip*: the matrix-level existing-wheel check matches
      combo but not version -- bumping a package version rebuilds nothing
      without `--overwrite`. Direction: include version in the check.
    - *No hash pinning*: a mutable URL serving different bytes over time
      breaks lockfile reproducibility downstream. The manifest above fixes
      this too (per-file sha256).
