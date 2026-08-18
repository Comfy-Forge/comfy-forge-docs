# The PyTorch wheel index, and its many opinions

Everything in this farm is pinned against `download.pytorch.org/whl/`. It is the
only source that describes what people can actually download, so it is the only
source worth trusting -- and it is not tidy. What follows is the list of
inconsistencies the scraper had to be taught, each one discovered the same way:
something looked wrong on the published matrix, and upstream turned out to be
right in a way nobody expected.

None of this is a complaint. Every one of these has a plausible history. But a
scraper written against a mental model of how the index *ought* to look will be
quietly wrong, and quietly wrong is the expensive kind.

## The local version tag is optional, sometimes

A CUDA wheel usually announces itself in its filename:

```
torch-2.5.1+cu126-cp310-cp310-manylinux_2_28_aarch64.whl
```

The `+cu126` is a PEP 440 local version. It is how everything downstream tells
one build from another. So it is reasonable to assume every wheel under
`/whl/cu124/` carries `+cu124`.

Eighteen of them do not:

```
torch-2.5.1-cp310-cp310-linux_aarch64.whl
```

That is a genuine CUDA 12.4 ARM build, hosted under `/whl/cu124/`, absent from
the CPU index, and it carries no local version at all. The adjacent `cu126`
index tags its ARM wheels normally. Requiring the tag made our matrix page state
that PyTorch had never shipped CUDA 12.4 for ARM, which it had.

**What we do:** trust the URL, not the filename. A wheel belongs to the index it
is served from. The `href` is the fact; the filename is a courtesy.

## Every index contains wheels from every other index

Fetch `/whl/cu132/torch/` and you will find, among the Blackwell-era builds,
`torch-0.1.6.post17-cp27mu-...-macosx_10_7_x86_64.whl`.

Ninety-eight such links appear in **every** CUDA index, byte for byte identical,
because they point at the shared root path `/whl/torch-...` rather than into the
index's own directory. They are one mirrored set of legacy wheels, surfaced
everywhere.

**What we do:** the same URL check. Root-path links are mirrors; directory links
are that index's own builds. The split is exact -- 98 mirrored links in every
index, no exceptions.

## The stable index ships dev builds

`/whl/cu126/torch/` -- the release index, not `/whl/nightly/` -- contains ten
wheels named `torch-2.13.0.dev20260610+cu126-...`. Properly tagged, properly
hosted, and a dated development snapshot.

**What we do:** exclude them deliberately rather than by accident. Our version
pattern happened not to match `.dev20260610`, which is the right outcome reached
the wrong way; a filter nobody chose is a filter nobody can reason about.

## CUDA versions are not consecutive

There is CUDA 13.0. There is CUDA 13.2. There is no 13.1 -- `/whl/cu131/`
returns 403, as do `cu133` and `cu135`. Nor is the sequence dense further back:
the published set runs cu75, cu80, cu90, cu91, cu92, cu100, cu101, cu102, cu110,
cu111, cu113, cu115 through cu118, cu121, cu124, cu126, cu128, cu129, cu130,
cu132.

**What we do:** discover the list by scraping the index root instead of
predicting it. A hardcoded list is a guess about NVIDIA's future release
numbering, and ours was wrong twice -- it omitted cu129, which this farm actively
builds, and cu132, which upstream had already shipped.

## Some indexes are empty and perfectly healthy

`/whl/cu75/` serves 43 KB of well-formed HTML containing zero wheels this farm
would ever build. So do cu80, cu90, cu91, cu92, cu100 and cu110. They are not
broken; they are old, and they still carry the 98 mirrors.

This matters because "the page parsed but produced nothing" is a perfectly good
description of both *an empty index* and *upstream changed their markup*. Those
need opposite responses.

## Coverage is ragged in every direction

- **A version can skip an index.** `cu129` has torch 2.12.**1** and not 2.12.0.
- **An index can stop.** `cu128` tops out at torch 2.11 while `cu126` -- an
  older CUDA -- carries 2.13. The index most Blackwell users are pinned to is
  the one upstream moved on from first.
- **Windows comes and goes.** `cu129` has Windows builds through torch 2.9.0 and
  none after.
- **Coverage varies by Python within a single release.** `cu124` torch 2.5.1 has
  Windows for py3.10 through py3.12 and not for py3.13. Torch 2.13.0 has no
  Windows for py3.15 on cu126, cu130 or cu132.

That last one is the reason our
[matrix page](https://pozzettiandrea.github.io/cuda-wheels/matrix/) puts the
platform letters inside each cell. A single "platforms" column per row is a
union across Python versions, and a union is wrong exactly where the interesting
cases are.

## Platform tags are a small archaeology

`linux_x86_64`, `manylinux2014_aarch64`, `manylinux_2_28_x86_64`, and in the CPU
index, compressed tag sets like
`manylinux_2_28_aarch64.manylinux2014_aarch64` and 21 `win_arm64` wheels that
have no CUDA counterpart yet. Each style is correct for when it was minted.

## And upstream's own description of itself disagrees

PyTorch publishes a declared build matrix in `pytorch/test-infra`. Measured
against the actual server it is wrong in three directions at once: it omits
cu124, cu128 and cu129, which between them carry hundreds of downloadable
wheels; it lists a CUDA version that is not published at all; and it misses the
real gap, cu129 having no Windows build for torch 2.12 and 2.13.

**What we do:** never consult it. The wheel index is the only thing that
describes what a user's machine can actually install, so it is the only thing
we scrape.

---

The through-line, if there is one: **observation beats declaration.** Every rule
above replaced an assumption about how the index should be shaped with a fact
about how it is. The scraper is a little uglier for it and correct instead of
plausible, which is the trade this whole repository keeps making.
