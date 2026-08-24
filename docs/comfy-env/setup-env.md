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

    A `[MISSING]` status means the config was discovered but the env was
    never materialized. That line names the pack and the exact command,
    because the header above it scrolls away once several packs report:

    ```
    [comfy-env]     env: ...  [comfyui-motioncapture: MISSING -- run
    `comfy-env install --dir .../custom_nodes/comfyui-motioncapture`]
    ```

3. **`dedupe_libomp()`**: macOS only: symlinks redundant bundled
   `libomp.dylib` copies to torch's canonical one, because multiple loaded
   copies corrupt OpenMP runtime state and segfault inside native filters
   ([ADR-0009](adr/0009-platform-strategy.md)). No-op elsewhere.

## What it does NOT do

No pip, no pixi, no network, no writes to the workspace. A missing env is
**reported, not repaired**.

It also **does not patch ComfyUI's globals**. Until 0.4.27 it filled in
`comfy.cli_args.args.base_directory` from `folder_paths.base_path` when the
user had not passed `--base-directory`. That was deleted: `None` there is
information -- "the user did not ask to relocate the base" -- and nodes read
it guarded (`if ... and args.base_directory`), so filling it in could never
prevent a crash. It could only flip that branch on, silently moving a
relative path from the CWD to the ComfyUI base. Identical when ComfyUI is
launched from its own directory, different under a service.

Only [`install()`](install.md) builds envs.