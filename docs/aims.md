# Aims & principles

comfy-forge exists so that **heavy native and CUDA node packs are
installable, runnable, and testable on ordinary end-user machines** -- the
hobbyist Windows box, the ComfyUI Desktop app, the headless Linux server --
without asking the user to be a developer.

Everything else on this site is a consequence of that aim. The three tools
divide it cleanly: [cuda-wheels](cuda-wheels/index.md) builds the binaries
nobody should have to compile, [comfy-env](comfy-env/index.md) delivers and
isolates them on the user's machine, and [comfy-test](comfy-test/index.md)
proves the whole thing installs and runs the way a real user would run it.

## The principles

These bind all three repos. Design arguments end here: a proposal that
violates one of these needs to change the principle first, in writing.

**1. No compiler, no toolkit, no admin.** An end user installs a node pack
with one click or one `git clone`. Anything that requires nvcc, MSVC, a
CUDA toolkit, or administrator rights on the user's machine is a defect.
(This is why the wheel farm exists, why pixi self-bootstraps per-user, and
why the workspace lives in user-writable locations.)

**2. The host environment is sacred.** comfy-env never installs anything
into ComfyUI's own environment. The host env's only comfy-env-related
content is `comfy-env` itself -- CUDA wheels, conda packages, and pip
dependencies all live in isolated envs. There is no config key, and there
will be no config key, that installs a library into the host.

**3. Never break ComfyUI startup.** Every failure path ends in "ComfyUI
still boots": missing env -> in-process import; failed auto-install ->
boot anyway; unreachable index -> fallback route; crashed worker ->
restart. Degradation must be *visible* (banners, badges, named-reason
errors) but never fatal to the app
([ADR-0008](comfy-env/adr/0008-graceful-degradation-everywhere.md)).

**4. Nodes declare, machines decide.** Capability requirements are
declarations on the code (`ACCELERATOR = "cuda"`), not runtime guesswork --
because import success proves nothing about GPUs. A machine that lacks a
declared backend registers the node visibly and refuses it with a named
reason at use, never by hiding it
([accelerator rule](comfy-env/accelerators.md)).

**5. One physical copy per machine.** The workspace is machine-global;
identical packages hardlink to shared caches; workers mapping the same
binaries share code pages. Disk and RAM cost scale with the number of
*distinct stacks*, not the number of ComfyUI installs or envs
([ADR-0007](comfy-env/adr/0007-machine-wide-workspace-with-per-env-manifests.md)).

**6. Test the way users install.** comfy-test builds a fresh env, clones
real ComfyUI, installs the pack as a user would, and drives real workflows
-- on the actual platform matrix users have, including Windows portable and
the Desktop app. A fake pass is worse than an honest skip: CPU lanes skip
what they cannot execute rather than mock it into a green checkmark.

**7. Accelerator-agnostic in principle, honest in practice.** The designs
carry no CUDA assumptions the vocabulary can't extend past ("cuda" is one
value of a backend enum; the wheel index is one entry in a registry; a
rocm-wheels repo mirrors cuda-wheels when hardware exists to test it). But
the docs say plainly what is wired end-to-end today and what is not.

**8. Contracts over internals.** Between repos (comfy-test asks comfy-env,
never reaches into its layout), between processes (the wire protocol is the
contract, round-trip equality is the test), and in the test suite
(promises, not snapshots -- a test that knows implementation details is a
maintenance bug).

## Non-goals

Recorded so they stay decided:

- **VRAM gating** -- workload-dependent; any static number is wrong.
  Runtime OOM handling belongs to ComfyUI's model management.
- **Scheduling / placement** -- there is no multi-accelerator scheduler to
  feed; we don't design for consumers that don't exist.
- **Hiding unavailable nodes** -- a missing node type breaks shared
  workflows inscrutably; unavailable nodes stay visible and explain
  themselves.
- **Hand-declared hardware capabilities** (min compute capability etc.) --
  the wheel farm already knows them per package; hand declarations rot.
  Derived metadata may arrive later; typed-in numbers never.
