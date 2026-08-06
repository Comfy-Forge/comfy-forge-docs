# comfy-env-architecture-docs

Architecture overview, diagrams, and decision records (ADRs) for
[comfy-env](https://github.com/PozzettiAndrea/comfy-env) -- the environment
management and CUDA wheel resolution system for ComfyUI custom nodes.

Built with [MkDocs Material](https://squidfunk.github.io/mkdocs-material/);
diagrams are Mermaid, so they also render directly on GitHub.

## Serve locally

```bash
python -m venv .venv
.venv/Scripts/pip install mkdocs-material     # Windows
# .venv/bin/pip install mkdocs-material       # Unix
.venv/Scripts/mkdocs serve
```

Then open http://127.0.0.1:8000

## Publish

Pushing to `main` on GitHub triggers `.github/workflows/deploy.yml`, which
builds the site and deploys it to GitHub Pages (set the repo's Pages source
to "GitHub Actions").

## Layout

- `docs/index.md` -- architecture overview with system, layering, build-time
  and runtime diagrams
- `docs/modules.md` -- per-module inventory of `src/comfy_env`
- `docs/adr/` -- numbered decision records (Nygard format)
