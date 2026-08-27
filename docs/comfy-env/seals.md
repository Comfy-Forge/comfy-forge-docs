# The three seals

*After all comfy-env.toml configs are discovered, three artifacts answer three different questions, at three different
moments: "did the inputs to the env definition change?", "did the env definition change?", and "is this
env safe for the runtime that is about to bind to it?".

`install()` wants to do **no work when nothing changed** -- but "did anything
change?" is three different questions, asked at three different prices:

1. **Fast key -- "did anything on this machine change?"** A quick photo of
   everything the plan gets made *from*: config files, GPU, python/torch.
   Costs a millisecond, touches no network. Photo matches yesterday's →
   stop immediately, do nothing. Differs → don't panic, ask question 2.
2. **Identity -- "something changed, but does the *plan* come out
   different?"** Actually compute the plan (the generated `pixi.toml` +
   wheel URLs) and compare it to last time's. Comment edit → photo differs,
   plan identical → skip the rebuild. Package added → plan differs →
   rebuild.
3. **Stamp -- "is this env the right one for the machine about to *use*
   it?"** A different moment entirely: launch time, not install time. A
   label on the finished env ("built for py3.13 + torch2.8 + cu128") that
   `register_nodes()` reads before binding a worker. Wrong stack → refuse
   and fall back, because a worker on a different torch than the parent
   does not fail cleanly -- it corrupts tensors crossing the boundary.

Seal 1 makes *skipping* cheap, seal 2 makes *rebuilding* rare, seal 3 makes
*using* safe.

Two words carry the whole page, so they are defined first. The **inputs**
are the facts which an env config is computed *from*:

1. the raw **bytes of an env's `comfy-env.toml`** (bytes, not meaning --
   a comment edit changes them)
2. the declared **`[cuda]` package names**, *across every discovered env* --
   cross-env because the whole machine makes ONE shared wheel-combo
   decision: the chosen (CUDA x torch) cell must have wheels for every cuda
   package declared anywhere, so pack A adding `natten` can shift the cell
   and change pack B's torch pin with zero edits to pack B. Hashing the
   union means a declaration change anywhere misses everyone's fast key,
   and seal 2 then decides per env whose plan actually changed.
3. the requested **python version** per env (`python = "..."` or "host")
4. the host **ABI tag** (python + torch + cuda stack of the bootstrap
   interpreter)
5. **GPU presence** (`has_nvidia_gpu()`)
6. a **manifest-format constant** (bumped when the *shape* of what install
   writes changes, so an on-disk layout from an older comfy-env cannot
   survive the skip -- added in 0.4.31 for the wheel inlining)

The **output** is what the derivation produces from them: the generated
`pixi.toml` plus the resolved cuda wheel URLs.

| # | Seal | Lives in | Question | When it is consulted |
|---|---|---|---|---|
| 1 | **fast key** | `install.hash`, line 2 (`fastkey:<sha256>`) | did any *local input* change? | every `install()`, first thing, per env |
| 2 | **identity** | `install.hash`, line 1 (`v3:<sha256>`) | did the *derived output* change? | only when seal 1 missed (or the env is on a fallback combo) |
| 3 | **stamp** | `env.stamp.json` | was this env built for *this* stack? | at runtime, by `register_nodes()`, before binding a worker |

Both files sit in the env's manifest directory
(`<workspace>/envs/<name>-<abi>/`), next to the generated `pixi.toml`.

## Seal 1 -- the fast key (`_fast_key`, `install/workspace.py`)

A sha256 over the **six inputs listed at the top of the page** -- all
readable from disk in a millisecond: no network, no torch import. When every
env's fast key matches (and each env is materialized, and none sits on a
fallback combo), the whole `install()` run exits before torch is even
resolved: the cheap N-installs-per-CI-run path.

It is deliberately **pessimistic**: editing a comment in `comfy-env.toml`
misses the key, because the key hashes bytes, not meaning. That is fine --
a miss costs one derivation, not a rebuild, because seal 2 stands behind it.

What it deliberately **cannot see**: the cuda-wheels index. Nothing remote is
an input to the fast key. That blind spot is why fallback-combo envs are
excluded from the level-1 skip -- see below.

## Seal 2 -- the identity (`_env_identity`, `install/workspace.py`)

A sha256 over the **derivation output**: the canonical generated `pixi.toml`
plus the resolved cuda wheel URLs. Computed only when seal 1 missed. Match →
the hash file is refreshed and the env is skipped, **no rebuild**. Mismatch →
`pixi install`.

The division of labour buys three specific behaviours:

- **Comment and `[env_vars]` edits never rebuild.** They change the fast key
  (bytes) but not the generated output -- one cheap derivation, then skip.
- **Fallback envs heal themselves.** An env stamped `:fallback` re-derives on
  *every* run: the moment its missing wheel is published, the resolved combo
  changes, the identity changes, and the env rebuilds onto the host's own
  cell -- with zero local changes. This is the one place the remote world
  enters the skip decision, and it enters at level 2, where it belongs.
- **comfy-env version bumps rebuild nothing.** The identity depends only on
  output; the version is recorded in seal 3 for diagnostics.

`install.hash` format (one entry per line): `v2:<identity>` then
`fastkey:<fastkey>`. A single-line legacy v1 file is grandfathered -- the env
is accepted as-built once, and the file is rewritten in v2 form so drift
tracking starts from there. Delete an env's `install.hash` to force a
rebuild.

## Seal 3 -- the stamp (`env.stamp.json`, `environment/cache.py`)

Written only after a **successful** install, read at **runtime**:

```json
{
  "comfy_env_version": "0.4.24",
  "abi_tag": "py313-torch2-10-cu128",
  "torch_pin": "==2.10.*",
  "provenance": "install_workspace:tier1",
  "pixi_lock_sha256": "...",
  "accel_imports": {"faithc-aot": ["faithcontour"], "cumesh": ["cumesh"]}
}
```

`accel_imports` maps each declared `[cuda]` package to the top-level import
names it actually installs. A distribution name is not an import name and
cannot be derived from one -- `faithc-aot` installs `faithcontour`, `pyyaml`
installs `yaml` -- so anything checking the lazy-import rule from outside the
env can only guess, and guesses wrong silently. Install is the one moment the
answer is knowable, because the env exists and its metadata is readable;
recording it here is what lets
[`comfy-test lint --check accel`](../comfy-test/index.md) be exact on a bare
checkout. Unlike the other fields it is **not** consulted by
`validate_env_stamp`: a wrong or missing mapping degrades a downstream check
to "could not verify", never to a wrong bind.

`validate_env_stamp` runs at `register_nodes()` bind time. Without it, an env
would be trusted purely because its directory exists -- and a foreign-stack
env gets loaded into torch's *private* multiprocessing ABI
(`reduce_storage` / `rebuild_cuda_tensor`), which has no version handshake of
its own. A present stamp that disagrees on the ABI tag **fails the bind**
(fall back to in-process import); an unstamped env passes with a note, the
don't-break-userspace case for envs that predate stamping.

Install-side, the stamp is consulted for exactly one field: `provenance`
ending in `:fallback` is what exempts an env from the level-1 skip.

## Which seal moves when

| Event | fast key | identity | stamp | Net effect |
|---|---|---|---|---|
| edit a comment / `[env_vars]` in the TOML | miss | match | -- | one derivation, no rebuild |
| add a dependency to the TOML | miss | miss | rewritten | rebuild |
| wheel published for a fallback env | match (can't see it) | miss (re-derived anyway) | rewritten, provenance → tier1 | env upgrades itself |
| comfy-env version bump | match | match | (stale version, harmless) | nothing |
| host torch / Python upgrade | miss (ABI) | miss (manifest pin) | new ABI = new env *directory* | fresh env built beside the old one |
| GPU appears / disappears | miss | miss (cpu↔cu index flip) | rewritten | rebuild |
| env dir deleted, files intact | (not consulted alone) | -- | -- | not materialized → rebuild |
| stamp disagrees with running stack | -- | -- | **fails bind** | runtime falls back to in-process import |

## Where each is read and written

| Artifact | Written by | Read by |
|---|---|---|
| `install.hash` (both lines) | `_write_hash_file`, after successful install and on identity-match refresh | the level-1 gate and level-2 comparison in `install_workspace` |
| `env.stamp.json` | `write_env_stamp` (`cache.py:310`), post-install | `validate_env_stamp` (`cache.py:350`) at bind time; `_stamp_provenance` (`workspace.py:358`) for the fallback exemption |

## Things to think about

- **The metadata cache doesn't use any of this.** Its key is a hash of `.py`
  mtimes -- it ignores `pixi_lock_sha256`, which seal 3 already computes and
  which is exactly the environment-identity input it lacks. (The 2026-08
  metadata review's main finding; see the roadmap.)
- Seal 1 and seal 3 both encode the ABI tag -- one as a hash input, one as a
  named field. If the ABI tag's definition ever changes, both move at once by
  construction, but nothing *tests* that they agree.
- `pixi_lock_sha256` is recorded but currently read by nothing. It is the
  natural future input for both the metadata cache key and a lockfile-drift
  diagnostic.
