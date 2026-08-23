# Roadmap / TODO

The standing work list, distilled from the 2026-08 reviews (three-reviewer
docs audit, the ADR-0002 adversarial panel, and the fleet conda survey).
Items link to the ADR or page that carries their full rationale. Done items
stay listed (struck) so the list doubles as a change record.

## cuda-wheels

1. **Requires-Dist curation** -- *landed 2026-08-21*: per-package
   `requires_dist` in `package.yml` replaces the wheel's metadata in the
   existing METADATA rewrite, with `{LOCAL}`/`{VER:<folder>}` expansion
   for exact local-version sibling pins; gate C2 asserts the result.
   Seven packages curated (ovoxel, spconv, cumm, cubvh, sageattn3,
   detectron2, nvdiffrast); torch lines deliberately untouched. Unblocks
   lockfile-visible inlining in comfy-env.
   ([CW-ADR-0004](cuda-wheels/adr/0004-combo-encoded-versions-and-metadata-patching.md))

## comfy-env

!!! success "12.8 on ARM64 -- resolved in the resolver, still blocked in the farm"
    The tier-2 fallback is now **per CPU architecture**: cu12.8/torch2.8 on
    x86_64, **cu13.0/torch2.10 on linux aarch64**. The old fallback was
    unsatisfiable on ARM -- PyTorch published no aarch64 wheel for the entire
    2.8 line on cu128 -- and 12.8/12.9 would have left Thor (`sm_110`) with no
    kernel image. 13.0 is the only line whose ARM arch list carries Thor
    natively, and it is where the ARM torchvision/torchaudio wheels become
    CUDA-tagged. Requires driver r580+. The wheel probe also matches the
    architecture now -- it used to accept an `x86_64` wheel on an ARM host and
    let pip discover the mismatch much later.

    **Still outstanding:** the farm builds ARM for exactly one pilot package
    (`cc_torch`), so tier 2 on ARM will still miss for any real pack. Widening
    `platforms:` beyond the pilot is a build-capacity decision
    ([CW-ADR-0015](cuda-wheels/adr/0015-linux-aarch64-opt-in.md)).

0. *Unscheduled thought (2026-08, not a commitment):* if the host env can be
   detected as conda-managed already, installing a pack's deps into that
   existing env might one day be acceptable -- recorded from the
   isolate-flag debate; today the answer to envless remains "not supported".
1. **Revive the inlining path** once curated wheels land -- URL
   pypi-dependencies in generated manifests, side-channel + `--no-cache`
   retired.
   ([The two-system problem](comfy-env/two-system-problem.md))
2. **Finish the orphaned system-env path** -- `_collect_root_conda_deps`
   is defined and never called; wire it as the shared GL/ffmpeg runtime
   layer. *Resolved by deletion 2026-08: its only would-be consumers
   (root-scope `[dependencies]` in UniRig/HYWM2) duplicated deps their
   subdir envs already deliver; those root sections were removed too. The
   shared-system-env idea stays here in case it is ever properly designed.*
3. **libomp dedupe beyond macOS; stop blanket `KMP_DUPLICATE_LIB_OK`** --
   the enforcement arm of the lineage-coherence principle
   ([ADR-0002](comfy-env/adr/0002-pixi-as-environment-manager.md)).
   **Needs a design pass before code**, because the macOS fix does not
   transplant:
    - macOS today: `dedupe_libomp()` symlinks every bundled `libomp.dylib`
      in site-packages to torch's canonical copy (`environment/libomp.py`,
      run at prestartup and per materialized env).
    - Windows: symlinks need privileges/dev-mode; needs hardlink-or-copy
      semantics plus DLL search-order care (`os.add_dll_directory`,
      Library/bin precedence) -- and the DLL name differs (`libiomp5md.dll`
      vs conda's `libomp.dll` family), so name-matching is part of the
      design.
    - Linux: soname variants (`libomp.so.5`, `libgomp`) and RPATH-baked
      loads; symlinking inside site-packages works but must respect the
      env's conda `libomp` as canonical when the env is conda-lineage
      (lineage coherence), torch's copy otherwise.
    - Exit criterion: once dedupe verifiably runs on all three platforms,
      remove `KMP_DUPLICATE_LIB_OK=TRUE` from `[activation.env]` in
      generated manifests and both hand-rolled env builders, so duplicate
      OMP runtimes fail LOUDLY instead of silently corrupting numerics.
4. ~~Housekeeping: `[apt]`/`[brew]` removed (pre-pixi legacy);
   `workspace.py` docstring now describes reality; dead
   `_read_env_torch_version` deleted outright~~ -- done.
5. **Support v3 `comfy_entrypoint` registration in the metadata scan** --
   the scan reads only `NODE_CLASS_MAPPINGS`, so an isolated package that
   registers *purely* via `comfy_entrypoint()` (no dict) scans as **0 nodes,
   silently** -- the same whole-pack-vanishes failure the accelerator rule
   fixed. Measured over the top 500 Registry packs: **30 (6%) are pure
   `comfy_entrypoint`** and they are the big/modern ones
   (cg-use-everywhere #1, animatediff-evolved, advanced-controlnet,
   inpaint-nodes, prompt-control); the fraction is growing.
   - *Not urgent under the current opt-in contract* (authors route through
     `register_nodes`/`initc` -> `NODE_CLASS_MAPPINGS`), but cheap and
     strategically pointed for the "isolate arbitrary packs" direction --
     those top packs are exactly the ones one would want to wrap.
   - *Now (~3 lines):* detect a pure-`comfy_entrypoint` package in the scan
     and warn loudly ("registers via comfy_entrypoint, which the scan does
     not read -- expose NODE_CLASS_MAPPINGS or use register_nodes") instead
     of returning 0 nodes.
   - *Soon (~20 lines):* full scan support -- mirror ComfyUI's loader
     (`nodes.py:2297-2327`): call `comfy_entrypoint()`, `await
     get_node_list()`, `GET_SCHEMA()` per class. The proxy half already
     exists (`_build_v3_proxy_class`, proven by GeometryPack's v3 classes),
     so only the scan's entry-door invocation is missing.
6. *Deferred by design*: uv-first materialization for envs with zero conda
   content; CI-pre-solved `pixi.lock` per env x ABI-tag for the ComfyUI
   Desktop population; py-rattler (watch item).
7. ~~Pin the pixi binary (version + sha256, version marker)~~ -- done.
8. ~~Canary transport handshake~~ -- done
   ([ADR-0005](comfy-env/adr/0005-tiered-tensor-serialization.md)).

### Open items from the 2026-08-15 four-reviewer scan

9. **Resolve the by-reference object cache -- decide, then fix.** The
   worker keeps non-tensor custom objects resident and returns
   `{"__comfy_ref__": ...}` handles (`_persistent_worker.py` `_cache_object`
   / `_serialize_result`); `_object_cache` is **never evicted** -- an
   unbounded leak for the worker's life. It also **contradicts**
   [ADR-0029](comfy-env/adr/0029-parent-as-switchboard.md), which says
   by-reference was "killed twice on census evidence." Decide whether the
   path is live (then cap/TTL it and correct 0029 to scope its claim to the
   cross-pack plane) or dead (then delete it). Do not fix the leak before
   deciding, or you entrench a design 0029 says was rejected.
10. **Three-hook upstream RFC to Comfy-Org** -- the executable form of the
    [ADR-0024](comfy-env/adr/0024-upstream-interface-contract.md) loan book:
    propose (as a ComfyUI issue/discussion) three official seams that retire
    most of the patch surface -- a node-registration seam, a VRAM
    lease/eviction API, and progress/interrupt forwarding. Strategic, not
    code; leverage (working system + 50-pack deployment + measurements)
    depreciates as Comfy-Org builds its own isolation. Optional, but the
    only item with a shrinking clock.
11. **Barrages vs suite-monorepo** -- decide before the
    [ADR-0017](comfy-env/adr/0017-pre-1-0-no-backward-compatibility.md)
    rollout tripwire whether the ~24 `[node_reqs]`-linked packs consolidate
    into one suite repo (atomic barrage = one commit) or stay hand-run. Trivial
    now, near-impossible after external packs pin independently.
12. **Widen the docs truth-sweep to defect-claims + branch names.** The
    [ADR-0027](comfy-env/adr/0027-testing-and-verification.md) doc-claims
    sweep greps config keys/env vars against the tree; extend it to flag any
    ADR line saying "in flight / on the `<x>` branch / still present /
    currently breaks", since a merge silently turns those into fiction (the
    0.4.18 batch left six such lines across five ADRs -- swept 2026-08-15).
13. **Reclaim orphaned `/dev/shm` on a double-crash.** If both parent and
    worker are SIGKILLed, a reply's shm blocks leak until reboot -- no ack,
    no TTL sweep. Extend the startup sweep (`wrap.py`, which already reaps
    stale sockets/temp dirs) to orphaned shm blocks
    ([ADR-0032](comfy-env/adr/0032-shm-lifetime-consumed-ack.md)).

## comfy-test

1. **Consume ACCELERATOR declarations** -- CPU lanes skip tagged nodes
   honestly (no empty-module mocks for declared packs); derive the
   cpu/cuda workflow splits from workflow content with the
   dispatcher-ambiguity rule and manual override
   ([accelerator declarations](comfy-env/accelerators.md)).
2. **CI reproducibility** -- pin the comfy-test version test jobs install
   (no `--upgrade` races); derive the random Python pick from `run_id` so
   re-runs reproduce; replace the gh-pages force-push with
   fetch-rebase-retry.
3. **comfy-env contract seam** -- stop importing `_abi_tag` / hardcoding
   the env layout; consume a stable `comfy-env info --json` instead.
4. **`javascript` isolation level** -- static collision lint of a pack's
   frontend JS (`reporting/js_lint.py`): ComfyUI auto-imports every
   `web/**/*.js` into one shared browser page, so packs collide via global
   writes, duplicate `registerExtension` names, unguarded `message`
   listeners, shared-object monkeypatches, shared-DOM/storage writes. The
   level derives the required namespace from `[tool.comfy].DisplayName` and
   errors on any main-realm touch of state a pack does not own; `.mjs`
   (iframe-only) is exempt, and a `.mjs` pulled in by a `.js` import is
   followed. **Done 2026-08.** Remaining: thread the pack's node names in so
   a namespaced extension that hooks *another* pack's node (the squat class)
   is caught, and land the deferred runtime tier (diff `window`/DOM after
   load -- catches variable-aliased globals the static pass cannot).

### Upstream ComfyUI: the real frontend boundary (PR to open)

The `javascript` lint *detects and contains*; it cannot make a same-origin,
full-JS plugin **safe** -- variable-aliased globals (`const w = window`) and
same-origin iframe reach-through (`parent.X` from iframe HTML) are past what
static analysis can see, and the install-weighted majority of popular packs
(rgthree, cg-use-everywhere, Crystools, Easy-Use) *legitimately* patch the
shared canvas/menubar because ComfyUI offers no sanctioned extension point.
Only core can close this. **TODO: open an upstream ComfyUI PR** proposing, in
leverage order:

1. **Entry-point manifest** replacing the `**/*.js` auto-import glob -- a
   pack declares which files enter the main realm; everything else under
   `web/` is a static asset. Makes "what runs in the shared page" an
   explicit, reviewable list and fixes accidental iframe-internal imports
   (the `.mjs` trick is already this, informally).
2. **Sandboxed, port-based iframe-widget API** (`app.iframeWidget(node,
   {src, onMessage})` returning a `MessageChannel` port) -- pairwise ports
   make cross-pack message bleed structurally impossible (no `event.source`
   to forget), and `sandbox` (opaque origin) makes the boundary real so
   `parent.*` reach-through throws. Must re-handshake on iframe reload (a
   navigated iframe drops its port) and needs a CORS/blob-proxy story for
   in-iframe `/view` fetches under an opaque origin.
3. **Sanctioned hooks for shared surfaces** -- registered canvas overlays,
   menubar/panel slots, theme tokens, serialization middleware -- each
   fault-isolated by core, so deep-integration packs stop monkeypatching
   `LGraphCanvas.prototype`. Hooks compose by construction; monkeypatches
   only compose if every pack chains correctly (today's n-factorial risk).

This is the frontend twin of comfy-env's **isolation before sandboxing**
([ADR-0011](comfy-env/adr/0011-isolation-before-sandboxing.md)): ship the
containment now, propose the real sandbox upstream.

## Hypothetical TODO: RAM/VRAM efficiency levers

*Status: ideas, not commitments. Everything here was measured/discussed on
**Windows only** (2026-08, one machine -- Windows 11, RTX 4060 Ti; numbers
in [ADR-0001's cost table](comfy-env/adr/0001-process-isolation-via-persistent-subprocess-workers.md)).
The Linux items are unmeasured hypotheses. Re-measure before building any
of this.*

Ranked by value-per-effort as currently understood:

1. **Same-volume placement** (free; Windows-measured). OS page sharing of
   torch binaries only works between processes mapping the *same file* --
   measured ~157 MB shared per same-build CUDA worker. Host venvs on D:
   and the workspace on C: get zero sharing between ComfyUI-main and
   workers despite identical builds (hardlinks cannot cross volumes).
   Candidate: a `comfy-env doctor` advisory when host env and workspace
   volumes differ.
2. **Idle worker reaper** (already proposed in ADR-0001). Each env the
   user stopped touching holds ~550 MB host + ~150 MB VRAM (CUDA context);
   reap after an idle window, never putting spawn latency back on the
   execution path.
3. **Narrow the combo spread** (wheel-farm coverage). Every tier-2
   fallback env (cu128/torch2.8 beside a cu130/torch2.12 bootstrap) is an
   unshareable second torch on disk AND in RAM (~400 MB private per
   worker becomes fully private). Building the missing wheels converts
   duplication into sharing.
4. **Linux-only, unmeasured**: CUDA MPS (shares context infrastructure
   across processes); fork/COW zygote (heap "hardlinking" -- blocked on
   Windows, defeated by one-worker-per-env, CUDA does not survive fork);
   KSM `madvise(MERGEABLE)` (kernel dedupes identical anonymous pages,
   server-only, CPU cost). File under "if forge ever runs server fleets".
5. **Tensor daemon** (endgame, ADR-0010 future work): one GPU-owner
   process, one CUDA context total; workers become CPU orchestrators.
   Big redesign; parked.

Irreducible on Windows (do not chase): the per-process Python heap
(~250 MB/worker beyond shared pages) and the per-process CUDA context
(~125 MB host + ~150 MB VRAM) -- no OS primitive shares either.

### Left open by the 2026-08 VRAM repair

[ADR-0034](comfy-env/adr/0034-admission-by-arithmetic.md) and
[ADR-0035](comfy-env/adr/0035-duck-typed-model-proxy.md) fixed admission
and the proxy's honesty. What they deliberately did not fix:

- **Offload latency is now the visible cost.** With eviction actually
  running, a worker's partial unload is a synchronous IPC round trip plus
  a `torch.cuda.empty_cache()`. Nobody has profiled how much that adds to
  a pressured graph. Measure before optimising.
- **The ledger's blind spot survives.** When NVML and `nvidia-smi` are
  both unavailable, the offset comes from comfy-env's hook-based
  accounting, which cannot see allocations that bypass
  `nn.Module.to()`/`.cuda()`. Admission stays optimistic there. Logged
  per request with its source so the degraded mode is at least visible.
- **Multi-GPU is still single-device by assumption.** The `device` field
  reserved in ADR-0025 is still not on the wire.
- **Verified by construction and unit test, not by a workload.** The
  measurement that motivated the fix was a synthetic sibling-allocation
  probe. A real before/after on a pressured multi-env graph has not been
  run.

## Upstream watch items

- **pixi PR [#5464](https://github.com/prefix-dev/pixi/pull/5464)**
  (per-dependency `no-deps`) -- would let manifests carry the wheels with
  zero farm changes; stalled, worth a nudge.
- **conda-forge pytorch coverage** -- feedstock is current (2.13, CUDA on
  Linux + Windows); the gate for conda-native publishing is our
  dependent-package matrix plus the build-lineage caveat for zero-copy.
  ([The two-system problem](comfy-env/two-system-problem.md))
