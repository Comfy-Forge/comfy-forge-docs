# CW-ADR-0008: Upstream torch watcher

**Status:** PROPOSED (design agreed 2026-08; not yet implemented)

## Context

The grid (CW-ADR-0005) is hand-edited and drifts: verified 2026-08-10,
upstream publishes torch **2.12.1 and 2.13.0** on cu126, cu129, and cu130
while the grid tops out at 2.11.0 everywhere -- six missing rows, two full
torch minors behind. Users' host torches move with upstream (a bootstrap
resolved 2.12.1+cu130 on a real machine), so every month of drift widens
the tier-1/tier-2 fallback gap in comfy-env's combo resolution
([comfy-env ADR-0004](../../comfy-env/adr/0004-prebuilt-cuda-wheel-index.md)).
The repo has zero watch automation; the two halves already exist
unconnected -- `fetch_torch_matrix.py` knows what upstream publishes,
`_defaults.yml` declares what we target. Nothing joins them.

## Decision (proposed)

A **daily scheduled workflow** (`torch-watch.yml`) driving a new
`scripts/torch_watch.py`:

1. **Detect**: reuse `fetch_torch_matrix.build_matrix()` (with fixes: add
   the missing `cu129` to its index list; filter dev/rc builds -- upstream
   indexes carry `2.13.0.devYYYYMMDD`; treat an empty per-cuda fetch as an
   error, not a removal signal). Diff upstream against the grid on
   (cuda, torch-minor), targeting the newest stable patch per minor.
2. **Update**: append grid rows with arch lists resolved live from
   PyTorch's `build_cuda.sh` (existing fetcher; a `None` result means the
   combo is not really supported -- skip and warn); pythons =
   upstream-published intersect the 3.10-3.14 policy; record phantom cells
   for (python, platform) gaps automatically (CW-ADR-0007's data file);
   append matching rows to `sageattn3.yml`'s hand-maintained list.
3. **Deliver**: auto-commit to main (grid rows are derived from upstream
   facts and validated by the arch-list fetch) and dispatch
   `build.yml -f package=all -f cuda=<X.Y> -f pytorch=<X.Y.Z>` once per new
   combo -- single-valued workflow inputs make per-combo dispatches the
   granularity; skip-existing (CW-ADR-0002) keeps them incremental; one
   combo stays under the 256-job matrix cap.
4. **Human gate only where humans are required**: a NEW CUDA index (e.g. a
   future cu131) files an issue instead of auto-building -- it needs
   workflow choice-list edits, a Windows CUDA installer URL in
   `setup-cuda`, and per-package `arch_list_by_cuda` review.

## Consequences (anticipated)

- Grid drift drops from months to <24h for new torch minors; phantom
  curation becomes automatic; `sageattn3.yml` stops silently detaching.
- A new torch minor triggers a large one-time build wave (~38 packages x
  pythons x 2 platforms per combo, minus phantoms/min_pytorch); hosted-CI
  hours are the price of currency, and packages like mmcv are expected to
  fail as "survey" runs on bleeding-edge torch.
- Consumer-side reminder emitted in the watch summary: comfy-env's
  `TORCH_FAMILY_COMPAT` table needs the new minor's torchvision/torchaudio
  pairings (separate repo, manual today).
