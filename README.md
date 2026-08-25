# comfy-forge-docs

Documentation site for the **comfy-forge** tool family:

- [comfy-env](https://github.com/PozzettiAndrea/comfy-env) -- environment
  management and CUDA wheel resolution for ComfyUI custom nodes
- [comfy-test](https://github.com/PozzettiAndrea/comfy-test) -- installation
  testing infrastructure for ComfyUI custom nodes
- [cuda-wheels](https://github.com/PozzettiAndrea/cuda-wheels) -- prebuilt
  CUDA wheel farm

Built with [MkDocs Material](https://squidfunk.github.io/mkdocs-material/);
diagrams are Mermaid, so they also render directly on GitHub.

Live site: https://docs.comfy-forge.org/

## Serve locally

```bash
python -m venv .venv
.venv/Scripts/pip install "mkdocs-material==9.*" "mkdocs<2"   # Windows
# .venv/bin/pip install "mkdocs-material==9.*" "mkdocs<2"     # Unix
serve            # default port 8001
serve 8080       # or pick one
```

`serve.cmd` wraps `mkdocs serve --dev-addr 0.0.0.0:<port>`; plain
`mkdocs serve` also defaults to 8001 via `dev_addr` in `mkdocs.yml`.
Then open http://127.0.0.1:8001

## Publish

Pushing to `main` triggers `.github/workflows/deploy.yml`, which builds the
site and deploys it to GitHub Pages (Pages source: "GitHub Actions").

## Layout

- `docs/index.md` -- comfy-forge umbrella landing page
- `docs/comfy-env/` -- architecture overview, module inventory, ADRs
- `docs/comfy-test/` -- overview
- `docs/cuda-wheels/` -- overview
