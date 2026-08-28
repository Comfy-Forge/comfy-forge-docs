# The aim

ComfyUI loads every node pack into one shared Python environment, and any package installed from the ComfyUI Registry
pip-installs its own requirements into it.
ComfyUI also serves every pack's frontend JavaScript into one shared browser page.
Both designs make collisions **structurally possible**, not bugs to fix but properties of the
architecture, leading to generally poor stability of the platform.

Everything in comfy-forge starts from one aim:

> **Make node packs behave like real software** -- without forking ComfyUI:
>
> 1. **Installable** by non-developers with one click.
> 2. **Isolated** where they would otherwise collide: Python dependencies in
>    their own environments, frontend JavaScript in its own namespace.
> 3. **Tested** the way users actually install them.

Those three justify the whole stack. If a proposed feature serves
none of them, the feature is scope creep; if an architectural choice
conflicts with it, the choice is wrong.

## The thesis

The aim implies a worldview borrowed from operating systems:

- **ComfyUI is the platform.** A host runtime that should be boring and
  stable: one Python, one torch family, the scheduler, the graph executor,
  the extension surface.
- **Node packs are applications.** Real software components with explicit
  contracts -- declared dependencies, declared accelerators, their own
  runtimes when needed -- not bits of Python injected into someone else's
  process.
- **comfy-forge is the runtime and compatibility layer between them.**

The operating rule that falls out:

> **Defaults should be boring; exceptional dependencies should be
> isolated.**

A pack whose deps fit the host's blessed runtime just uses it.
Isolation is the *escape hatch* for components that cannot comfortably inhabit the
platform, and one should NEVER touch the blessed ComfyUI OG runtime.

Crucially, none of this requires owning ComfyUI. The proxy-class design
means ComfyUI never needs to understand any of it: isolated nodes look
like ordinary nodes.

## How the tools serve the aim

| Tool | Serves | Contribution |
|------|--------|--------------|
| [cuda-wheels](cuda-wheels/index.md) | 1 | Nobody compiles anything: prebuilt binaries for the combos users actually have. |
| [comfy-env](comfy-env/index.md) | 1, 2 | Delivery + Python isolation: the escape hatch that makes "exceptional dependencies" safe. |
| [comfy-test](comfy-test/index.md) | 2, 3 | Proof: installs and runs packs the way users do, on the platforms users have -- and today the **JavaScript** half of aim 2 is enforced here, not in comfy-env ([ADR-0031](comfy-env/adr/0031-frontend-javascript-isolation.md) defers it; comfy-test's `javascript` level is the shipped gate). |

## What stands between the current system and that promise

- **Frontend JavaScript.** Isolation stops at the process boundary. A pack's JS
  still runs in one shared browser origin -- one `window`, one `document`, one
  extension-name namespace -- because ComfyUI serves every pack's scripts into
  the same page and there is no per-pack boundary to isolate at. What ships
  instead is containment rather than isolation: comfy-test's `javascript`
  level lints for collisions, and iframe-only bundles stay out of the shared
  realm by being named `.mjs`, which ComfyUI's `**/*.js` scan does not pick
  up. The reasoning is
  [ADR-0031](comfy-env/adr/0031-frontend-javascript-isolation.md); what would
  have to change upstream is
  [Frontend JavaScript isolation](roadmap.md#frontend-javascript-isolation).

- **Whatever a pack's `__init__.py` does on the way in.** ComfyUI does not
  sandbox that file, and neither can comfy-env: `register_nodes()` is called
  **from** it, so the module still executes in ComfyUI's process and only the
  node code moves to a worker. Anything the import does first still lands on
  everybody -- 9% of the top-500 packs mutate `sys.path`, two `pip install`
  into the shared env, one replaces `PromptServer.start`. Isolation makes
  those side effects local for node code, not for the module that registers
  it: [Import-time side effects in the wild](comfy-env/import-side-effects.md).

- **Filesystem (and network) isolation.** A worker's *dependencies* are
  isolated; its *privileges* are not. Node code still runs with the user's
  full account: it can read the home directory, write anywhere, and open
  any network connection -- same as vanilla ComfyUI, where pack code runs
  unsandboxed in the main process itself. Deferred deliberately, not
  overlooked: process isolation is sandboxing's *precondition* (in-process
  code cannot be confined at all), and the path is mapped -- a
  deny-by-default bubblewrap sandbox on Linux (explicit system-path
  allow-list, network off by default, GPU device nodes bound explicitly,
  IPC namespace kept shared so /dev/shm and CUDA IPC keep working), as the
  sibling project pyisolate already demonstrates on this exact
  architecture. The sequencing argument, the anti-RCE transport
  precondition, and why this is not negligence are
  [ADR-0011](comfy-env/adr/0011-isolation-before-sandboxing.md).

- **Model weights.** A pack only runs once its checkpoints are on disk, and
  nothing installs them. `pyproject.toml` even has a `Models` field carrying
  `location` and `model_url` -- but `load_custom_node` never reads it. That
  field is Registry metadata for comfy.org, not an instruction to the running
  server, and comfy-env does not fetch weights either. Between "the install
  succeeded" and "the workflow runs", this is usually what is missing.

- **Name collisions between packs.** Node ids and socket type names are open,
  string-keyed global registries. Two packs that both pick `MESH`, or both
  claim the same node id, are the same thing as far as ComfyUI is concerned,
  and the winner is whichever loaded last. comfy-env cannot fix this -- its
  proxies register into that same dict -- though comfy-test's
  [registration level](comfy-test/levels/registration.md) detects it.

## What the aim implies (working principles)

Consequences we hold ourselves to; a proposal that violates one must
change it here first, in writing.

1. **No user-facing toolchain, no admin.** The user never installs or
   configures a compiler, build tools, or a CUDA toolkit.
   Small scale <1m compilation for some python deps MAY happen on the user's machine,
   but user should do no toolchain setup at all.
3. **The host environment is sacred.** comfy-env never installs anything
   into ComfyUI's env; the only host-side content is `comfy-env` itself.
4. **Never break ComfyUI startup.** Every failure degrades visibly
   (banners, badges, named-reason errors) but ComfyUI always boots
   ([ADR-0008](comfy-env/adr/0008-graceful-degradation-everywhere.md)).
5. **Nodes declare, machines decide.** Requirements are declarations
   (`ACCELERATOR = "cuda"`), never runtime guesswork.
   A node whose declared backend the machine lacks (example: a CUDA node on a Mac) is **hidden from
   the node menu**, but stays *registered* so shared workflows still load, and
   it says why it cannot run if something reaches it
   ([accelerator rule](comfy-env/accelerators.md),
   [ADR-0012](comfy-env/adr/0012-unavailable-nodes-hidden-not-unregistered.md)).
7. **One physical copy per machine.** Cost scales with distinct stacks,
   not with installs or envs, and heavy libraries like torch are hardlinked to avoid wasting disk space.
   ([ADR-0007](comfy-env/adr/0007-machine-wide-workspace-with-per-env-manifests.md)).
8. **Test the way users install.** A fake pass is worse than an honest
   skip.

## The long game (open direction, not a commitment)

If the thesis holds, the clean-sheet contracts deserve their own small
laboratory repo someday (a `comfy-platform/ForgyUI` sketch), where even core-style
nodes could be expressed through the same extension contract.