# `setup_env()`

```python
# prestartup_script.py
from comfy_env import setup_env; setup_env()
```

The **launch-time** entry point. ComfyUI core executes each pack's
`prestartup_script.py` on every launch, *before* the server boots and before
any node imports -- the only moment where the process environment can still
be fixed. `setup_env()` deliberately installs nothing (that is
[`install()`](install.md)'s job): it is a health report plus environment
hygiene.

Source: `src/comfy_env/environment/setup.py:135`. `node_dir` defaults to the
caller's directory via `inspect.stack()`, same trick as the other two calls.

## What it does, in order

1. **Enable `faulthandler`** -- if native code segfaults later in the run,
   you get a Python traceback on stderr instead of a silent death.

2. **Print the startup banner** -- walks the pack for `comfy-env.toml`
   files and reports each isolation env's resolved workspace path and state:

    ```
    [comfy-env] comfyui-motioncapture: 1 isolation env(s):
    [comfy-env]   nodes -> ...\custom_nodes\comfyui-motioncapture\nodes
    [comfy-env]     env: ...\comfy-env\envs\motioncapture-nodes  [OK]
    [comfy-env] prestartup complete
    ```

    `[MISSING -- run install.py]` means the config was discovered but the
    env was never materialized.

3. **Stop here if isolation is disabled** (`COMFY_ENV_ISOLATE`, default on)
   -- the banner still prints, nothing else happens.

4. **`dedupe_libomp()`** -- macOS only: symlinks redundant bundled
   `libomp.dylib` copies to torch's canonical one, because multiple loaded
   copies corrupt OpenMP runtime state and segfault inside native filters
   ([ADR-0009](adr/0009-platform-strategy.md)). No-op elsewhere.

5. **Optionally register the shareable-CUDA-pool hook**
   (`COMFY_ENV_PATCH_SHAREABLE_POOL`, default **off**). This is the
   parent-side half of Pool IPC
   ([ADR-0005](adr/0005-tiered-tensor-serialization.md)): a `sys.meta_path`
   import hook that fires exactly when ComfyUI imports
   `comfy.model_management`, then creates a shareable CUDA memory pool and
   patches `get_free_memory` / `get_total_memory` /
   `torch.cuda.empty_cache` to account for it. The import-hook shape exists
   because at prestartup time `comfy.model_management` does not exist yet --
   this is the only way to patch it at birth without monkey-patching
   ComfyUI's source.

6. **Ensure `args.base_directory` is set** -- some nodes resolve relative
   paths through ComfyUI's `--base-directory`; if the user did not pass it,
   it is filled in from `folder_paths.base_path`.

## What it does NOT do

No pip, no pixi, no network, no writes to the workspace. A missing env is
reported, not repaired -- unless the user has opted into
`COMFY_ENV_AUTO_INSTALL`, which is handled later by
[`register_nodes()`](register-nodes.md), not here.
