# Architecture decision records

Nygard-style records (Status / Context / Decision / Consequences) for
[comfy-env](https://github.com/PozzettiAndrea/comfy-env). Numbers are
chronological-ish by subsystem, not by date; most decisions were made
implicitly during development and are recorded here retroactively as of
v0.4.12 (August 2026).

| ADR | Decision | One-liner |
|-----|----------|-----------|
| [0001](0001-process-isolation-via-persistent-subprocess-workers.md) | Process isolation via persistent subprocess workers | Conflicting node deps get their own interpreter; ComfyUI talks to proxies. |
| [0002](0002-pixi-as-environment-manager.md) | pixi as environment manager | conda-forge + PyPI in one manifest with a real lockfile; uv speed underneath. |
| [0003](0003-two-config-files-with-two-roles.md) | Two config files, two roles | `comfy-env-root.toml` never touches the Python env; `comfy-env.toml` means full isolation. |
| [0004](0004-prebuilt-cuda-wheel-index.md) | Prebuilt CUDA wheel index | Kill the ABI x torch x CUDA x OS x arch build matrix for end users. |
| [0005](0005-tiered-tensor-serialization.md) | Tiered tensor serialization | Six strategies, best-available-first, zero-copy where the platform allows. |
| [0006](0006-worker-crosses-the-boundary-as-source-text.md) | Worker crosses the boundary as source text | The worker is materialized as a file, never imported; duplication is deliberate. |
| [0007](0007-machine-wide-workspace-with-per-env-manifests.md) | Machine-wide workspace, per-env manifests | One shared store per machine; one `pixi.toml` per env; stamps guard staleness. |
| [0008](0008-graceful-degradation-everywhere.md) | Graceful degradation everywhere | Every failure path ends in "ComfyUI still boots". |
| [0009](0009-platform-strategy.md) | Platform strategy | Windows/macOS/Linux each get targeted workarounds, not lowest-common-denominator. |
| [0010](0010-wire-protocol-and-transport.md) | Wire protocol and transport | Hand-rolled framing stays; the v2 direction versions the wire and unifies the stack. |
| [0011](0011-isolation-before-sandboxing.md) | Isolation before sandboxing | v1 ships dependency isolation only; security is deferred with the path mapped, and isolation is never sold as a security boundary. |
| [0012](0012-unavailable-nodes-hidden-not-unregistered.md) | Unavailable nodes: menu-hidden, never unregistered | Workflows must load; menus must not show dead nodes. `DEPRECATED` separates the two. |
| [0013](0013-env-file-passthrough-contract.md) | Env-file config: honest passthrough | Forward everything pixi owns; comfy-env keeps 4 denied keys, 1 rewritten family, 1 merged table. Amends ADR-0003. |
| [0014](0014-pack-extensible-serializer-registry.md) | Pack-extensible serializer registry | Packs register their own wire types; payloads decompose into schema + tensors, never pickle; unknown tags pass through opaque. |
| [0015](0015-declared-wire-types.md) | Declared wire types | One `[types]` table per pack; `serialization.py` only for hero types; identity tags make shared types interop; failed serialization errors loudly. |
