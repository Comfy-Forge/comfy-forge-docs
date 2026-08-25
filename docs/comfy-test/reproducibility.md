# Reproducibility: what a result actually means

comfy-test guarantees less than a dashboard full of green cells implies.
This page states the limits plainly, because a CI tool that overstates its
own determinism is worse than one that has none.

## What is pinned

- **The torch family** -- `torch`/`torchvision`/`torchaudio` are installed
  as a known-aligned triple before your requirements, so nothing upgrades
  them ([ADR-0005](adr/0005-pinned-torch-random-python.md)). *Only on
  fresh-install lanes.*
- **comfy-test itself** -- when a lane is called at a release tag, the
  version is pinned to match.
- **Your pack** -- the commit under test is recorded (`commit_hash`).

## What floats

- **The Python interpreter is 3.13 by default**, and only varies if you
  ask it to: `[test] python_version` accepts a list, and a list draws one at
  random per run. When you do widen it, a re-run may pick a different
  interpreter and go green without a fix -- check `provenance.python_version`
  before concluding anything.
- **ComfyUI is a shallow clone of HEAD** by default (`comfyui_version =
  "latest"`). The version string only moves on releases, so
  `provenance.comfyui_commit` -- the SHA -- is the field that identifies
  what ran.
- **Hosted lane caches are effectively immortal.** The key is only
  (lane, Python version), so ComfyUI and torch stay frozen at whatever
  HEAD first populated it until GitHub evicts. Two runs a month apart on the
  same attach lane may test very different ComfyUI code with identical
  metadata.
- **Attach lanes do not exercise the torch pin at all** -- they inherit
  whatever their cache holds ([ADR-0003](adr/0003-two-install-paths-attach-and-fresh.md)).
- **Dispatch lanes install comfy-test with `pip install --upgrade`**, making
  behaviour a function of PyPI state at run time.
- **There are no workflow seeds.** Sampler determinism is the node author's
  responsibility; comfy-test does not fix seeds, and identical runs may
  produce different images. Timings in `results.json` are observations, not
  benchmarks.

## The provenance block

Every `results.json` carries what actually produced it:

```json
"comfyui_version": "0.3.68",
"comfyui_commit": "b323a34...",
"commit_hash": "<your pack's SHA>",
"provenance": {
  "comfy_test_version": "0.4.7",
  "python_version": "3.12",
  "torch_version": "2.10.0",
  "torch_triple": {"torch": "2.10.0", "torchvision": "0.25.0", "torchaudio": "2.10.0"},
  "install_mode": "attach",
  "levels": ["syntax", "install", "registration", "execution"]
}
```

`install_mode` is the field to read first: `attach` means the environment
was prebuilt and INSTALL was a no-op, so the result says nothing about
installability. `torch_triple` is `null` when nothing pinned it (the
Desktop app brings its own).

## Reproducing a red run

1. Read `provenance` and `comfyui_commit` from the failing artifact.
2. Recreate locally with the same interpreter and ComfyUI ref:

    ```bash
    comfy-test run --lane linux-cpu --level execution
    ```

    with `python_version` and `comfyui_version` pinned in your
    `comfy-test.toml` to the recorded values.
3. Note that a local run is always **fresh**, so a failure that only
   reproduces on an attach lane is a property of that lane's cache, not of
   your pack.

## What would improve this

Recording provenance was the first step. The remaining gaps -- cache keys
that ignore ComfyUI's SHA, `--upgrade` installs on dispatch lanes, and the
absence of a "re-run exactly this" command -- are real and unfixed. They are
listed in the [roadmap](../roadmap.md) rather than papered over here.
