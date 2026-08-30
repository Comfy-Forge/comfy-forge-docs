# `windows-portable-cpu`

> The official Windows portable bundle: an embedded Python, unpacked rather
> than installed, with no `.git` and no venv. It breaks packs that assume a
> normal Python installation.

| | |
|---|---|
| **OS / accelerator** | Windows / CPU |
| **Install method** | `portable` -- the official ComfyUI bundle |
| **Runner** | `windows-latest` (GitHub-hosted, 2x billing) |
| **Install path** | **attach** |
| **Config key** | `[test.windows_portable]` |
| **Also accepts** | `windows-portable`, `windows_portable`, `windows_portable_cpu` |

## Why this lane exists

The portable bundle is how a large share of Windows users actually run ComfyUI,
and it is not a normal Python environment:

- **`python_embeded`, not a venv.** No `activate`, no `pip` on PATH; packages
  install with `python_embeded\python.exe -m pip`.
- **No `.git`.** ComfyUI is unpacked from a release archive, so anything
  shelling out to `git` inside the ComfyUI root fails.
- **No compiler.** A dependency with no Windows wheel cannot fall back to
  building from source the way it might on a developer machine.

A pack that installs fine on `windows-cpu` and fails here is almost always
assuming one of those three.

## Pinning the bundle

```toml
[test.windows_portable]
comfyui_portable_version = "v0.3.60"
```

Unset, the lane takes the latest release. Pin it when you need a red run to
stay reproducible.

## Gotchas

- **Attach lane.** Like the other hosted lanes it does not prove
  installability; see [Lanes](../lanes.md#what-a-green-cell-means-read-this-one).
- **The bundle is large.** Extraction dominates this lane's wall-clock, which
  is why the hosted version caches it.

## See also

- [`windows-cpu`](windows-cpu.md) -- the same OS with a conventional install
- [`windows-portable-cuda`](windows-portable-cuda.md) -- the fresh, GPU version
