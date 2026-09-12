# ADR-0040: Models never cross the process boundary

**Status:** accepted (2026-09-11). Narrows [ADR-0005](0005-tiered-tensor-serialization.md)
by carving `ModelPatcher`, `CLIP`, `VAE`, `ControlBase` and every object that
wraps one out of the serialization ladder. Leaves [ADR-0035](0035-duck-typed-model-proxy.md)
untouched: the stand-in carries a *handle*, not a model, and that is the one
model-shaped thing that is allowed to cross.

## Decision

> **A model is loaded in the process that uses it, and never leaves it.** Not
> as bytes, not as a pickle, not as a copy. The serializer refuses any
> model-shaped object with a named error that says so, in both directions.
> A pack that needs a model in its worker loads it there.

## Context

The serialization ladder ([ADR-0005](0005-tiered-tensor-serialization.md))
ends in a pickle rung with one rule: fail loudly if pickling fails. Nothing on
the input path recognises a model — `grep -rnE '"(MODEL|CLIP|VAE)"' src/`
returns nothing — so a host-created `ModelPatcher` handed to an isolated node
falls straight to that rung. And because ComfyUI is on the worker's
`sys.path`, **the pickle succeeds.**

Measured, with comfy-env's real `_to_shm`/`_from_shm`:

```
type back: ModelPatcher    same object? False    back.model is p.model? False
is_clone(back)? False      clone_base_uuid equal? True
after add_patches in the copy:  original has patches? 0   copy: 1
```

The worker receives a real, complete, independent `ModelPatcher`. The
consequences are all silent:

| What happens | Why it is wrong |
|---|---|
| every patch the node applies lands on the copy | the host's model never sees it. The node "works" and does nothing |
| weights are pickled inline | a 12 GB UNet is 12 GB of `pickle.dumps` plus a 12 GB memfd plus the original, then 12 GB again on unpickle |
| the copy touches CUDA in the worker and auto-registers | a `SubprocessModelPatcher` lands in `current_loaded_models` **beside** the host's own entry for the same weights. `is_clone` says they are unrelated. Two entries, two copies on the card |
| every `Hook` on a CONDITIONING that round-trips loses its identity | `Hook.__hash__` is `hash(self.hook_ref)`. Hook LoRAs and `SetClipHooks` silently do nothing, and upstream's filter is *designed* to drop unregistered hooks |
| two kwargs aliasing one object arrive as two objects | `_from_shm` has no memo. Identity-keyed upstream logic stops matching |

And where it does *not* succeed, it fails with the wrong message. `VAE.__init__`
assigns four instance-level lambdas unconditionally (`comfy/sd.py`);
`ControlBase.__init__` assigns one (`comfy/controlnet.py`). Both are
unpicklable, so a `VAE` or `CONTROL_NET` input dies every time — with a
comfy-env `TypeError` advising the author to register a serializer in *their
pack's* `serialization.py`, for a core type they do not own and cannot change.

The docs said the opposite. `serializers.md` and `memory-approach.md` both
stated that models do not cross. That was true of the worker→host direction
only, which [ADR-0035](0035-duck-typed-model-proxy.md) handles with the
stand-in. Nobody had audited host→worker.

## Why refuse rather than fix

The alternative is to make the copy correct: write-back for patches, identity
preservation across the wire, VRAM accounting for the duplicate, hook
re-registration. Each of those is a mechanism, and together they amount to
re-implementing ComfyUI's model manager across a socket — the thing
[ADR-0038](0038-the-memory-floor.md) already declined to do.

More fundamentally: **a model is not data.** It is a handle on device memory,
a patch stack, a set of hooks keyed by identity, and a registration in a
process-global ledger. Every one of those is meaningless in another process.
Copying the bytes and calling it the same model is the lie the pickle rung
was telling.

The honest shape is the one ComfyUI itself has: one process, one model, loaded
where it runs. comfy-env's job is to make the *pack* run elsewhere, not to
make ComfyUI's memory manager distributed.

## Consequences

- **A pack that takes a `MODEL`, `CLIP`, `VAE` or `CONTROL_NET` input cannot be
  isolated as written.** It gets a named, immediate error at the boundary
  naming the sentinel type and this ADR — not a pickle failure three levels
  inside an argument it never touched. The pack's fix is to load its own model
  in the worker, which is what it would do standalone anyway.
- **The stand-in is unaffected.** ADR-0035's `SubprocessModelPatcher` carries a
  handle to a model the *worker* owns, so the host can evict it. That is a
  reference, not a copy, and is exactly the shape this ADR permits.
- **The return path is covered too.** A worker node that returns a `MODEL`
  today ships the weights back in parallel with the stand-in
  (`_persistent_worker.py`, `result_meta = _to_shm(result, ...)`, no model
  guard). Same refusal, same error.
- **Enforcement is a serializer guard, not a type registry.** A short denylist
  of class names checked before the pickle rung, raising with a message that
  names the type, the direction, and the alternative. Small, and it makes
  the silent case loud and the loud case honest.
- **`serializers.md` and `memory-approach.md` are corrected** to say what they
  meant: models do not cross *in either direction*, and the stand-in is not
  an exception to that but the mechanism for it.

## What this does not decide

- Whether an isolated pack should be able to *request* a host-loaded model by
  name and have the host run it on the pack's behalf. That is an RPC design,
  not a serialization one, and nothing today asks for it.
- The `CLIP` case specifically: `CLIP` has no instance lambdas and its
  picklability turns on the tokenizer (`sentencepiece` for T5/Flux/SD3). This
  ADR refuses it regardless — a pickled tokenizer is still a copy of a model.
