# ADR-0009: A helper pack is injected into every environment

**Status:** accepted (2026-08); disclosed here after the 2026-08
adversarial review flagged it as an undisclosed supply-chain fact.

## Decision

> **comfy-test clones a second, foreign node pack --
> `PozzettiAndrea/ComfyUI-validate-endpoint` -- into every environment it
> builds**, alongside the pack under test (`levels/install.py`).

It exists to expose validation endpoints ComfyUI does not ship, which the
VALIDATION level calls.

## Context

ComfyUI's public surface answers "is this workflow accepted?" only by
running it: `POST /prompt` either queues or 400s. That conflates schema
errors, graph-wiring errors, and node-introspection errors, and it cannot
answer "would this node accept these inputs?" without executing the node --
which on a CPU lane may be impossible and on a GPU lane may take minutes.

The VALIDATION level needs a cheaper, more precise answer than "run it and
see". Getting one requires code inside the server process. There are three
places that code can live: in ComfyUI (a patch), in the pack under test
(contaminates the thing being measured), or in a separate pack installed
beside it.

## Alternatives rejected

- **`/prompt`-only validation.** Rejected: it cannot separate a bad socket
  from a bad value, and its only failure signal is a 400 body that varies by
  ComfyUI version.
- **Patching ComfyUI in the test environment.** Rejected: the environment
  must stay representative of what a user runs. A patched ComfyUI tests a
  ComfyUI nobody has.
- **Vendoring the endpoints into comfy-test and injecting them at boot.**
  Same effect, worse honesty: it hides a third-party code path inside the
  test tool instead of showing it as an installed pack.

## Consequences

- **Every environment comfy-test builds contains a pack the author did not
  ask for**, fetched from GitHub at install time. That is a supply-chain
  fact and belongs in the docs, not just in the source. If the helper repo
  were compromised, it would execute inside the test environment.
- Test environments are not byte-identical to user environments. The delta
  is one pack, but "we install exactly what a user installs" is false and
  should not be claimed.
- The clone is a network dependency in INSTALL: GitHub being down fails the
  level for reasons unrelated to the pack under test.
- Attach lanes ([ADR-0003](0003-two-install-paths-attach-and-fresh.md))
  install the helper in YAML rather than through comfy-test, so the same
  fact holds by a different route.
- A future version could vendor the endpoints as a comfy-test subpackage
  installed from PyPI (pinned, hash-checked) instead of a git clone at test
  time. That would keep the capability and remove the unpinned fetch.
