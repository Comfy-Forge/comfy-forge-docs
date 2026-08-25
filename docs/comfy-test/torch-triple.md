# torch, torchvision and torchaudio

Every run installs these three as a **known-aligned triple**, before anything
else. This page is why that is necessary, where the triple comes from, and what
happens when it is wrong.

## What ComfyUI pins, and what it does not

ComfyUI's own `requirements.txt` is mostly unpinned. Of its 35 requirements
(ComfyUI 0.33.0):

| Form | Count | Examples |
|---|---|---|
| Exact `==` | 5 | `comfyui-frontend-package==1.49.6`, `comfy-kitchen==0.2.31` |
| Floor `>=` | 11 | `numpy>=1.25.0`, `transformers>=4.50.3`, `av>=16.0.0` |
| Compatible `~=` | 2 | `pydantic~=2.0` |
| **Bare name** | **17** | `torch`, `torchsde`, `torchvision`, `torchaudio`, `scipy`, `Pillow`, `requests`, `spandrel` |

That is the right choice for ComfyUI -- it wants to run on whatever the user
already has -- and it means most of the environment under test floats. The five
exact pins ride along with the ref you clone, so setting `comfyui_version` also
pins the frontend, the workflow templates and the embedded docs.

comfy-test pins **three** of those seventeen bare names. Not because the other
fourteen are unpinned, but because of what happens below.

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

### Why not torchsde, kornia or spandrel

They sit beside torch in the same file and read like the same family. They are
not, and the distinguishing test is mechanical: **does the package ship a
binary linked against `libtorch`?**

| Package | Wheel | Declares | Coupled? |
|---|---|---|---|
| `torchvision` | `cp310-cp310-...` | `torch==2.13.0` | **yes** |
| `torchaudio` | `cp310-cp310-...` | *(nothing)* | **yes** |
| `torchsde` | `py3-none-any` | `torch>=1.6.0` | no |
| `kornia` | `py3-none-any` | `torch>=2.0.0` | no |
| `spandrel` | `py3-none-any` | `torch`, `torchvision` | no |

torchsde is an SDE solver written in Python against torch's *public* API. No
compiled extension, so no C++ ABI to break -- and its own requirement is a
floor rather than an exact pin precisely because it does work across torch
releases. Adding it to the triple would be pinning for symmetry.

The same test clears ComfyUI's compiled dependencies. `comfy-kitchen`,
`comfy-aimdo` and `comfy-angle` ship native code, but scanning their wheels for
`libtorch`/`libc10` references finds none -- they are not torch extensions.
`scipy`, `av` and `blake3` are compiled against their own libraries.

So **torchvision and torchaudio are the only two packages in ComfyUI's
requirements that moving torch can break**, and that is the whole membership
rule for the triple.

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
4. **Import all three and check they agree**, before any level runs. A pin is a
   claim about what *should* be installed; this is the only step that observes
   what actually is. It fails the run naming the versions, rather than letting
   an undefined symbol surface three levels later inside your node's code.

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
    to violate, so nothing in the resolution reflects that the pair was never
    built or tested together.

    Whether that *breaks* is a separate question, and the honest answer is
    "sometimes". Installed for real on a CPU lane, those exact three import
    cleanly and run compiled ops -- `torchvision.ops.nms` and
    `torchaudio.functional.resample` both work. The ABI break is real but it is
    version- and variant-dependent, not guaranteed.

    That is the argument for pinning, not against it. An unconstrained resolve
    gives you a combination nobody validated, which happens to work today and
    carries no guarantee about tomorrow -- and when it does break, it breaks as
    an undefined symbol at import rather than as a version conflict at install.

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

## Why not pin the other fourteen too

`einops`, `pyyaml`, `Pillow`, `scipy`, `tqdm` and `psutil` are exactly as
unpinned as torch is. They are not pinned because they fail in a different
way, and only one of the two failure modes is the installer's fault.

### Class A -- a set that cannot be resolved one package at a time

The torch family breaks even when **every declared constraint is satisfied**.
Upstream [#14384](https://github.com/Comfy-Org/ComfyUI/issues/14384):

```text
RuntimeError: Detected that PyTorch and TorchAudio were compiled with
different CUDA versions. PyTorch has CUDA version 13.0 whereas TorchAudio
has CUDA version 12.8.
```

`torch 2.12.0+cu130` with `torchaudio 2.12.0+cu128` -- the public versions
agree, every pin is honoured, and it refuses to start. The disagreement lives
in the local segment, which no specifier mentions.
[#11093](https://github.com/Comfy-Org/ComfyUI/issues/11093) is the same class
one layer out: `undefined symbol: _ZN3c104cuda9SetDeviceEa` -- `c10` is
torch's own C++ namespace, so that is a binary linked against a different
libtorch. And [#14232](https://github.com/Comfy-Org/ComfyUI/issues/14232) is
the availability half: torchaudio has no stable wheel for `cu132`, so on that
index **no correct triple exists at all** and the only honest move is to say
so before building anything.

No newer version fixes any of these. There is no version of torchaudio that is
correct on its own -- only a *set* is correct, and the constraint that defines
the set is invisible to the resolver.

### Class B -- ordinary version drift

The other fourteen fail the normal way: an API moved, and you get a legible
traceback naming the symbol. Upstream
[#13036](https://github.com/Comfy-Org/ComfyUI/issues/13036) is the archetype --
`ImportError: cannot import name 'mapped_column'` on SQLAlchemy 1.x, fixed by
raising the floor to `>=2.0`.

Three things are true of Class B and false of Class A:

- some version is always correct **on its own**, and it is usually the newest;
- the break is legible -- a named symbol, not an undefined one;
- **the break is true.** A user installing your pack today hits precisely the
  same failure.

That last point is the reason. Pinning Class B would not prevent breakage, it
would *hide* breakage your users are already having -- turning a nightly run
from an early-warning system into a museum. Class A is different because the
failure is manufactured by the install procedure itself, not by upstream
shipping something new.

!!! warning "Class A is not strictly unique to torch"
    `numpy>=1.25.0` has no ceiling, and numpy 2.0 broke the C ABI -- a wheel
    compiled against numpy 1.x fails against 2.x for structurally the same
    reason. comfy-test does not address that today. It is a narrower risk (one
    package rather than a three-way set, and no invisible local segment), but
    it is a real gap rather than a solved problem.

### Upstream is not going to pin them either

Worth knowing before filing an issue about it. Across 360 commits to
`requirements.txt`:

- **No upper bound has ever shipped.** Not one `<` on any dependency, ever;
  the only bounded specs are `pydantic~=2.0` and `pydantic-settings~=2.0`.
- **Constraints are floors, added reactively** after a specific breakage --
  SQLAlchemy after #13036, `aiohttp>=3.11.8` after "crash caused by outdated
  incompatible aiohttp", `safetensors>=0.4.2` "for fp8 support".
- **Exact pins get removed.** Commit `9a151b7d`, *"Fix issue and unpin spandrel
  package"*, changes the calling code to suit the new spandrel rather than
  holding the old one back.
- **Every `==` in the file is a Comfy-Org package.** They pin what they ship
  and nothing they do not.
- **Nobody has proposed a lockfile.** Zero issues or PRs.

So the floating environment is a deliberate upstream position, not an
oversight, and a test harness that locked it would be testing a configuration
no user runs.

There is no config lever that freezes the rest, either. `extra_pip_indices`
adds `--extra-index-url` entries, so it widens the search rather than
restricting it -- aiming it at a frozen mirror does not stop uv reaching
pypi.org. The only real lever is pinning in **your own `requirements.txt`**,
which is installed after ComfyUI's and does take precedence -- at the cost
that your pins are also what every user gets, and a pack that pins
`transformers` exactly will collide with the next pack that does.

See [Reproducibility](reproducibility.md) for the full list of what does and
does not hold still.

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
