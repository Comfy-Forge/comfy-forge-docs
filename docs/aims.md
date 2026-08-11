# The aim

ComfyUI loads every node pack into one shared Python environment, and tells
each pack to pip-install its own requirements into it. That design makes
dependency conflicts **structurally possible** -- not a bug to fix but a
property of the architecture. Everything in comfy-forge starts from one
aim:

> **Make node packs behave like real software**: installable by
> non-developers with one click, isolated when their dependencies demand
> it, and tested the way users actually install them -- without forking
> ComfyUI.

That single sentence justifies the whole stack. If a proposed feature does
not serve it, the feature is scope creep; if an architectural choice
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

A pack whose deps fit the host's blessed runtime just uses it. Isolation
is the *escape hatch* for components that cannot comfortably inhabit the
platform (a different Python for `bpy`, a conda-only native stack, a
conflicting torch) -- not a fashion applied to everything.

Crucially, none of this requires owning ComfyUI. The proxy-class design
means ComfyUI never needs to understand any of it: isolated nodes look
like ordinary nodes. That is leverage -- the strategy is to make the
substrate so good that ComfyUI effectively becomes the graph host sitting
on top of it, not to fork the host (and certainly not torch). A fork
becomes justified only if upstream ever structurally blocks isolation;
until then, compatibility is the weapon.

## How the tools serve the aim

| Tool | Contribution |
|------|--------------|
| [cuda-wheels](cuda-wheels/index.md) | Nobody compiles anything: prebuilt binaries for the combos users actually have. |
| [comfy-env](comfy-env/index.md) | Delivery + isolation: the escape hatch that makes "exceptional dependencies" safe. |
| [comfy-test](comfy-test/index.md) | Proof: installs and runs packs the way users do, on the platforms users have. |

## What the aim implies (working principles)

Consequences we hold ourselves to; a proposal that violates one must
change it here first, in writing.

1. **No compiler, no toolkit, no admin** on the user's machine, ever.
2. **The host environment is sacred.** comfy-env never installs anything
   into ComfyUI's env; the only host-side content is `comfy-env` itself.
3. **Never break ComfyUI startup.** Every failure degrades visibly
   (banners, badges, named-reason errors) but ComfyUI always boots
   ([ADR-0008](comfy-env/adr/0008-graceful-degradation-everywhere.md)).
4. **Nodes declare, machines decide.** Requirements are declarations
   (`ACCELERATOR = "cuda"`), never runtime guesswork; unavailable nodes
   stay visible and explain themselves
   ([accelerator rule](comfy-env/accelerators.md)).
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
