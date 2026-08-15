# ADR-0024: The upstream interface contract

**Status:** accepted (2026-08-14). The inventory below is the ledger;
keeping it current is part of touching any listed surface.

## Decision

> **Every ComfyUI internal comfy-env touches is a loan, and this is
> the loan book.** Each entry carries what we hold, why, and the
> upstream ask that would retire it. A monkey-patch without an entry
> here is a bug; an entry without an upstream ask is a debt with no
> repayment plan.

### The inventory (stamped against ComfyUI 0.30.x, comfy-env 0.4.18)

*Note: line numbers below are indicative, not exact -- the 0.4.18 IPC-auth
insertion shifted several worker offsets. Grep the named symbol, not the
line. Keeping these exact is the very discipline this ADR asks for, and is
tracked as the widened truth-sweep in [ADR-0027](0027-testing-and-verification.md).*

| # | Surface | Where | Upstream ask |
|---|---------|-------|--------------|
| 1 | `SubprocessModelPatcher` subclasses `ModelPatcher`; instances inserted into `comfy.model_management.current_loaded_models` with hand-built weakref finalizers | `isolation/model_patcher.py`, `wrap.py:_register_new_patchers` | A VRAM **lease/eviction API**: register (size, evict-callback), receive eviction requests. Retires 1-3. |
| 2 | `load_models_gpu` shim in the worker negotiating budget with the parent | `_persistent_worker.py` (~:1376) | same as 1 |
| 3 | The `_STALE_PATCHERS` dance around `free_memory`'s iteration ([ADR-0019](0019-worker-lifecycle.md)) | `wrap.py:_cleanup_stale_patchers` | same as 1 |
| 4 | Worker-side `torch.nn.Module.to()`/`.cuda()` hooks for model auto-detection | `_persistent_worker.py` (~:1160) | A model-registration hook (or the lease API's register call) |
| 5 | `DEPRECATED = True` repurposed to menu-hide unavailable nodes ([ADR-0012](0012-unavailable-nodes-hidden-not-unregistered.md)) | proxy synthesis | A real `HIDDEN` flag -- cheap, likely accepted; ask first |
| 6 | `folder_paths` state snapshotted in the parent and re-applied in the worker | `subprocess.py:444-479`, worker config apply | A serializable path-config values API |
| 7 | Worker mutates `comfy.cli_args.args` (`use_sage_attention`, `use_flash_attention`, `cpu`) | `_persistent_worker.py` (~:1331-1363) | An args override entry point; until then this is the entry most likely to anger a core dev who greps for it -- flag it to them before they find it |
| 8 | `sys.meta_path` hook patching `model_management` memory accounting (Pool IPC parent half, default-off) | `environment/setup.py` | none -- scheduled for replacement by the pluggable-allocator design ([ADR-0030](0030-gpu-platform-floors.md)); do not upstream a patch we intend to delete |
| 9 | V1/V3 schema duality absorbed in proxy synthesis ([ADR-0023](0023-metadata-scan-and-proxy-synthesis.md)) | `metadata.py` | Nothing to ask; this is normal API consumption. Watch V3 executor parallelization ([ADR-0020](0020-concurrency-and-env-granularity.md) is the recorded posture) |
| 10 | Manager interop assumptions: Manager runs `install.py`/`requirements.txt` (the [ADR-0022](0022-comfy-env-placement-in-host-env.md) delivery channel AND its downgrade hazard); `[node_reqs]` clones land outside Manager's bookkeeping ([ADR-0016](0016-node-pack-dependencies.md)) | `install/` | Ask Manager for: an install-provenance marker for programmatically-added packs, and (registry team) the integrity guarantees 0016 names as its revisit condition |

Enforcement: the ComfyUI compat canary (`comfyui-canary.yml`) is the
tripwire for entries 1-9; a scheduled HEAD-lane
([ADR-0027](0027-testing-and-verification.md)) is the early warning.

### The posture for the day Comfy-Org ships isolation

Comfy-Org owns pyisolate. If core ships a blessed isolation seam, the
recorded position is: **the transport is the replaceable half; the env
layer is the durable asset** (pixi + the three pillars, the wheel farm,
torch-pin alignment, the `comfy-env.toml` surface with the pack fleet
on it). Core shipping isolation is an integration event, not an
existential one -- comfy-env adopts the official transport at the
synthesized-proxy seam and keeps compiling envs. Every entry above is
therefore written as an *ask*: the goal state is that comfy-env holds
no patches, only APIs.

The 2026-08 external review's strongest strategic claim is recorded
here without being decided: the upstreaming leverage (a working
system, measured data, a deployed pack fleet) **depreciates monthly**,
and a concrete three-hook RFC (proxy registration seam, VRAM lease,
progress/interrupt forwarding) should go to Comfy-Org sooner rather
than later. Timing is the maintainer's call; the asks above are the
RFC's raw material whenever it goes.

## Context

Before this record, the patch surface was scattered across five ADRs
and the code; nobody could answer "what exactly does comfy-env hold
against ComfyUI internals" without grepping. ADR-0001 named upstreaming
"the maintenance endgame"; a ledger with per-item asks is what makes
that endgame executable instead of aspirational.

## Consequences

- Adding a new patch/hook/assumption requires adding a row here with
  an ask, in the same change.
- The rows are the negotiation document if upstream relations ever get
  formal, and the triage list when a ComfyUI release breaks something.
- Entry 8 is deliberately ask-less: patches we plan to delete are not
  offered upstream.
