# comfy-env and attention

*The worker follows the host's backend — for the two rungs comfy-env knows
how to name. The other five flags that decide attention are not carried, and
that is the gap.*
{: .subtitle }

## ComfyUI background

How ComfyUI picks a backend, and why the choice freezes at import, is the
subject of its own page.

**[Read it first](comfyui-attention.md)**.

## What crosses

The host does not ship its attention *flags*. It ships its **resolved
backend**, as one synthetic key in the args-mirror payload
(`src/comfy_env/mirrored_args.py:107-116`):

```python
def resolve_host_attention(args):
    if getattr(args, "use_sage_attention", False):  return "sage"
    if getattr(args, "use_flash_attention", False): return "flash"
    return None
```

Two rungs. The reasoning is recorded in the function's docstring and it is
sound: *a host that could import sage but resolved pytorch attention made a
deliberate choice the worker must not upgrade past.* A store-true flag on the
wire cannot express "the host had it available and chose not to" — host-False
looks identical to host-default — so the parent sends the outcome, not the
inputs.

The worker applies it at its own attention site, before its first comfy
import, with an importability check
(`isolation/workers/_persistent_worker.py:1594-1634`): if the host said
`sage` and the worker's env cannot `import sageattention`, it does not fake
it. It logs, and falls through to whatever the worker's own probe finds.

`COMFY_ENV_WORKER_ATTENTION=auto` disables following the host entirely and
restores the worker's own probe — for a pack env that is richer than the host
and *should* diverge.

## What does not cross

`resolve_host_attention` returns `None` for every host that is not on sage or
flash. That is most hosts. And `None` means the worker decides for itself,
which on NVIDIA means the **auto-enable** fires and it lands on pytorch
attention — regardless of what the host did.

| Host flag | In the mirror? | Worker outcome |
|---|---|---|
| `--use-sage-attention` | yes | follows |
| `--use-flash-attention` | yes | follows |
| `--use-pytorch-cross-attention` | **no** | usually coincides, by luck of the auto-enable |
| `--use-split-cross-attention` | **no** | worker on pytorch/SDPA. **OOMs in the pack only** — this is the documented low-VRAM workaround, and it is silently undone |
| `--use-quad-cross-attention` | **no** | same |
| `--disable-xformers` | **no** | worker re-enables xformers if its env can import it — the black images or crash the operator disabled it to avoid come back, in the pack only |
| `--use-ck-attention` | **no** | worker never uses Comfy Kitchen attention |
| `--force-upcast-attention` | **no** | worker runs fp16 attention the host had upcast. **Black images from the isolated pack** while the same model is fine in a host node — the precise symptom the flag exists to cure |
| `--dont-upcast-attention` | **no** | the reverse |

None of these seven is in the allowlist, and none is in the allowlist's stated
non-mirror rationale either. They were not rejected; they were not considered.
The mirror was built around dtype and memory, and attention landed in it only
as far as the two rungs a worker might not be able to import.

Each is one line to add to `MIRRORED_ARGS`. The synthetic `attention` key
would then be redundant for everything except the importability check, which
is worth keeping.

## Running a pack on a different backend than the host

Sometimes divergence is the point: a pack ships a faster kernel the host lacks,
or a model that needs upcast on a host running without it. The machine-global
`COMFY_ENV_WORKER_ATTENTION=auto` covers the first case crudely — every worker
diverges, not the one that needs to.

The per-pack override proposed in [the mirror page](args-mirror.md#proposed-a-per-pack-override)
would cover both precisely:

```toml
[options.comfyui_args]
attention = "quad"              # the full ladder, not just sage/flash
force_upcast_attention = true
```

Applied after the host mirror and before the worker's first comfy import, so
the pack's value wins for its own worker and nobody else's. Not built yet.

## See also

- [The backend ladder](comfyui-attention.md) — how upstream decides
- [How comfy-env mirrors it](args-mirror.md) — the mechanism this rides on, and the override proposal
