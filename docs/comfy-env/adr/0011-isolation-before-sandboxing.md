# ADR-0011: Isolation before sandboxing

**Status:** accepted (2026-08)

## Decision

> **v1 ships dependency isolation only; sandboxing is deferred with the
> path mapped.** Not security theater (isolation is never sold as a
> boundary); not negligence (vanilla ComfyUI has no sandbox either, and
> process isolation is sandboxing's precondition).

**v1 scope is dependency isolation only.** Sandboxing is *deferred*, not
rejected. Engineering effort goes to making isolation work end-to-end
(envs, workers, transport -- ADR-0001/0007/0010) before any security
hardening.

## Context

comfy-env's workers run node-pack code with the user's full privileges: no
filesystem confinement, no network restriction, no syscall filtering. The
2026-08 reviews called this out ("no security posture"), and the sibling
project pyisolate demonstrates what a posture looks like on this exact
architecture: a deny-by-default bubblewrap sandbox (explicit system-path
allow-list, `--unshare-user --unshare-pid --new-session`, network off by
default, GPU device nodes bound explicitly, IPC namespace deliberately kept
shared so /dev/shm and CUDA IPC still work) plus a no-pickle transport as an
anti-RCE invariant.

Containers were evaluated separately (ADR-0001 alternatives): rejected as
the universal mechanism (Windows host<->container memory boundary,
Docker-Desktop prerequisite), viable later as an optional Linux backend.

## Why this is legitimate (and not negligence)

1. **No regression.** Vanilla ComfyUI runs arbitrary pack code unsandboxed
   *in the main process*. comfy-env with unsandboxed workers matches the
   ecosystem's status quo; deferring security declines to ADD a property
   nobody currently has, it removes nothing.
2. **Isolation is sandboxing's precondition.** In-process code cannot be
   sandboxed at all. Once every pack runs in its own subprocess behind a
   narrow socket, adding bwrap/namespaces is a launch-command change, not a
   redesign -- pyisolate proves the recipe on an architecture shaped like
   ours. Building isolation first IS building security's prerequisite.
3. **Demand asymmetry.** Dependency conflicts break users daily; sandbox
   escapes are hypothetical today. Sequencing by observed pain is correct.

## Consequences and honesty clauses

- **Never advertise isolation as a security boundary.** Workers give crash
  containment and dependency separation; they give NO protection against a
  malicious pack -- it runs with the user's privileges, same as vanilla
  ComfyUI. Docs and README language must not conflate the two.
- **The deferred path is mapped, not vague:** Linux first via bubblewrap on
  the existing workers, reusing pyisolate's allow-list and GPU
  device-pattern approach (keep the IPC namespace shared); Windows is a
  separate, harder track (restricted tokens / job objects / AppContainer --
  comfy-test's stduser CI lane already demonstrates the primitive).
  Optional Linux container backend remains on the table per ADR-0001.
- **Guardrail: do not foreclose the later move.** Current design has no
  foreclosing choices; note that ADR-0010 v2's replace-pickle-with-schemas
  item is itself the transport-hardening prerequisite, so transport work
  and future sandboxing pull in the same direction.
