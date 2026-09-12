# comfy-env and model paths

*The whole registry crosses the process boundary, so path handling simply
works in a worker. One deliberate exception.*
{: .subtitle }

## ComfyUI background

How ComfyUI's `folder_names_and_paths` registry works is the
subject of its own page, and none of this makes sense without it.

**[Read it first](comfyui-folder-paths.md)**.

**The whole registry crosses**, and this is one of the better-built parts of
the system.

At spawn the parent snapshots `folder_names_and_paths` along with the
input/output/temp/user directories and `base_path`
(`isolation/workers/subprocess.py`), and the worker applies them onto
its own `folder_paths` module before any pack code runs
(`isolation/workers/_persistent_worker.py`).

So `get_full_path`, `get_save_image_path`, `recursive_search` and the rest
simply work in a worker, against the host's real directories, including
everything `extra_model_paths.yaml` contributed. **A pack does not need to
know it is isolated.**

That is worth stating loudly because it is invisible: there is no shim to
notice and no API to call. Authors who assume otherwise reimplement path
plumbing they already have.

## One scalar that did not cross: `models_dir`

**Fixed 2026-09-11** — `models_dir` is now in the snapshot. What follows is
why it was missed, kept because the shape of the mistake is instructive.

The snapshot carried `base_path`, the four working directories and the whole
`folder_names_and_paths` registry — and not `folder_paths.models_dir`.

They are independent after import. `models_dir` is the root the registry was
*built from* (`folder_paths.py`), but once built the registry is a dict of
absolute paths and `models_dir` is just a string nobody recomputes. In a
worker it was set from the worker's own default `base_path` before comfy-env
overrode anything, and the override does not reach it.

So every function that reads the registry — `get_folder_paths`,
`get_filename_list`, `get_full_path` — is correct in a worker, while a pack
reading the attribute directly is not:

```python
os.path.join(folder_paths.models_dir, "mypack")   # stale in a worker
```

On a default layout the stale value happens to equal the host's, so this is
invisible. It bites on `--models-directory`, `--base-directory` and Desktop:
silent duplicate downloads, or "model not found" for a file visibly on disk.

## Live re-listing is separate

`get_filename_list` gets its own treatment, because a frozen list would go
stale the moment a user drops in a new checkpoint. It is shimmed so a combo's
options are resolved by the **parent**, at the moment ComfyUI asks, rather
than captured at scan time. See [Dynamic combos](live-dropdowns.md).

## One deliberate exception

When a pack calls `add_model_folder_path` itself, comfy-env records it in a
**private registry** and never writes it into ComfyUI's global dict
(`isolation/metadata.py`).

The consequence is split:

| | Sees the pack's custom directory |
|---|---|
| The pack's own nodes | **yes** — paths resolve correctly |
| ComfyUI's `/models/<category>` listing | no |
| Upload routing | no |
| The asset seeder | no |

This is the one place where "a pack does not need to know it is isolated"
stops being true. The rationale is recorded in a code comment and nowhere
else, and it has no ADR.

## See also

- [Dynamic combos](live-dropdowns.md) — how `get_filename_list` stays live
- [The process boundary](process-boundary.md) — the rest of what crosses
- [Saved-image metadata](png-metadata.md) — `get_save_image_path` works;
  the metadata that should accompany it currently does not
