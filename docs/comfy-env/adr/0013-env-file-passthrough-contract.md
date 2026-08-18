# ADR-0013: Env-file config -- honest passthrough with a compiler-owned deny-list

**Status:** accepted 2026-08, **implemented 2026-08**. Amends
[ADR-0003](0003-two-config-files-with-two-roles.md): resolves its
"allowlist-not-passthrough" defect. One accompanying change, the
`schema = 1` version key, was **reverted 2026-08** (see below) --
the passthrough contract itself is unaffected.

## Context

ADR-0003 promised that unknown `comfy-env.toml` keys "flow into the
generated `pixi.toml` verbatim." The v0.4 generator instead copies an
**allowlist** of six keys (`[dependencies]`, `[pypi-dependencies]`,
`[target.*]`, `[pypi-options]`, `[system-requirements]`,
`workspace.channels`) and silently drops everything else: `[tasks]`
vanishes, `[activation]` is dropped *and* overwritten by a hardcoded block,
and a typo'd `[dependancies]` evaporates -- the env materializes
successfully, empty of the author's packages, and fails days later as an
ImportError. The design promises open, behaves closed, and validates
nothing -- the one combination where every mistake is silent.

Two coherent alternatives existed:

- **Validated key schema**: comfy-env mirrors pixi's key surface and
  rejects what it does not know. Honest, but comfy-env then chases pixi's
  schema forever; lagging means either silent drops (today's bug) or false
  errors on legitimate new pixi keys, making comfy-env the bottleneck
  ADR-0002 promised it would never be.
- **Honest passthrough**: forward everything comfy-env does not own; let
  pixi -- pinned to the same version on author and host machines
  (ADR-0002) -- validate its own language.

## Decision

**Honest passthrough.** The generator forwards *every* passthrough table
into the generated manifest at the correct level (workspace-level vs
feature-level mapping), except for the compiler-owned keys below. The
guiding symmetry: **the root file is a closed schema because comfy-env
owns all of it; the env file is open passthrough because pixi owns most of
it.** Own what you own, forward what you don't.

### Compiler-owned keys -- the exact enumeration

**DENY -- hard error if the author sets them:**

| Key | Why the compiler owns it |
|-----|--------------------------|
| `workspace.platforms` | Host-derived: each machine generates its own manifest for its own platform (`toml_generator.py:437, 677`). An author pin would break every other platform's solve. |
| `workspace.name`, `workspace.version` | Identity: always `comfy-env-<env_name>`, matching the env directory. |
| `[environments]` | The per-env manifest is single-environment `default` with `no-default-feature = true` -- that shape *is* [ADR-0007](0007-machine-wide-workspace-with-per-env-manifests.md). |
| `[feature.*]` | Same shape constraint (one `node` feature per manifest); pixi additionally reserves the feature name `default`. |

**REWRITE -- normalized with a log line, never denied:**

- `torch` / `torchvision` / `torchaudio` entries inside `[dependencies]`
  or `[pypi-dependencies]`: `_strip_torch_family`
  (`toml_generator.py:218`) removes author pins and injects the host
  family pin -- parent and workers must share one torch ABI for tensor
  IPC ([ADR-0005](0005-tiered-tensor-serialization.md),
  [ADR-0007](0007-machine-wide-workspace-with-per-env-manifests.md)).
  Authors legitimately believe they need torch pins; rewriting with a
  clear log beats rejecting them.

**MERGE -- author entries and compiler entries coexist:**

- `[activation]` / `[activation.env]`: author keys pass through; the
  compiler's own `KMP_DUPLICATE_LIB_OK = "TRUE"`
  (`toml_generator.py:322`) wins only on direct collision. (Today the
  hardcoded block clobbers author activation entirely.) This merge is
  also the prerequisite for retiring comfy-env's `[env_vars]` in favor of
  pixi-native `[activation.env]` -- a separate future decision.

Those three buckets -- four denied keys, one rewritten family, one merged
table -- are the **entire** schema knowledge comfy-env retains about
pixi's language.

### Accompanying changes

- **Warnings for unrecognized keys inside comfy-env-owned sections**
  (`[cuda]`, `[options]`, `[settings]`, `[serializers]`): a typo'd
  `pakages` currently vanishes without a trace; owned sections are the one
  place pixi cannot validate for us.
- ~~**`schema = 1` version key** (absent means 1): one line now; the day
  the format's semantics change again, old and new files can coexist and
  the parser can dispatch migrations.~~ **Reverted 2026-08.** The key was
  never written by any config in the wild, and its real job was *forward*
  compatibility -- letting an old comfy-env refuse a future v2 file with
  "upgrade comfy-env" instead of misreading it. Under
  [ADR-0017](0017-pre-1-0-no-backward-compatibility.md) two schema
  versions are never live at once (comfy-env and its packs ship as one
  barrage), so there is nothing for the check to protect against yet, and
  a versioning hook with no consumer reads as dead code. It was also
  quietly broken: the closed root schema rejected `schema` as an
  "unsupported section", so `comfy-env-root.toml` -- the file whose
  semantics had *already* changed once -- could not carry the marker.
  Reintroduce it deliberately at the slow-rollout tripwire, when packs
  stop shipping in lockstep and the compat clock actually starts.
- **Provenance header** in generated manifests
  (`# generated from <config path> by comfy-env <version>`): when pixi
  rejects a forwarded-but-invalid key, its error names the generated file
  the author never wrote; the header leads the trail home.

## Consequences

- pixi's full manifest surface becomes genuinely reachable, and stays
  reachable as pixi grows, with zero comfy-env schema chasing -- the
  original ADR-0002/0003 promise, made true.
- Invalid keys now produce *pixi's* error at materialization instead of
  silence -- later than a parse-time error would be, and pointing at the
  generated file (mitigated by the provenance header). This is the
  accepted cost of not mirroring pixi's schema.
- The deny/rewrite/merge table above becomes contract: adding a key to it
  is a documented decision, not an implementation detail.
- comfy-env's typo protection is split by ownership: owned sections are
  warned on locally; everything else is pixi's job.
