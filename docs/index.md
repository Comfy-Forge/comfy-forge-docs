# comfy-forge

**comfy-forge** is the umbrella name for a family of tools that make ComfyUI
custom nodes with heavy native dependencies installable, isolatable, and
testable on end-user machines:

| Project | What it does | Docs |
|---------|--------------|------|
| [comfy-env](https://github.com/PozzettiAndrea/comfy-env) | Environment management and automatic CUDA wheel resolution for custom nodes: isolated pixi environments, persistent subprocess workers, transparent proxying. | [Architecture](comfy-env/index.md) |
| [comfy-test](https://github.com/PozzettiAndrea/comfy-test) | Installation testing infrastructure: installs a node pack the way a user would, on a real platform matrix (Linux/Windows/macOS, CPU/CUDA, portable/desktop), and publishes results. | [Overview](comfy-test/index.md) |
| [cuda-wheels](https://github.com/PozzettiAndrea/cuda-wheels) | Prebuilt CUDA wheel farm: flash-attn, nvdiffrast, pytorch3d and friends, compiled across the Python x torch x CUDA x OS matrix and served as a pip simple index. | [Overview](cuda-wheels/index.md) |

The three fit together like this: **cuda-wheels** builds the binaries nobody
should have to compile, **comfy-env** delivers and isolates them (and
everything else) on the user's machine, and **comfy-test** proves the whole
thing actually installs and runs -- on every platform ComfyUI users really
have.

## Where to start

- What binds the three tools -- the mission, the eight principles, and the
  recorded non-goals: [Aims & principles](aims.md).

- New to ComfyUI packaging? Read the
  [ComfyUI background](comfy-env/index.md#comfyui-background-for-newcomers)
  primer first.
- Want the "why" behind the design? The
  [comfy-env decision records](comfy-env/adr/index.md).
