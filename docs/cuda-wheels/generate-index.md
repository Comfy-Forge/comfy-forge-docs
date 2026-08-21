# generate_index.py

The publisher: it turns the farm's GitHub releases into the static, PEP 503
package index that pip/uv/comfy-env resolve against. It runs only inside
`update-index.yml` at deploy time — the site is built into `_site/` and
force-pushed to `gh-pages` as a single orphan commit; none of it lives on
`main` (see [the repo breakdown](build-process.md#where-do-the-files-live)).

## What it does

1. **Enumerate wheels** from every `<package>-latest` rolling release via the
   GitHub API. Wheels stay on Releases (CW-ADR-0002); the index holds only
   anchor tags whose hrefs point at the release assets (CW-ADR-0003).
2. **Emit the PEP 503 tree**: `/whl/<package>/index.html` pages listing every
   wheel, in both v2 (`+cu128torch2.8`) and v1 (`+cu128torch28`) naming so
   older resolvers keep working.
3. **Expand torch-free aliases.** Packages declaring `links_torch: false`
   build one wheel per (cuda, python, platform) under a single torch tag.
   The index lists that same asset under **every** torch of its CUDA line —
   the alias set is derived from the shared grid, so widening the grid widens
   the aliases automatically. Same href, different anchor text; one upload.
   Two documented limits: the Releases-API fallback path only finds the real
   filename, and pip installs a dist whose local tag names the build-env
   torch (`pip freeze` disagrees with the environment). The durable fix is a
   torch-less local tag (CW-ADR-0011, not yet implemented).
4. **Refuse to shrink.** With `--previous <dir>` (update-index.yml checks out
   the LIVE gh-pages branch for this), a newly generated index that lists
   markedly fewer wheels than the deployed one aborts the deploy — an API
   hiccup must not publish an empty index over a full one.

```bash
python scripts/generate_index.py --out _site --previous previous-site
```

## Deploy path

```text
release changes ──> update-index.yml ──> generate_index.py + dashboard +
matrix page ──> _site/ ──> gh-pages (force_orphan: single commit, no history)
```

`torch-matrix.yml` never deploys; it only refreshes the committed PCWM
snapshot on `main`, and that push triggers `update-index.yml` — one workflow
owns gh-pages, so deploys cannot race.
