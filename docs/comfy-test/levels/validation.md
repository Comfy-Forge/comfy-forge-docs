# `validation`

> Checks each workflow against the running server's own node definitions,
> in three tiers, without executing anything.

| | |
|---|---|
| **Needs** | `api` (provided by [`registration`](registration.md)) |
| **Default** | yes |
| **Fails the run** | yes |
| **Source** | `orchestration/levels/validation.py`, `comfyui/validator.py` |

Where [`static_capture`](static_capture.md) asks *"does this render?"*, this
asks *"is this graph coherent against what the nodes actually declare?"* --
and it answers from the live server, not from a stale copy of the schema.

## The three tiers

| Tier | Asserts |
|---|---|
| **Schema** | widget values match the allowed enums, types and ranges |
| **Graph** | connections are valid and every referenced node exists |
| **Introspection** | node definitions themselves are well-formed |

Schema catches a workflow saved with a sampler name your node no longer
offers, or an int outside a declared `min`/`max`. Graph catches links pointing
at removed slots, or a node type absent from the registry. Introspection
catches the pack's own definitions being malformed -- a `RETURN_TYPES` that is
a bare string instead of a tuple, a `FUNCTION` naming a method that is not
there.

That third tier is the one that finds bugs in *your* node rather than in your
workflow, which is why it is worth running even when the workflows are known
good.

## The injected helper pack

Validation POSTs to a `/validate` endpoint that stock ComfyUI does not have.
It comes from `PozzettiAndrea/ComfyUI-validate-endpoint`, cloned into **every**
environment by [`install`](install.md).

!!! warning "This is a supply-chain fact"
    A second pack, from a personal GitHub account, is installed alongside
    yours on every lane -- and the clone is **unpinned** (default branch, no
    ref). On attach lanes its install failure is swallowed, so validation can
    run against a missing helper. Disclosed in
    [ADR-0009](../adr/0009-a-helper-pack-is-injected.md).

## What it does not catch

Nothing executes. A graph that validates cleanly can still OOM, produce black
images, or crash on the first node -- that is [`execution`](execution.md).
Validation also cannot see anything decided at runtime: a node whose accepted
values depend on files present on disk will validate against whatever the test
machine happens to have.

## Config

| Key | Effect |
|---|---|
| `[test.workflows] cpu` / `cuda` | which workflows are validated on this backend |

In the default set. It needs `api`, so listing it pulls in `install` and
`registration`. A run with no workflows logs and returns without error.

It is one of the four **terminal** levels: list it in `[test] levels` instead
of `execution` when a pack should be checked for coherence but never spend GPU
time ([ADR-0012](../adr/0012-level-flag-swaps-terminals.md)).

## See also

- [The ladder](../levels.md) -- all 13 levels and the resource model
- [`static_capture`](static_capture.md) -- the same workflows as a rendered page
- [`execution`](execution.md) -- actually runs them
