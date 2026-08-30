# `windows-portable-cuda`

> The portable bundle with a GPU, installed fresh. The closest CI gets to what
> a typical Windows ComfyUI user actually has.

| | |
|---|---|
| **OS / accelerator** | Windows / CUDA |
| **Install method** | `portable` -- the official ComfyUI bundle |
| **Runner** | `[self-hosted, windows, cuda]` |
| **Install path** | **fresh** |
| **Config key** | `[test.windows_portable_cuda]` |
| **Dispatch only** | yes |

## What a green cell proves

That your pack installs into an embedded Python with no venv and no compiler,
on a real GPU, and that its CUDA workflows execute there. Of the ten lanes,
this is the one whose environment most closely matches the median Windows user.

## Gotchas

- **The embedded Python is the hard part, not the GPU.** Most failures here are
  the portable constraints from
  [`windows-portable-cpu`](windows-portable-cpu.md) -- no `.git`, no compiler,
  `python_embeded` paths -- surfacing on the fresh path where the install
  actually runs.
- **Prebuilt wheels matter.** With no compiler available, a CUDA dependency
  without a Windows wheel for the resolved torch simply cannot install. See
  [cuda-wheels](../../cuda-wheels/index.md).

## See also

- [`windows-portable-cpu`](windows-portable-cpu.md) -- the hosted, attach version
- [torch, torchvision and torchaudio](../torch-triple.md)
