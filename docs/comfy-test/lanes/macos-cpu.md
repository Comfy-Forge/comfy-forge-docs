# `macos-cpu`

> Apple Silicon, MPS rather than CUDA, and the most expensive runner in the
> matrix. The lane that catches "CUDA or nothing" assumptions.

| | |
|---|---|
| **OS / accelerator** | macOS / CPU |
| **Install method** | `manual` -- venv + a ComfyUI checkout |
| **Runner** | `macos-latest` (GitHub-hosted, **10x** billing) |
| **Install path** | **attach** |
| **Config key** | `[test.macos]` |
| **Also accepts** | `macos`, `macos_cpu` |

## Why this lane exists

It is the only lane where `cuda_capable` is false. A pack that reaches for
`torch.device("cuda")` unconditionally, or calls `torch.cuda.empty_cache()` as
if it were universal, fails here and passes everywhere else.

The ComfyUI-native answers are `comfy.model_management.get_torch_device()` and
`soft_empty_cache()`, which dispatch across MPS, XPU, NPU, MLU and CUDA.
[`syntax`](../levels/syntax.md) fails the first pattern statically and
[`warnings`](../levels/warnings.md) reports the second.

## Cost, and `execution_light`

At 10x billing this lane dominates a full matrix's runner budget. It is also
memory-constrained: the full per-frame capture loop can peg the browser process
and kill the Playwright IPC pipe on a 7 GB runner, which is exactly why
[`execution_light`](../levels/execution_light.md) exists
([ADR-0011](../adr/0011-execution-light-is-a-level.md)).

A pack that hits this lists `execution_light` instead of `execution`; for
per-lane variation use `skip_workflow`:

```toml
[test.macos]
skip_workflow = true   # run the pipeline, not the workflows
```

## Gotchas

- **Attach lane** -- no installability claim.
- **macOS resolves pack requirements differently.** That lane overrides the
  index-routed install path with plain uv, so `extra_pip_indices` may not reach
  your pack's own requirements there. Verify before depending on it.

## See also

- [`macos-desktop`](macos-desktop.md) -- the Electron app on the same hardware
