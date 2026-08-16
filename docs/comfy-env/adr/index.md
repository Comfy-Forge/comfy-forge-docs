# Architecture decision records

Nygard-style records (Status / Context / Decision / Consequences) for
[comfy-env](https://github.com/PozzettiAndrea/comfy-env). Numbers are
chronological-ish by subsystem, not by date; most decisions were made
implicitly during development and are recorded here retroactively as of
v0.4.12 (August 2026).

Where a status line cites "adversarial review": these are structured
LLM review panels (two independently-briefed reviewer personas that
investigate the ADRs and code separately, then debate each other's
findings), run by the maintainer in August 2026. Their claims were
verified against the working tree before being incorporated; treat the
citations as "the argument survived cross-examination", not as external
human endorsement.

| ADR | Decision | One-liner |
|-----|----------|-----------|
| [0001](0001-process-isolation-via-persistent-subprocess-workers.md) | Process isolation via persistent subprocess workers | Conflicting node deps get their own interpreter; ComfyUI talks to proxies. |
| [0002](0002-pixi-as-environment-manager.md) | pixi as environment manager | conda-forge + PyPI in one manifest with a real lockfile; uv speed underneath. |
| [0003](0003-two-config-files-with-two-roles.md) | Two config files, two roles | `comfy-env-root.toml` never touches the Python env; `comfy-env.toml` means full isolation. |
| [0004](0004-prebuilt-cuda-wheel-index.md) | Prebuilt CUDA wheel index | Kill the ABI x torch x CUDA x OS x arch build matrix for end users. |
| [0005](0005-tiered-tensor-serialization.md) | Tiered tensor serialization | Six strategies, best-available-first, zero-copy where the platform allows. |
| [0006](0006-worker-crosses-the-boundary-as-source-text.md) | comfy-env is never installed into worker envs | The worker script crosses as source text -- materialized as a file, never imported; duplication is deliberate. |
| [0007](0007-machine-wide-workspace-with-per-env-manifests.md) | Machine-wide workspace, per-env manifests | One shared store per machine; one `pixi.toml` per env; stamps guard staleness. |
| [0008](0008-graceful-degradation-everywhere.md) | Graceful degradation everywhere | Every failure path ends in "ComfyUI still boots". |
| [0009](0009-platform-strategy.md) | Platform strategy | Windows/macOS/Linux each get targeted workarounds, not lowest-common-denominator. |
| [0010](0010-wire-protocol-and-transport.md) | Wire protocol and transport | Hand-rolled framing stays; the v2 direction versions the wire and unifies the stack. |
| [0011](0011-isolation-before-sandboxing.md) | Isolation before sandboxing | v1 ships dependency isolation only; security is deferred with the path mapped, and isolation is never sold as a security boundary. |
| [0012](0012-unavailable-nodes-hidden-not-unregistered.md) | Unavailable nodes: menu-hidden, never unregistered | Workflows must load; menus must not show dead nodes. `DEPRECATED` separates the two. |
| [0013](0013-env-file-passthrough-contract.md) | Env-file config: honest passthrough | Forward everything pixi owns; comfy-env keeps 4 denied keys, 1 rewritten family, 1 merged table. Amends ADR-0003. |
| [0014](0014-pack-extensible-serializer-registry.md) | Pack-extensible serializer registry | Packs register their own wire types; payloads decompose into schema + tensors, never pickle; unknown tags pass through opaque. |
| [0015](0015-declared-wire-types.md) | Declared wire types | One `[types]` table per pack; `serialization.py` only for hero types; identity tags make shared types interop; failed serialization errors loudly. |
| [0016](0016-node-pack-dependencies.md) | Node pack dependencies | `[node_reqs]` auto-install stays for headless testing -- but pinned (git refs only; registry untrusted) and comfy-envved only; test-workflow utilities move to comfy-test config. |
| [0017](0017-pre-1-0-no-backward-compatibility.md) | Pre-1.0: no backward compatibility, by decision | Break freely while all consumers are the author's; the era ends at the slow-rollout tripwire, which starts the compat and security clocks. |
| [0018](0018-worker-call-timeout.md) | Worker call timeout | 600 s default becomes per-env `call_timeout`; expiry keeps kill-the-worker semantics; the mid-call heartbeat is the named successor. |
| [0019](0019-worker-lifecycle.md) | Worker lifecycle | Workers are disposable, replacement is invisible; generations, the stale-patcher invariant, consumed-ack, and the idle reaper decided. |
| [0020](0020-concurrency-and-env-granularity.md) | Concurrency and env granularity | The pack is the unit of concurrency and fate: one worker + one lock per env; disk may dedupe by content, processes never merge. |
| [0021](0021-three-call-contract.md) | The three-call contract | install/setup_env/register_nodes as one-liners; caller inference via stack frames; the file layout is the configuration. |
| [0022](0022-comfy-env-placement-in-host-env.md) | comfy-env's placement in the host env | The one exception to the host-env principle, kept for pre-1.0; the split-and-relocate alternative recorded for the rollout tripwire. |
| [0023](0023-metadata-scan-and-proxy-synthesis.md) | Metadata scan and proxy synthesis | The main process interviews packs instead of importing them; scan subprocess + hash-keyed cache + generated V1 proxies. |
| [0024](0024-upstream-interface-contract.md) | The upstream interface contract | The loan book: every ComfyUI internal we touch, stamped, with the upstream ask that retires it; the posture for the day core ships isolation. |
| [0025](0025-vram-co-management.md) | VRAM co-management across processes | ComfyUI stays the single VRAM authority; workers hold on lease; WDDM is best-effort by decision; device fields reserved for multi-GPU. |
| [0026](0026-trust-and-supply-chain.md) | Trust and supply chain | What users trust today, enumerated; wheel hashes and farm qualification move to now; bus factor named; pickle flips at the sandbox. |
| [0027](0027-testing-and-verification.md) | Testing and verification strategy | CI proves transport and compilers; the canary IS the GPU test; conformance, benchmark harness, and the HEAD-canary lane get owners. |
| [0028](0028-workspace-disk-lifecycle.md) | Workspace disk lifecycle | Envs are caches evicted with consent: gc categories, a banner nudge, and the refcount design content-addressing is blocked on. |
| [0029](0029-parent-as-switchboard.md) | Parent as switchboard | All inter-worker data flows through the parent, which owns what it holds; alternatives rejected at 1-2%; revisit trigger named. |
| [0030](0030-gpu-platform-floors.md) | GPU platform floors | Pinned-memory D2H is the next GPU investment; pool IPC demoted to experimental pending an ownership contract; floors probed, never assumed. |
| [0031](0031-frontend-javascript-isolation.md) | Frontend JavaScript isolation (deferred) | Pack JS shares one browser origin -- backend isolation buys zero here; deferred like 0011, with a comfy-test collision gate as the one build-now item. |
| [0032](0032-shm-lifetime-consumed-ack.md) | Shared-memory lifetime: consumed-ack | The reader frees blocks by acking `call_id`, not by a timer; the TTL is a crash fallback; materialize-on-receipt is the sibling rule. |
| [0033](0033-local-ipc-authentication.md) | Local IPC authentication | Per-spawn authkey as the worker's first frame + `SO_PEERCRED` on Linux; addr/key via env not argv; the honest Windows same-user gap and the not-a-sandbox line. |
| [0034](0034-admission-by-arithmetic.md) | Admission by arithmetic, never `mem_get_info` | `mem_get_info` is per-process on WDDM (measured: 13 GiB invisible); admission is decided from NVML/own-ledger numbers and a pre-compensated eviction target. |
| [0035](0035-duck-typed-model-proxy.md) | The model proxy is a duck-type | 18 declared members instead of ~120 inherited; unknown access raises naming the attribute; a canary fails when ComfyUI drifts. |
| [0036](0036-mirroring-comfyui-memory-management.md) | Mirroring ComfyUI's memory manager | Proxies stay in upstream's ledger; comfy-env owns only the eviction target and which worker model to evict. The target is a change of variables, not an estimate. |
