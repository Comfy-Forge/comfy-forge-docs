# CW-ADR-0014: Zero-shim sharding — partition at the compiler, hand off a cache

**Status:** accepted (Linux); Windows remains on the legacy per-package path

## Decision

> **`sharding: N` in a package YAML is the entire opt-in.** A wrapper
> installed into the nvcc seat hash-partitions translation units across N
> shard jobs; the handoff to the link job is each shard's **content-
> addressed compiler cache (ccache)**, not the build tree. The link job
> unions the caches, replays the full build as cache hits, and links one
> ordinary fat wheel. No per-package shard code exists on Linux.

## Context

flash-attention cells run 3-6+ hours against GitHub's 6-hour job cap
(73 translation units, recompiled once per arch), and the original
sharding mechanism (CW-ADR-0006) required a hand-written shim per package
to make its setup.py compile only a slice. Three independent designs were
commissioned and debated: intercept `ninja` (rewrite the build graph),
monkeypatch `torch.utils.cpp_extension` (filter source lists), or
partition below the build system entirely. The third won on simplicity
(a ~30-line wrapper vs a 150-line ninja parser), coverage (anything that
routes compiles through the nvcc seat), and for what it deletes: the
entire mtime-identity problem class -- `touch -m` rituals, PAX-nanosecond
tar flags, ninja-state surgery -- because a content-addressed cache keys
on bytes, not timestamps.

## Mechanism

- The build action installs a wrapper as `PYTORCH_NVCC` (torch's ninja
  writer honours it) and `CUDACXX` (cmake). Per compile: hash the source
  path; `hash % N != my index` &rarr; copy a prebuilt empty object (and a
  stub depfile) and exit 0; otherwise `exec ccache real-nvcc "$@"`.
- Shard jobs upload their `CCACHE_DIR` as the shard artifact. The link
  job extracts every shard's cache into one directory (the layout is
  content-addressed; collisions are byte-identical), unsets the shard
  variables, and runs a plain full build: every compile is a hit, and a
  *partial* restore self-heals -- missing objects recompile, slowly but
  correctly, instead of silently linking stale ones.
- ccache is configured `COMPILERCHECK=content` (the toolkit is installed
  per job; the default mtime check would yield 0%), `BASEDIR`/`NOHASHDIR`
  for path independence, unlimited size, includes-sloppiness.

## The two mandatory guards

Compiler-cache failure is **cliff-edged** -- one bad flag drops the hit
rate from ~100% to ~0% while everything still "works", just too slowly to
finish. Both guards convert that into a red X:

1. A shard whose cache is empty after the build **fails**: the wrapper was
   never consulted (build system bypassed `PYTORCH_NVCC`/`CUDACXX`).
2. The link job asserts a **&ge; 90% cache hit rate** and fails otherwise:
   "the build 'worked' only by recompiling, which defeats sharding."

## Windows

Windows keeps the legacy generic mechanism (a `CUDAExtension` source-list
filter injected into setup.py by the action -- generic, not per-package),
repaired by this change: the object-packaging globs now match MSVC's
`.obj` alongside `.o`, and shard jobs export `LINK=/FORCE:UNRESOLVED` so
the shard-stage partial link succeeds generically (link.exe reads the env
var; the shard's .pyd is garbage by design and discarded). The wrapper is
not ported: nvcc+cl+ccache on Windows is thin ice (PyTorch itself needs a
randomtemp shim), and a shell-script wrapper needs a compiled launcher
under CreateProcess. Port only if the repaired legacy path proves flaky.

## Coverage and known limits

- **Covered, zero-shim:** every torch `cpp_extension` package (~40 of 50)
  on both platforms; cmake packages on Linux *in principle* via `CUDACXX`,
  unvalidated -- the known killer is response files (`--options-file`),
  which ccache mis-parses; the wrapper needs rsp expansion before the
  cmake lane is declared real. natten validates that lane when attempted;
  until then its hand shim in `patches/natten.py` stands (the one
  surviving per-package shard code).
- **Not covered:** pccm/codegen builds (cumm, spconv -- their tooling does
  not consult the nvcc seat; they also no longer need sharding, being
  torch-free and cheap) and AOT script builds (flashinfer).
- **The 256-entry matrix cap:** cells x shards per dispatch must stay
  under GitHub's limit, and an oversized matrix fails with every listed
  job green (the job is never created). Full-grid `--overwrite` of a
  sharded package must be dispatched in filtered slices.
- Nondeterministic codegen across shard jobs defeats the cache keys; the
  hit-rate guard catches it but cannot repair it.

## Alternatives rejected

- **Per-arch wheel splitting** (`+cu128torch2.8sm90`): rejected outright --
  comfy-env's resolver substring-matches `+cu128torch2.8`, so every
  pre-upgrade client would silently install an arbitrary arch; and it
  multiplies artifacts for the same compute saved. See the review's
  compatibility contract: the price is a coordinated two-repo migration
  for less benefit than file-sharding.
- **The ninja-graph shim**: best coverage on paper, but ~380 lines
  including a ninja-file parser to maintain; its self-healing link-mode
  idea survives here via the cache's natural self-healing.
- **The cpp_extension monkeypatch**: clean and net-negative but cannot see
  cmake; its manifest-fingerprint idea survives as the hit-rate guard.
