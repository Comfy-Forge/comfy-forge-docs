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

## What the aim implies (working principles)

Consequences we hold ourselves to; a proposal that violates one must
change it here first, in writing.

1. **No user-facing toolchain, no admin.** The user never installs or
   configures a compiler, build tools, or a CUDA toolkit. Compilation MAY
   happen on the user's machine -- some envs deliver a conda toolchain and
   build small extensions automatically, inside their isolated env, like
   any other dependency. What is forbidden is the user doing toolchain
   setup, anything touching the host, and CUDA kernel builds (the
   nvcc/arch matrix cannot be delivered quietly -- that burden is
   centralized in the wheel farm).
2. **The host environment is sacred.** comfy-env never installs anything
   into ComfyUI's env; the only host-side content is `comfy-env` itself.
3. **Never break ComfyUI startup.** Every failure degrades visibly
   (banners, badges, named-reason errors) but ComfyUI always boots
   ([ADR-0008](comfy-env/adr/0008-graceful-degradation-everywhere.md)).
4. **Nodes declare, machines decide.** Requirements are declarations
   (`ACCELERATOR = "cuda"`), never runtime guesswork. A node whose declared
   backend the machine lacks -- a CUDA node on a Mac, say -- is **hidden from
   the node menu**, but stays *registered* so shared workflows still load, and
   it says why if something reaches it
   ([accelerator rule](comfy-env/accelerators.md),
   [ADR-0012](comfy-env/adr/0012-unavailable-nodes-hidden-not-unregistered.md)).
5. **One physical copy per machine.** Cost scales with distinct stacks,
   not with installs or envs
   ([ADR-0007](comfy-env/adr/0007-machine-wide-workspace-with-per-env-manifests.md)).
6. **Test the way users install.** A fake pass is worse than an honest
   skip.
7. **Accelerator-agnostic in principle, honest in practice.** CUDA is one
   value of an enum; the docs say plainly what is wired today.
8. **Contracts over internals** -- between repos, between processes, and
   in the tests.

## Non-goals

Recorded so they stay decided: VRAM gating (workload-dependent; ComfyUI's
model management owns OOM), scheduling/placement (no consumer exists),
hiding unavailable nodes (breaks shared workflows inscrutably), and
hand-declared hardware capabilities (the wheel farm knows them; typed-in
numbers rot).

## The long game (open direction, not a commitment)

If the thesis holds, the clean-sheet contracts -- pinned platform runtime,
extension manifest, lifecycle, capabilities -- deserve their own small
laboratory repo someday (a `comfy-platform` sketch), where even core-style
nodes could be expressed through the same extension contract. That
experiment can run without replacing anything; the practical tools above
remain the delivery vehicle either way.
