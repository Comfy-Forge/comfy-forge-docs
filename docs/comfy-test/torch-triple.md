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

torchvision declares its torch exactly; torchaudio *usually* shares torch's
version number. **comfy-test keeps no table of these** -- the mapping is
derived from the wheel index and cached on disk, so nothing in source needs
updating when torch ships.

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

1. Resolve the triple against this run's index (`common/torch_triple.py`).
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
torch_version = "2.10.0"          # any version on this run's index
torch_version = "latest"          # opt out; let uv resolve freely
torch_version = "2.13.0/0.28.0/2.13.0"   # explicit triple, checked by nobody
```

The slash form is the escape hatch for a torch released after your comfy-test
version -- **and it is the one you want when a release lands out of order.**
torch, torchvision and torchaudio do not ship simultaneously; torch frequently
lands first. Until the matching torchvision and torchaudio appear, that torch
has no valid triple, and `"latest"` will either resolve to the previous
complete set or produce a conflict.

An unresolvable version is a hard error that suggests the newest complete
triple, rather than a silent fallback.

Precedence, highest first: `COMFY_TEST_TORCH_VERSION`, then
`--torch-version`, then `[test] torch_version`, then the built-in default.

### Resolution, and when it aborts

**The triple is resolved against the index the install will actually use** --
`download.pytorch.org/whl/cpu` or `.../cu128` -- not against PyPI.

That distinction is the whole point, and it is easy to get wrong. PyPI already
publishes torch 2.13.0; the cu128 index tops out at 2.11.0. Resolving against
PyPI would eventually name a triple that does not exist on the CUDA index, and
because the install passes `--extra-index-url pypi --index-strategy
unsafe-best-match`, uv would quietly satisfy all three from **plain PyPI
wheels on a CUDA lane** -- no CUDA, no error.

Within that index:

1. **torchvision's declared pin** is read from its metadata. Both spellings are
   accepted: modern `torch==2.13.0` and the legacy `torch (==2.2.1)` that every
   torchvision up to 0.17.1 uses.
2. **torchaudio is paired by version number, then verified.** The convention
   usually holds, but not always -- `torchaudio 2.0.1` requires `torch==2.0.0`,
   and `2.0.2` requires `2.0.1`. When it declares a pin, that pin wins and a
   contradiction is refused. When it declares nothing (`2.11.0` declares no
   torch dependency at all), the version-number pairing stands.
3. **A hard error**, at config-parse time -- before a venv is built.

Results cache in `~/.comfy-test/torch_triples.json` for a day, per index
variant, written atomically so parallel lanes cannot tear it.

!!! danger "Why the resolver cannot be trusted to do this"
    Because torchaudio 2.11.0 declares no torch dependency, asking uv for
    `torch==2.13.0 torchvision torchaudio` resolves **successfully** to
    torchaudio 2.11.0 against torch 2.13.0 -- there is no declared constraint
    to violate. It installs clean and dies at import. Verified with a dry run,
    not hypothetical.

The error names what is wrong:

```text
[test] torch_version = '2.12.0'

torch 2.12.0 is not published on the cu128 index, or ships neither companion.

Use the newest complete triple instead:
    torch_version = "2.11.0"
Or state the triple explicitly:
    torch_version = "2.12.0/<torchvision>/<torchaudio>"
```

"Not published" and "index unreachable" are deliberately different messages --
an offline run must not claim a version does not exist.

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
