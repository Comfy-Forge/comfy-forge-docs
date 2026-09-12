# The backend ladder

*ComfyUI picks one attention implementation at import, from a fixed ladder
of flags and probes, and every transformer block in every model uses it. It
is decided once, in one place, and never revisited.*
{: .subtitle }

## One assignment, decided at import

`comfy/ldm/modules/attention.py` is a single if/elif chain that runs
when the module is first imported:

```python
optimized_attention = attention_basic

if model_management.sage_attention_enabled():         # --use-sage-attention
    optimized_attention = attention_sage
elif model_management.flash_attention_enabled():      # --use-flash-attention
    optimized_attention = attention_flash
elif model_management.xformers_enabled():             # xformers importable, not disabled
    optimized_attention = attention_xformers
elif model_management.pytorch_attention_enabled():    # ENABLE_PYTORCH_ATTENTION
    optimized_attention = attention_pytorch
else:
    if args.use_split_cross_attention:
        optimized_attention = attention_split
    else:
        optimized_attention = attention_sub_quad      # the fallback

if model_management.comfy_kitchen_attention_enabled():  # --use-ck-attention
    optimized_attention = attention_comfy_kitchen_int8
```

The name `optimized_attention` is then imported by every attention layer in
`comfy/ldm/`. There is no per-model choice and no runtime switch. Whatever
this resolved to at import is what the whole process uses.

## Where each rung's answer comes from

Two of the predicates read a flag directly; the rest read **module globals in
`comfy.model_management` that were themselves decided at import**:

| Rung | Predicate | Decided by |
|---|---|---|
| sage | `args.use_sage_attention` | the flag, verbatim (`model_management.py`) |
| flash | `args.use_flash_attention` | the flag, verbatim |
| xformers | `XFORMERS_IS_AVAILABLE` and not CPU/DirectML | `--disable-xformers` forces `False`; otherwise an import probe |
| pytorch | `ENABLE_PYTORCH_ATTENTION` | `--use-pytorch-cross-attention` sets it; **or the auto-enable** below |
| split | `args.use_split_cross_attention` | the flag |
| sub-quad | none of the above | the default |
| comfy-kitchen | `args.use_ck_attention` | the flag, applied last and overriding |

### The auto-enable that makes the "off" flags matter

Most users never pass an attention flag, so on NVIDIA the rung that fires is
pytorch — but only because of this:

```python
# model_management.py
if is_nvidia():
    if torch_version_numeric[0] >= 2:
        if ENABLE_PYTORCH_ATTENTION == False and args.use_split_cross_attention == False \
                and args.use_quad_cross_attention == False:
            ENABLE_PYTORCH_ATTENTION = True
```

So `--use-split-cross-attention` and `--use-quad-cross-attention` do not
*select* their backend so much as **suppress the auto-enable** of pytorch
attention, letting the chain fall through to the `else` branch. The same
shape repeats for Intel XPU and AMD.

That is why these two flags are the documented low-VRAM workaround: SDPA's
memory behaviour on a small card is what people are escaping, and the escape
is to stop it being chosen.

## Precision is a separate, parallel decision

Independent of *which* kernel runs is *what dtype* it runs in:

```python
# comfy/ldm/modules/attention.py
FORCE_UPCAST_ATTENTION_DTYPE = model_management.force_upcast_attention_dtype()

def get_attn_precision(attn_precision, current_dtype):
    if args.dont_upcast_attention:
        return None
    if FORCE_UPCAST_ATTENTION_DTYPE is not None and current_dtype in FORCE_UPCAST_ATTENTION_DTYPE:
        return FORCE_UPCAST_ATTENTION_DTYPE[current_dtype]
    return attn_precision
```

`--force-upcast-attention` exists because some GPU/dtype combinations produce
black images in fp16 attention; upcasting to fp32 cures it at a memory cost.
`FORCE_UPCAST_ATTENTION_DTYPE` is captured **once**, at import, so this too
is frozen.

## What this means for a second process

Every decision above is made by reading `args` at import into a module global,
then never again. A process that imports the same ComfyUI tree without the
host's flags will:

- take the **auto-enable** path and land on pytorch attention on NVIDIA, no
  matter what the host chose
- re-enable xformers the host disabled, if the environment can import it
- run fp16 attention the host had upcast

None of that raises. The kernel that runs is a valid kernel; it is simply not
the one the operator picked. [How comfy-env handles it](attention.md) covers
what is carried across and what is not.

## See also

- [How comfy-env handles it](attention.md)
- [The args namespace](comfyui-cli-args.md) — why import-time reads freeze
