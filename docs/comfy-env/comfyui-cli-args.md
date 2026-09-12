# The args namespace

*ComfyUI has one `argparse` namespace and reads it from module level, at
import, all over the codebase. That is what makes it hard to carry into a
second process — and why "the worker uses the same flags" is not automatic.*
{: .subtitle }

## One namespace, read everywhere

Every `--flag` you pass to `main.py` lands on a single object:

```python
from comfy.cli_args import args
```

`comfy/cli_args.py` declares **107** distinct destinations. They are not read
in one place. They are read wherever they are needed, and a large share of
them are read **at import time**, into module-level globals that never change
again:

```python
# comfy/model_management.py
ENABLE_PYTORCH_ATTENTION = False
if args.use_pytorch_cross_attention:
    ENABLE_PYTORCH_ATTENTION = True
    XFORMERS_IS_AVAILABLE = False
```

Once `comfy.model_management` has been imported, `ENABLE_PYTORCH_ATTENTION` is
whatever it was at that instant. Setting `args.use_pytorch_cross_attention`
afterwards changes nothing.

## The families, and when each is read

| Family | Examples | When read | Where it lands |
|---|---|---|---|
| **dtype and numerics** | `--fp8_e4m3fn-unet`, `--bf16-vae`, `--force-fp16`, `--fast` | at import | `comfy.model_management` module globals |
| **memory behaviour** | `--disable-smart-memory`, `--async-offload`, `--disable-pinned-memory`, `--highvram` | at import | same |
| **attention backend** | `--use-sage-attention`, `--use-quad-cross-attention`, `--disable-xformers` | at import | `model_management` and `comfy.ldm.modules.attention` |
| **allocator / compiler** | `--cuda-malloc`, `--disable-cuda-graphs`, `--disable-comfy-compiler` | at import, or per-op | `cuda_malloc.py`, `comfy/ops.py`, `model_prefetch.py` |
| **device** | `--cuda-device`, `--cpu`, `--directml` | at import, some via env var | `CUDA_VISIBLE_DEVICES`, `model_management` |
| **paths** | `--base-directory`, `--models-directory`, `--output-directory` | at import | `folder_paths` module globals |
| **executor** | `--cache-lru`, `--cache-none`, `--preview-method` | per prompt | `main.py`, `execution.py` |
| **server** | `--listen`, `--port`, `--enable-cors-header`, `--max-upload-size` | at startup | `server.py` |
| **logging** | `--verbose`, `--log-stdout` | at startup | `app/logger.py` |

The first four families are the ones that matter for a second process,
because their effect is **frozen at import** and reaches every tensor operation
that follows.

## A second process parses nothing

`main.py` is the only entry point that turns on argument parsing:

```python
comfy.options.enable_args_parsing() # main.py
```

Any other process that imports `comfy.cli_args` — a test, a script, a worker —
gets `args_parsing = False` and parses an **empty argv**
(`comfy/cli_args.py`). Every one of the 107 flags resolves to its
default.

So a host started with `--fp8_e4m3fn-unet` and a worker importing the same
ComfyUI tree disagree about dtype, silently, from the first import onwards.
The worker is not wrong about anything; it simply never heard the flag.

## Why this shape, and what it costs

Module-level reads are fast and simple, and for one process they are entirely
correct — the flags never change after startup, so there is nothing to
re-read. The cost only appears with a second process: there is no function to
call to "apply the host's settings", because there is no moment after import
at which applying them would do anything.

Any faithful second process therefore has to **set the values before its first
comfy import**, and has to know which values matter. That is the whole problem
[the mirror](args-mirror.md) exists to solve.

## See also

- [How comfy-env mirrors it](args-mirror.md) — what crosses, what doesn't, and why
