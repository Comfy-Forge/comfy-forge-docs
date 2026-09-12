# The model path registry

*`folder_paths.py` answers every "where does this file live" question ComfyUI
has: thirty-odd model categories, the four working directories, and the rules
deciding whether a path is allowed to be opened at all.*
{: .subtitle }

## What `folder_paths.py` is

One module, 589 lines, and the only thing it imports from ComfyUI is
`comfy.cli_args`.

Every question of the form *"where does this file live"* is
answered here and nowhere else, which is why it is imported before the server
starts and by essentially every node that touches disk.

It is not only a lookup table. It keeps four different kinds of state.

<div class="wide-table num-col" markdown>

| # | What it keeps | ELI5 | Variation |
|---|---|---|---|
| 1 | **The model registry** | Ask it for `loras` and it tells you which folders to search and which file extensions count as one. | `<base>/models/<name>` for 25 of the 27 categories; `custom_nodes` and `datasets` sit directly under `<base>`. `--models-directory` moves the models root on its own, and `extra_model_paths.yaml` adds further folders to any category, or registers a new one. |
| 2 | **The four working directories** | `<base>/input` — what you upload.<br>`<base>/output` — what gets saved.<br>`<base>/temp` — previews and scratch, **wiped at every startup**.<br>`<base>/user` — `users.json`, the `comfyui.db` SQLite file, your settings and saved workflows. | This is where installs actually diverge. Manual clone and Windows portable: `<base>` is the folder holding `main.py`, so all four sit beside the code. **Desktop: `<base>` is the user data folder — `~/Documents/ComfyUI` by default, chosen at first run — while the code stays in the app bundle.** Each of the four is separately overridable, and `--temp-directory` appends `temp` to whatever you pass it. |
| 3 | **Two caches** | Remembers what is on disk, so opening a model dropdown does not re-scan every folder. One stores each folder's **mtime** — the timestamp the filesystem stamps when entries are added, deleted or renamed — and throws the list away the moment any stamp moves. The other lives for a single request. | Memory only. Coarse mtime on FAT32/exFAT (1–2 s) can briefly hide a new file. |
| 4 | **The file-access safety rules** | Every filename in a workflow is user input. This is what stops a `LoadImage` widget set to `../../../.ssh/id_rsa` from opening it, and stops an `evil.html` uploaded to `input/` from **running** when someone opens it through `/view` instead of downloading. | Both are bypass risks rather than cosmetics. One is Windows-only: a path on `C:` cannot be compared against an output folder on `D:` — `commonpath` raises, and that is treated as *outside*. The other varies by platform everywhere: `guess_type("x.js")` returns `text/javascript` on some platforms and `application/javascript` on others, so the blocklist carries both spellings; one missing spelling is a way through. |

</div>

Row 4 is a third of the module, and it is not defensive
hypothesising: it is the fix for **GHSA-779p-m5rp-r4h4**, a real advisory whose
numbered fixes each have their own regression test in
`tests-unit/security_test/` — preview traversal, annotated-path traversal,
userdata XSS, dangerous content types, and the SVG exemption. The checks are
centralised in `folder_paths` precisely so the three endpoints that serve
user-controlled files (`/view`, `/userdata`, the assets download route) cannot
drift apart. A change there is a security change, not a bookkeeping one.

The two caches in row 3 are unrelated to each other despite living side by
side: `filename_list_cache` is the long-lived one keyed on folder
mtimes, while `CacheHelper` is a context manager that only holds
results while something has entered it, and clears on exit.

## What ComfyUI registers, and when

The registry is not filled in one go. It grows in three phases:

<div class="num-col" markdown>

| # | Phase | What lands |
|---|---|---|
| 1 | **Import** of `folder_paths` | 27 categories: 25 rooted at `models_dir`, and `custom_nodes` + `datasets` rooted at `base_path` |
| 2 | **`main.py` startup**, `apply_custom_paths()` | first any `extra_model_paths.yaml` and `--extra-model-paths-config` files, then five output subdirectories appended to `checkpoints`, `clip`, `vae`, `diffusion_models`, `loras` so `CheckpointSave` has somewhere to write |
| 3 | **Any time after** | a custom node calling `add_model_folder_path` at import |

</div>

Of the 27 registered at import, **22 share one extension set**
(`supported_pt_extensions` — `.ckpt`, `.pt`, `.pt2`, `.bin`, `.pth`,
`.safetensors`, `.pkl`, `.sft`). The five exceptions say something about what
a "category" can be:

| Category | Extensions | Meaning |
|---|---|---|
| `configs` | `[".yaml"]` | text, not weights |
| `diffusers` | `["folder"]` | a placeholder `folder_paths` never interprets: no filename ends in `.folder`, so `get_filename_list("diffusers")` is always empty. The directory semantics live in `nodes.py` — `DiffusersLoader` walks `get_folder_paths("diffusers")` looking for a `model_index.json` |
| `classifiers` | `{""}` | extensionless files only |
| `custom_nodes`, `datasets` | `set()` | empty set means **accept everything** |

## Where the roots come from

Two values are computed at import and everything else hangs off them
:

```python
base_path  = os.path.abspath(args.base_directory) if args.base_directory \
             else os.path.dirname(os.path.realpath(__file__))
models_dir = os.path.abspath(args.models_directory) if args.models_directory \
             else os.path.join(base_path, "models")
```

The default for `base_path` is **the location of `folder_paths.py` itself**,
resolved through `realpath`. That is what lets a git clone, a Windows portable
build and the Desktop app all self-locate with no configuration — and it means
a symlinked ComfyUI directory resolves to its real home, not the link.

Every root is individually overridable, and the working directories override
the base rather than deriving from it:

| Flag | Moves |
|---|---|
| `--base-directory` | models, custom_nodes, input, output, temp, user — all at once |
| `--models-directory` | just the models root, overriding `--base-directory` |
| `--output-directory`, `--input-directory`, `--temp-directory`, `--user-directory` | one working directory each, overriding `--base-directory` |
| `extra_model_paths.yaml` | adds directories to existing categories, or creates a new category (with an empty extension set) for a name it has not seen — it goes through `add_model_folder_path`; `~` and environment variables are expanded (`utils/extra_config.py`) |

## What actually differs between operating systems

Most of the module is platform-neutral because it goes through `os.path`. The
places where the platform is visible in behaviour, rather than just in
separators:

<div class="wide-table" markdown>

| Behaviour | Windows | Linux / macOS | Why |
|---|---|---|---|
| **The output filename counter** | `IMG_00001_.png` and `img_00001_.png` collide, so the counter continues across both | they are different files and each gets its own counter | `os.path.normcase` lowercases on Windows and is a no-op on POSIX |
| **Containment check across drives** | comparing `C:\...` with `D:\...` raises `ValueError`, which is caught and treated as *outside* | not reachable | upstream's own comment says so |
| **MIME type spelling** | `guess_type` may return `text/javascript` **or** `application/javascript` | same variance | why `DANGEROUS_CONTENT_TYPES` lists both spellings |
| **Subfolder paths in API responses** | `\` is rewritten to `/` before the list is returned | already `/` | `rel_path.replace(os.sep, '/')` |
| **Symlinked model directories** | followed, but creating one needs privilege or developer mode | followed | `os.walk(..., followlinks=True)` |
| **Extension matching** | case-insensitive | **also** case-insensitive | `filter_files_extensions` lowercases before comparing, so `.SAFETENSORS` works everywhere |

</div>

The last row is worth separating from the rest: extension matching is
case-insensitive *by ComfyUI's choice*, not by the filesystem's. The
**filename** is still whatever the filesystem says, so a workflow saved on
Windows referencing `Model.safetensors` can fail to resolve on Linux against
`model.safetensors` — `get_full_path` does a plain `os.path.isfile`
and inherits the filesystem's opinion.

## The registry itself

Every model directory lives in one module-global dict
(`folder_paths.py`):

```python
folder_names_and_paths: dict[str, tuple[list[str], set[str]]] = {}
```

A **category name** maps to a tuple of *(list of directories, set of allowed
extensions)*. Roughly thirty categories are registered at import —
`checkpoints`, `loras`, `vae`, `controlnet`, `text_encoders`,
`diffusion_models` and so on — each pointing at one or more real folders:

```python
folder_names_and_paths["loras"]      = ([os.path.join(models_dir, "loras")], supported_pt_extensions)
folder_names_and_paths["controlnet"] = ([os.path.join(models_dir, "controlnet"),
                                         os.path.join(models_dir, "t2i_adapter")], supported_pt_extensions)
```

Three of the 27 carry two directories rather than one — `text_encoders`,
`diffusion_models` and `controlnet`. That is not an accident of tidiness: it
is how ComfyUI absorbs a rename without breaking saved workflows. `unet` →
`diffusion_models` and `clip` → `text_encoders` both kept the old folder in
the list, so a model already on disk keeps resolving. `map_legacy`
handles the other half, translating an old *category name* a node still asks
for into the current one.

## The surface a node actually uses

| Function | Returns |
|---|---|
| `get_folder_paths(name)` | a **copy** of the directory list |
| `get_filename_list(name)` | every file in every directory for that category, filtered by extension, sorted |
| `get_full_path(name, filename)` | absolute path, or `None`; `get_full_path_or_raise` for the loud version |
| `get_save_image_path(prefix, out_dir, w, h)` | output folder, filename stem and the next counter — this is what makes `ComfyUI_00017_.png` |
| `add_model_folder_path(name, path, is_default=False)` | registers a new directory, or a whole new category |

`get_filename_list` is the one that shows up in `INPUT_TYPES`, because a
combo widget of available checkpoints is literally
`("checkpoints", folder_paths.get_filename_list("checkpoints"))`.

It is **cached with mtime validation**: the cache stores each
folder's modification time and is discarded when any of them changes, so
dropping a file into `models/loras` shows up on the next graph refresh
without a restart.

## Adding a directory

`extra_model_paths.yaml` is the declarative route, and it is how one model
library serves several installs — the same 200 GB of checkpoints pointed at
by a git clone, a portable build and the Desktop app at once.

`add_model_folder_path` is the programmatic version, with one
subtlety worth knowing: **`is_default=True` inserts at the front** of the
directory list, and the front is what most "where do I write this" logic
picks. Appending is the safe default; inserting changes where new files land.

## See also

- [comfy-env and model paths](folder-paths.md) — what happens to all of
  this once the node runs in another process
