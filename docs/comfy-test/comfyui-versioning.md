# ComfyUI versioning

> What `comfyui_version` actually pins, why the number in the boot log can be
> wrong, and why ComfyUI Desktop has no ComfyUI version at all.

comfy-test records two fields for the ComfyUI under test: `comfyui_version`
and `comfyui_commit`. That looks redundant. It is not, and the reason is the
subject of this page.

Everything below was checked against the live repositories in **August 2026**.
Version numbers are snapshots; the *mechanisms* are the durable part.

## Core: the number is a file, not the git state

ComfyUI's version lives in `pyproject.toml`, and a CI job mirrors it into
`comfyui_version.py`. That constant is what `main.py` prints at boot and what
`/system_stats` returns as `comfyui_version`.

It is **not derived from git**. Nothing checks it against the checked-out
commit, so a tree can report any version its last release commit left behind.

!!! danger "On `master`, the version constant is stale by design"
    Checked live in August 2026:

    | | version constant | newest release |
    |---|---|---|
    | `master` | `0.33.0` | -- |
    | tag `v0.33.4` | `0.33.4` | v0.33.4 |

    `master` said **0.33.0** while four newer patch releases existed. This is
    not a bug: patch releases are cut on `release/vX.Y` branches, and the
    version bump commit lands **there**, never on master.

    So a run that reports "ComfyUI 0.33.0" may be a tree built from master
    days after v0.33.4 shipped. **The version string does not identify the
    code.** Only the commit does -- which is why comfy-test records
    `comfyui_commit` alongside it, and why that field is the one to quote in
    a bug report.

### Patches live on release branches

`vX.Y.0` is cut by hand on master. `vX.Y.Z` for Z above zero is cut by
automation onto `release/vX.Y` as a cherry-pick, and is **not an ancestor of
master**.

The two lines genuinely diverge. A live comparison of master against the
newest tag showed master **23 commits behind and 53 ahead** simultaneously.

!!! warning "\"Latest\" is not \"newest\""
    Tracking master does not give you the newest tested code. It gives you
    untested new work *and* omits fixes that only ever landed on the release
    branch. Neither line is a superset of the other.

### "Patch release" carries no size guarantee

Patch bumps have ranged from three changed lines to several thousand. Some
patch releases contain nothing but a dependency bump -- a release whose entire
content was swapping the pinned frontend package, changing the UI without
touching a line of Python.

Minor bumps have carried breaking changes too, including a raised minimum
supported PyTorch. **Neither digit is a compatibility contract.** Read the
diff, not the number.

### The GitHub Releases page is not the version list

A large share of recent tags have no GitHub Release object at all -- at the
time of checking, including the four newest. A project watching the Releases
page was several patch versions behind reality.

Use the tags:

```bash
git ls-remote --tags https://github.com/comfyanonymous/ComfyUI.git \
  | grep -oP '(?<=refs/tags/)v\d+\.\d+\.\d+$' | sort -V | tail -5
```

!!! danger "There is a git tag literally named `latest`. Do not use it."
    It is real, it is on the remote, and it points at a commit from
    **2023-05-15**:

    ```
    2ec6d1c6...  2023-05-15  Don't import custom nodes when the folder ends with .disabled
    ```

    `git checkout latest` silently gives you a three-year-old ComfyUI.

    comfy-test is safe from this by construction: `comfyui_version = "latest"`
    is translated to `HEAD` before git ever sees it, so the tag is
    unreachable through the config. A test guards that translation
    (`tests/test_comfyui_version_ref.py`), because the day someone "simplifies"
    it into passing the string through is the day every run silently tests
    2023.

## What a core tag does not pin

Pinning ComfyUI to a tag fixes the Python source tree. It does **not** fix
what that tree installs at runtime.

`requirements.txt` exact-pins several first-party packages, and they carry
much of the user-visible behaviour:

| Package | What it is |
|---|---|
| `comfyui-frontend-package` | the entire web UI, developed in a separate repo |
| `comfyui-workflow-templates` | the built-in template workflows |
| `comfyui-embedded-docs` | the in-app node documentation, served at `/docs` |
| `comfy-kitchen`, `comfy-aimdo` | first-party runtime packages |

Those pins are *per-commit*, so a given core commit does name one exact
frontend. But two things loosen it:

- the **installed** version is whatever is in the venv, which can be newer if
  anything ran an upgrade -- core compares the two at boot and warns on
  mismatch rather than failing
- a launch flag can override the frontend version entirely

And the rest of `requirements.txt` -- torch, transformers, and friends -- is
floors and ranges, not pins. **A core tag is reproducible for the UI layer and
not for the ML stack.**

!!! note "A core patch can move the frontend under you"
    Frontend pin changes across in-series patch releases are common, not
    exceptional. One observed patch release consisted of *only* a frontend
    bump; another was a template **downgrade**, to bring back templates a
    previous release had broken.

    If you pin core and care about the UI, freeze the venv too.

The frontend has its own complication: several minor lines are maintained
concurrently, so a higher frontend version is not necessarily newer in
wall-clock terms. Its PyPI stream is also a subset of its GitHub releases --
core consumes PyPI, so the frontend's Releases page does not tell you what
core can pin.

There is **no pip-installable ComfyUI core**. The PyPI name `comfyui` is an
unrelated stub. A git ref is the only way to pin core.

## Desktop: the answer changed in mid-2026

This is the part that surprises people, so it is worth stating the conclusion
first: **a current ComfyUI Desktop version tells you nothing about which
ComfyUI core it will run.**

There are two Desktop repositories, and they answer the question oppositely.

| | Legacy Desktop (archived) | Current Desktop |
|---|---|---|
| Version scheme | `0.8.x` / `0.9.x` | `1.0.x` |
| Pins a core version? | **yes, exactly** | **no** |
| Where the pin lives | a `config.comfyUI.version` field in `package.json` | nowhere |
| How core is obtained | shallow clone of that release tag, baked into the build | fetched at runtime; the user picks a channel |
| Published in release notes? | yes, verbatim ("Bump bundled ComfyUI to ...") | no -- the notes never mention core |

The legacy repository is **archived** and superseded. Its last release bundled
a core version that is now many minors behind.

The current Desktop is a launcher and environment manager, not a bundle. It
offers a channel choice -- a tested stable release, or master -- and supports
updating core in place afterwards. So:

- the core version is a property of **an installation**, not of a Desktop build
- two machines on the same Desktop version can be running different cores
- there is no table to look it up in, because it is a per-user runtime choice

To find out what a Desktop install is actually running, ask the server:
`GET /system_stats` gives `comfyui_version`. Bear in mind the caveat at the
top of this page -- on a master-channel install that number is the stale
constant, not the release.

## What this means for comfy-test

!!! warning "`comfyui_version` does nothing on desktop lanes"
    The desktop lanes install the real application from the official download
    endpoint and drive it as an app
    ([ADR-0013](adr/0013-desktop-is-driven-over-cdp.md)). That path **never
    clones ComfyUI core**, so there is nothing for `comfyui_version` to
    control. It is read back from the running app for provenance and is
    otherwise ignored.

    A pack pinning `comfyui_version` and enabling a desktop lane is not
    testing the pinned version there. Read `provenance.comfyui_version` from
    that lane's results to see what it actually ran.

For every other lane, `comfyui_version` selects the ref that gets fetched, and
all four forms cost the same -- see
[pinning ComfyUI to a commit](config.md#pinning-comfyui-to-a-commit).

### What to pin, and what it buys you

| Identifier | Reliable? | Notes |
|---|---|---|
| A core **commit SHA** | **yes** | Immutable and unambiguous. The only identifier that survives everything on this page. |
| A core **tag** (`v0.33.4`) | yes | Immutable. Prefer it for readability; resolve it to a SHA when you need to compare runs. |
| `"latest"` | no | Master's HEAD -- a different commit every day, reporting a stale version number. Fine for a nightly, wrong for a bisect. |
| The boot-log **version string** | **no** | Stale on master. Never use it alone to identify a build. |
| A **Desktop** version | no | Says nothing about core in the 1.0.x era. |
| The git tag `latest` | **never** | Points at 2023. |

The practical rule: **pin a tag for readability, quote the commit for truth.**
comfy-test records both, and `provenance.comfyui_commit` is the field that
answers "what did this run actually test".

## See also

- [`comfy-test.toml` reference](config.md) -- the `comfyui_version` key
- [Reproducibility](reproducibility.md) -- reproducing a specific run
- [ADR-0013](adr/0013-desktop-is-driven-over-cdp.md) -- why desktop is driven
  as an application
