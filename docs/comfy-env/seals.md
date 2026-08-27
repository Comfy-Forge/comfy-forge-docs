# The three seals

## Definitions

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

## Quick explanation

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