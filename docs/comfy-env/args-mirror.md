# How comfy-env mirrors it

*A worker parses an empty argv, so every host flag is at its default there.
comfy-env carries an allowlist of resolved values across before the worker's
first comfy import. What is on the list, what was left off on purpose, what
was left off by accident, and how a pack could override it.*
{: .subtitle }

## ComfyUI background

How the args namespace works, and why its values freeze at import, is the
subject of its own page.

**[Read it first](comfyui-cli-args.md)**.

## The mechanism

The parent serializes **resolved values, never argv** — an argv replay would
re-parse against a possibly different `cli_args.py`, die with `SystemExit(2)`
on version skew, and leak through `/proc/<pid>/cmdline`. The payload is JSON
of `{dest: value}` in one environment variable, `COMFY_ENV_HOST_ARGS`, and the
worker applies it by `setattr` on `comfy.cli_args.args` **before its first
comfy import** freezes the module-level reads.

`src/comfy_env/mirrored_args.py` is shared by both sides — imported as
`comfy_env.mirrored_args` in the parent and staged beside the worker script
as `mirrored_args` in the worker. Pure, no comfy import at module level.

**The allowlist is the contract.** 33 destinations, in three groups:

| Group | Flags |
|---|---|
| dtype and numerics — the 2× footprint class | `force_fp32`, `force_fp16`, `fp32_unet`, `fp64_unet`, `bf16_unet`, `fp16_unet`, `fp8_e4m3fn_unet`, `fp8_e5m2_unet`, `fp8_e8m0fnu_unet`, `fp16_vae`, `fp32_vae`, `bf16_vae`, `cpu_vae`, `fp8_e4m3fn_text_enc`, `fp8_e5m2_text_enc`, `fp16_text_enc`, `fp32_text_enc`, `bf16_text_enc`, `fp16_intermediates`, `supports_fp8_compute`, `deterministic`, `fast`, `force_channels_last`, `force_non_blocking` |
| memory behaviour frozen at import | `disable_smart_memory`, `disable_pinned_memory`, `async_offload`, `disable_async_offload`, `fast_disk`, `high_ram`, `disable_mmap`, `mmap_torch_files` |
| device placement the VRAM RPC does not carry | `gpu_only` |

Plus one synthetic key, `attention`, which is not an args dest: the parent
resolves the host's backend to `"sage"` or `"flash"` and the worker applies it
at its own attention site with an importability check. A host that *could*
import sage but resolved pytorch attention made a deliberate choice the worker
must not upgrade past.

### Deliberately not mirrored, with the reason recorded

| Flag | Why not |
|---|---|
| `lowvram` / `novram` / `highvram` | `vram_state` already crosses per call on the budget RPC; a second authority can disagree with it |
| `cuda_device` | the worker inherits the host's already-narrowed `CUDA_VISIBLE_DEVICES`; the flag would re-index a second time |
| `cpu` | `COMFY_ENV_COMFY_CPU` / `COMFY_CPU` owns it |
| `reserve_vram` | crosses three times with one owner — `SIMPLE_HEADROOM` at spawn, `EXTRA_RESERVED_VRAM` as the budget owner's advance payment, settled by every budget reply |
| `cache_*` | executor-side; workers run no prompt queue |
| listen, port, auth, path flags | host server surface |

### The escape hatches

| Variable | Effect |
|---|---|
| `COMFY_ENV_MIRROR_ARGS=0` | disables the whole mirror. Exists because a pack's `[env_vars]` cannot *unset* an args write |
| `COMFY_ENV_NO_MIRROR=fast_disk,high_ram` | withholds named flags. The global switch is too big a hammer — escaping one `--fast-disk` regression with it would also surrender the fp8 mirror and reinstate the 2× footprint |
| `COMFY_ENV_WORKER_ATTENTION=auto` | restores the worker's own attention auto-probe, for a pack env richer than the host's |

## What the sweep found

The 107-flag namespace was enumerated mechanically and every flag classified
against the allowlist *and* its stated rationale:

| Class | Count | Verdict |
|---|---|---|
| mirrored | 33 | the contract |
| stated reason, confirmed correct | ~12 | fine |
| covered by an env var or a dedicated seam instead | ~12 | fine — `CUDA_VISIBLE_DEVICES`, `COMFY_ENV_AIMDO_*`, `COMFY_CPU` |
| path flags, covered by the `folder_paths` snapshot | 7 | fine, with one residue: [`models_dir`](folder-paths.md#one-scalar-does-not-cross-models_dir) |
| no worker-side reader at all | ~28 | correctly ignored |
| **read in a worker, absent from both the list and the rationale** | **~15** | **the "nobody noticed" set** |

### The "nobody noticed" set

These freeze at import, exactly the window the mirror exists to hit, and sit
in families the allowlist was never built around:

| Flag | Read at | Effect in a worker |
|---|---|---|
| `use_split_cross_attention`, `use_quad_cross_attention`, `use_pytorch_cross_attention`, `use_ck_attention`, `disable_xformers` | `model_management.py:403, 463-473, 519, 1688` | a `--use-quad-cross-attention` host (the documented low-VRAM workaround) gets workers on SDPA and OOMs in the pack only; a `--disable-xformers` host gets xformers back |
| `force_upcast_attention`, `dont_upcast_attention` | `attention.py:78-86` | black images from the isolated pack while the same model is fine in a host node — the exact symptom the flag exists to cure |
| `cuda_malloc` / `disable_cuda_malloc` | `comfy/ops.py:402` | the env var crosses but the flag is `False`, so a worker allocates cast buffers on `cudaMallocAsync` — a combination the host never runs |
| `verbose`, `log_stdout` | `app/logger.py` | the worker's log level is fixed at `WARNING` regardless — see [logging](logging-approach.md) |
| `enable_triton_backend` / `disable_triton_backend` | `comfy/quant_ops.py:50-56` | a host that force-disabled the ROCm Triton backend gets it back in workers |
| `disable_comfy_compiler`, `disable_cuda_graphs`, `assert_graph_breaks` | `model_prefetch.py`, `gemma4.py` | host disabled the compiler; the worker compiles and CUDA-graphs anyway |
| `directml` | `model_management.py:112-116` | a DirectML host runs GPU; the worker silently falls to CPU |

The pattern is consistent: the allowlist was built around **dtype and memory**,
so flags that *also* freeze at import but live in the attention, allocator or
compiler families were never considered. Every one of them is one line to add.

## Proposed: a per-pack override

!!! note "Proposed, not built"
    This section describes a design that does not exist yet. It is written
    down so the shape can be argued about before it is implemented.

The mirror makes every worker follow the host. Sometimes that is wrong: a
pack's environment may carry a faster attention kernel the host lacks, or a
model that needs upcast attention on a host that runs without it. Today the
only lever is `COMFY_ENV_WORKER_ATTENTION=auto`, which is machine-global and
attention-only.

The proposal is a table under `[options]` — the existing "runtime knobs" key,
so the closed set of four consumed top-level keys stays closed:

```toml
# comfy-env.toml
[options]
health_check_timeout = 5.0

[options.comfyui_args]
use_quad_cross_attention = true
force_upcast_attention = true
```

Semantics:

| Rule | Why |
|---|---|
| Keys are `comfy.cli_args` **dest names**, `hasattr`-validated on the worker side | same rule as the mirror; a ComfyUI without the flag degrades silently instead of erroring |
| Applied **after** the host mirror, before the first comfy import | the pack's value wins for its own worker; every other worker still follows the host |
| Only flags that freeze at import are meaningful | a server or executor flag here does nothing, and the worker should say so at startup rather than accept it silently |
| Denied names from `COMFY_ENV_NO_MIRROR` are **not** exempt | the operator's per-machine denylist outranks a pack's manifest |
| Never `lowvram` / `novram` / `highvram`, `cuda_device`, `cpu`, `reserve_vram` | the same stated reasons as the mirror; a second authority for any of these is the bug the mirror was designed to avoid |

The `attention` synthetic key would accept the full ladder — `sage`, `flash`,
`xformers`, `pytorch`, `split`, `quad`, `auto` — rather than the two the
host resolution carries, since the point is to let a pack diverge.

## See also

- [The args namespace](comfyui-cli-args.md) — why values freeze at import
- [Settings reference](settings.md) — the three escape-hatch variables in context
- [comfy-env and model paths](folder-paths.md) — the path flags' other route
