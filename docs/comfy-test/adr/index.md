# Architecture decision records

Nygard-style records (Status / Context / Decision / Consequences) for
[comfy-test](https://github.com/PozzettiAndrea/comfy-test). Numbers are
grouped by subsystem, not by date; most decisions were made implicitly
during development and are recorded here retroactively as of v0.4.7
(August 2026).

Where a status line cites "adversarial review": these are structured LLM
review panels (independently-briefed reviewer personas that investigate the
code separately, then argue with each other's findings), run by the
maintainer in August 2026. Their claims were verified against the working
tree before being incorporated -- and several were **wrong** and are
recorded as such. Treat the citations as "the argument survived
cross-examination", not as external human endorsement.

Every record here names what it **rejected**. A decision without a rejected
alternative is a feature description; those live in the reference pages, not
in this directory.

| ADR | Decision | One-liner |
|-----|----------|-----------|
| [0001](0001-real-installs-are-the-unit-of-test.md) | Real installs are the unit of test | Real venv, real ComfyUI, real server -- never a mocked `comfy` import. |
| [0002](0002-levels-are-an-ordered-pipeline.md) | Levels are an ordered pipeline | Execution order *is* the enum; dependencies are static; every check claims a slot. |
| [0003](0003-two-install-paths-attach-and-fresh.md) | Two install paths: attach and fresh | Hosted lanes attach to a prebuilt env; a green cell there does not mean "installs clean". |
| [0004](0004-mocking-is-earned-by-probing.md) | Mocking is earned by probing | CUDA packages are mocked only after probing the materialized env, never on a flag. |
| [0005](0005-pinned-torch-random-python.md) | Pinned torch, random Python | A hand-maintained triple beats resolver skew; the interpreter is sampled, not matrixed. |
| [0006](0006-config-is-a-hard-fail-allowlist.md) | Config is a hard-fail allowlist | An unknown key aborts the run, because a typo once produced a plausible lie. |
| [0007](0007-lane-registry-is-the-source-of-truth.md) | The lane registry is the source of truth | Five irreducible facts per lane; matrices are guarded against drift, not hand-written. |
| [0008](0008-lanes-are-opt-in.md) | Lanes are an opt-in allowlist | Listing lanes is explicit; per-lane booleans are a hard error. |
| [0009](0009-a-helper-pack-is-injected.md) | A helper pack is injected into every env | Validation needs an endpoint ComfyUI does not ship; the cost is a supply-chain fact. |
| [0010](0010-capture-drives-a-real-browser.md) | Capture drives a real browser | Screenshots come from the real frontend, because that is where the bugs are. |
| [0011](0011-execution-light-is-a-level.md) | `execution_light` is a level, not a fallback | A silent downgrade would make two green cells mean different things. |
| [0012](0012-level-flag-swaps-terminals.md) | `--level` swaps terminal levels | Passing a terminal level replaces the others instead of truncating the ladder. |
| [0013](0013-desktop-is-driven-over-cdp.md) | Desktop is driven over CDP, installed by git clone | The Electron app is tested as an app; Manager could not install the packs. |
| [0014](0014-javascript-isolation-is-static.md) | Frontend isolation is enforced statically | AST facts are errors, heuristics are warnings, `.mjs` is exempt by construction. |
| [0015](0015-publish-is-a-separate-job.md) | Publish is a separate job | Results are an artifact; the dashboard is the consumer's own gh-pages. |
| [0016](0016-run-output-is-namespaced-run-branch-lane.md) | The branch level is never dropped | Output is always `run/branch/lane`; an omitted `--branch` defaults to the detected git branch, not a missing level. |
