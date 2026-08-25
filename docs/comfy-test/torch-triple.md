# torch, torchvision and torchaudio

Every run installs these three as a **known-aligned triple**, before anything
else. This page is why that is necessary, where the triple comes from, and what
happens when it is wrong.

## ComfyUI does not pin them

ComfyUI's own `requirements.txt` asks for them by bare name:

```text
torch
torchsde
torchvision
torchaudio
```

No version, no floor, no ceiling. Whatever the resolver picks on the day is
what you get -- which is correct for ComfyUI (it wants to run on whatever the
user has) and useless for a test harness (a run must be reproducible, and a
green result must mean something specific).

That is the gap comfy-test fills. Note what ComfyUI *does* pin exactly:

```text
comfyui-frontend-package==1.49.6
comfyui-workflow-templates==0.11.41
comfyui-embedded-docs==0.5.10
comfy-kitchen==0.2.31
comfy-aimdo==0.4.13
```

So the **frontend is already pinned by the ComfyUI ref you clone** -- pinning
`comfyui_version` pins the UI too. The torch stack is the part left floating.

## The three are ABI-coupled

`torchvision` and `torchaudio` are not pure Python. They ship compiled
extensions linked against `libtorch`, and PyTorch publishes **no stable C++ ABI
across releases**. An extension built against torch 2.10 does not load against
2.11 -- it fails at import with an undefined-symbol error, not at install.

Upstream knows this and declares it. `torchvision 0.25.0` requires:

```text
torch==2.10.0
```

and `torchaudio 2.10.0` likewise. An **exact** pin, not a range. So the three
versions are not independently choosable: picking a torchvision picks a torch.

The correspondence is offset, because torchvision versions independently:

| torch | torchvision | torchaudio |
|---|---|---|
| 2.11.0 | 0.26.0 | 2.11.0 |
| 2.10.0 | 0.25.0 | 2.10.0 |
| 2.9.1 | 0.24.1 | 2.9.1 |
| 2.9.0 | 0.24.0 | 2.9.0 |
| 2.8.0 | 0.23.0 | 2.8.0 |

torchaudio tracks torch's number; torchvision is roughly `0.(minor+15)`. Do not
rely on the arithmetic -- the table is the source of truth.

## Why the exact pin is not enough on its own

`torch==2.10.0` constrains the **public** version. It says nothing about the
CUDA build, because PEP 440 ignores a local version segment unless the
specifier names one:

```text
torch==2.10.0  is satisfied by  2.10.0
                                2.10.0+cu128
                                2.10.0+cu130
                                2.10.0+cpu
```

So `torch 2.10.0+cu130` next to `torchaudio 2.10.0+cu128` satisfies every
declared constraint and still crashes on import: same public version, different
libtorch.

That is the failure mode this exists to prevent, and it is invisible to the
resolver.

## What comfy-test does

Install the triple **first**, explicitly, from the correct index:

1. Resolve the triple from `TORCH_TRIPLES` (`common/config.py`).
2. `uv pip install torch==<t> torchvision==<tv> torchaudio==<ta>` with
   `--index-url` set to the CPU or CUDA backend index, so all three come from
   **one** index and therefore carry the same local tag.
3. Only then install ComfyUI's `requirements.txt`, which now sees its bare
   `torch` / `torchvision` / `torchaudio` already satisfied and leaves them
   alone.

The ordering is load-bearing. These are two separate resolver invocations, and
the second one has no view of what the first decided beyond what is already
installed -- so if the bare names are resolved first, nothing stops an upgrade
that strands the other two.

## Choosing a version

```toml
[test]
torch_version = "2.10.0"          # a key in the table above
torch_version = "latest"          # opt out; let uv resolve freely
torch_version = "2.13.0/0.28.0/2.13.0"   # explicit triple, not yet in the table
```

The slash form is the escape hatch for a torch released after your comfy-test
version -- **and it is the one you want when a release lands out of order.**
torch, torchvision and torchaudio do not ship simultaneously; torch frequently
lands first. Until the matching torchvision and torchaudio appear, that torch
has no valid triple, and `"latest"` will either resolve to the previous
complete set or produce a conflict.

An unknown version is a hard error naming the known keys, rather than a silent
fallback.

Precedence, highest first: `COMFY_TEST_TORCH_VERSION`, then
`--torch-version`, then `[test] torch_version`, then the built-in default.

### Resolution, and when it aborts

A version you name is resolved in three steps:

1. **The checked-in table** above -- offline, instant.
2. **PyPI metadata**, if it is not in the table, so a torch released after your
   comfy-test still works without a code change. The lookup reads
   `torchvision` and `torchaudio`'s `requires_dist` and inverts it; `torch`
   itself declares nothing about them, so it cannot be the source.
3. **A hard error**, at config-parse time -- before a venv is built, not
   twenty minutes into one.

The error names precisely what is wrong. Asking for a torch whose companions
have not shipped yet:

```text
[test] torch_version = '2.13.0'

torch 2.13.0 has no complete triple yet: torchvision 0.28.0 exists, but no
matching torchaudio has been published.

The three do not release together -- torch lands first and torchaudio follows,
so a just-released torch is not installable as a set.

Use the newest complete triple instead:
    torch_version = "2.11.0"
or, if you know a torchaudio that works, state it explicitly:
    torch_version = "2.13.0/<torchvision>/<torchaudio>"
```

"Not published" and "could not check" are deliberately different messages -- an
offline run says PyPI was unreachable rather than claiming the version does not
exist.

## What actually ran

`results.json` records the resolved triple:

```json
"provenance": {
  "torch_version": "2.10.0",
  "torch_triple": {"torch": "2.10.0", "torchvision": "0.25.0", "torchaudio": "2.10.0"}
}
```

`torch_triple` is `null` when nothing pinned it -- `"latest"`, or a Desktop lane
bringing its own. Read it before concluding anything about a run, especially a
red one: an import-time undefined-symbol error almost always means the triple
was not what you thought.

!!! note "Attach lanes do not exercise the pin at all"
    Hosted CPU lanes attach to a prebuilt cached environment, so the torch
    install never runs and the cache holds whatever populated it. See
    [Reproducibility](reproducibility.md).

## See also

- [What a run does](what-a-run-does.md) -- where the triple install sits in the sequence
- [ADR-0005](adr/0005-pinned-torch-random-python.md) -- the decision
- [cuda-wheels](../cuda-wheels/index.md) -- why compiled CUDA packages are bound to an exact torch, and how prebuilt wheels avoid the compile
