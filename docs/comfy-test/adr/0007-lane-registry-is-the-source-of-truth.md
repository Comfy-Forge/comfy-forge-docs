# ADR-0007: The lane registry is the source of truth

**Status:** accepted (2026-08)

## Decision

> **A lane is an (os x accelerator x install method) target described by exactly five
> stored facts -- `id`, `os`, `backend`, `install_method`, `label` -- and everything
> else is computed** (`lanes/registry.py`). Config tokens, the
> `TestConfig` field lookup, the results-gallery entries and the CI job
> matrix all derive from that one table.

There is no `"gpu"` backend: accelerators are named concretely (`cpu`,
`cuda`, `rocm`).

## Context

The same lane list was previously restated in four places: the valid
config tokens, the dataclass fields, the HTML gallery, and the GitHub
matrix YAML. Four copies of one fact drift, and the drift is silent -- a
lane present in the matrix but absent from the gallery produces results
that render nowhere, while the reverse produces an empty cell that looks
like a failure.

The design rule is stated in the module docstring: *store only irreducible
facts; compute everything derivable*. `config_key`, the runner image, the
display order and the aliases are all properties, so adding a lane is
one row.

## Alternatives rejected

- **Hand-maintained matrices in each consumer.** The status quo that caused
  the drift.
- **Generating the workflow YAML from the registry** (`comfy-test lanes
  --matrix-json`, named in the docstring). Rejected *for now*: generated CI
  YAML is hard to review in a PR diff and the generator becomes a release
  dependency. Instead the YAML is hand-written and **guarded** --
  `tests/test_lanes_matrix_yaml.py` fails when it diverges from the
  registry. The generator remains the eventual direction.
- **Storing derived fields for convenience** (e.g. a `runner` column).
  Rejected: every stored derivation is a drift opportunity.

## Consequences

- Adding a lane is a one-row change plus a runner; forgetting the YAML
  is caught by a test rather than by a confusing dashboard.
- The registry is a compatibility surface: renaming an `id` breaks existing
  `comfy-test.toml` files, so aliases exist and are resolved centrally.
- `rocm` is reserved in the taxonomy with no runner wired -- the vocabulary
  is allowed to lead the infrastructure, so long as selecting it fails
  loudly ([ADR-0008](0008-lanes-are-opt-in.md)).
- Because `install_method` is a first-class axis (`manual` / `portable` /
  `desktop` -- ComfyUI's own three), fundamentally different install mechanisms
  ([ADR-0013](0013-desktop-is-driven-over-cdp.md)) coexist without special
  cases in the config layer.
