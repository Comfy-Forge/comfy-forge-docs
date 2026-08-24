# `setup_env()`

```python
# prestartup_script.py
from comfy_env import setup_env; setup_env()
```

The **launch-time** entry point. ComfyUI core executes each pack's
`prestartup_script.py` on every launch, *before* the server boots and before
any node imports.

Setup_env() is mostly a health report plus environment
hygiene.

## What it does, in order

1. **Enable `faulthandler`**: if native code segfaults later in the run,
   we get a Python traceback on stderr instead of a silent death.

2. **Print the startup banner**: walks the pack for `comfy-env.toml`
   files and reports each isolation env's resolved workspace path and state:

    ```
    [comfy-env] comfyui-motioncapture: 1 isolation env(s):
    [comfy-env]   nodes -> ...\custom_nodes\comfyui-motioncapture\nodes
    [comfy-env]     env: ...\comfy-env\envs\motioncapture-nodes  [OK]
    [comfy-env] prestartup complete
    ```

    `[MISSING -- run install.py]` means the config was discovered but the
    env was never materialized.

3. **`dedupe_libomp()`**: macOS only: symlinks redundant bundled
   `libomp.dylib` copies to torch's canonical one, because multiple loaded
   copies corrupt OpenMP runtime state and segfault inside native filters
   ([ADR-0009](adr/0009-platform-strategy.md)). No-op elsewhere.

4. **Ensure `args.base_directory` is set**: some nodes resolve relative
   paths through ComfyUI's `--base-directory`, if somehow this directory hasn't been filled yet,
   it is filled in from `folder_paths.base_path`.

## What it does NOT do

No pip, no pixi, no network, no writes to the workspace. A missing env is
**reported, not repaired**.

Only [`install()`](install.md) builds envs.