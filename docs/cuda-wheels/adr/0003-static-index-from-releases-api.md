# CW-ADR-0003: PEP 503 static index generated from the Releases API

**Status:** accepted (defects acknowledged; direction agreed)

## Decision

> **A static PEP 503 index on Pages, generated from the Releases API.**
> Not an index server (cost and uptime); the API is the source of truth,
> the index a pure projection of it, and the API itself doubles as the
> consumer's different-routing-edge fallback.

`scripts/generate_index.py` reads the **GitHub Releases API as the source
of truth** and emits a static PEP 503 simple index to `docs/`, deployed to
GitHub Pages (orphan-branch deploy). Two parallel trees: **v2** (real
filenames, dotted torch tag: `+cu128torch2.9`) and a **v1 compat shim**
(dot-stripped display names, real asset hrefs) kept alive for legacy
comfy-env versions. Every index entry links directly to the release-asset
download URL. The consumer keeps the Releases API itself as a
different-routing-edge fallback when the Pages CDN is blocked.

## Context

Consumers (comfy-env's resolver, plain pip users) need an installable index
over the wheels stored in Releases (CW-ADR-0002). Running an index server
costs money and uptime; a static site does not.

## Consequences

- Zero-cost, CDN-backed, cacheable; the dashboard and matrix pages ride the
  same deploy.
- Regenerating is idempotent: the Releases API is authoritative, the index
  is a pure projection of it.
- **Verified defects (2026-08 audit), with agreed direction:**
    - *Unpaginated API call*: `get_releases()` fetches one page (30
      releases); at release #31 whole packages silently vanish from the
      index, and `force_orphan` makes the loss stick. A dated time bomb --
      27 packages published at audit time. Direction: paginate
      (`per_page=100` + Link-follow) and fail the deploy if the generated
      index lost packages versus the previous one.
    - *Release notes advertise the v1 root* with an unpinned
      `pip install --extra-index-url` -- v1's dot-stripped anchor text
      violates PEP 503's filename rule and misleads standards resolvers.
      Direction: notes should name `/v2/` with an exact `pkg==V+combo` pin;
      v1 gets a sunset once legacy comfy-env versions age out.
- A machine-readable `packages.json` manifest alongside the index
  (CW-ADR-0002 direction) will eventually demote HTML scraping to a
  fallback for both this project's consumers.
