# The two classmethods

*`IS_CHANGED` and `VALIDATE_INPUTS` run before execution, in the ComfyUI
process. Both have semantics that surprise people.*
{: .subtitle }

Both run **in the ComfyUI process, before anything executes** — but not at
the same moment. `VALIDATE_INPUTS` runs inside `validate_prompt`, on the
server side, when the prompt is submitted. `IS_CHANGED` is evaluated later:
`PromptExecutor.execute_async`, on the prompt-worker thread after the prompt
is dequeued, hands an `IsChangedCache` to each cache's `set_prompt`, and the
fingerprints are computed there while the cache keys are built, before any
node runs. That timing is what makes them hard to isolate.

## `IS_CHANGED` — telling the cache it is wrong

ComfyUI caches node outputs keyed on inputs. For a node whose result does not
depend only on its inputs — reading a file off disk, fetching a URL,
generating a random number — that cache is a bug. `IS_CHANGED` is the
override (`execution.py`):

```python
if issubclass(class_def, _ComfyNodeInternal) and first_real_override(class_def, "fingerprint_inputs") is not None:
    ...                                   # V3 spelling
elif hasattr(class_def, "IS_CHANGED"):
    ...                                   # V1 spelling
if not has_is_changed:
    self.is_changed[node_id] = False      # no method -> "never changed"
    return self.is_changed[node_id]
```

Whatever it returns is folded into the cache key. The idiom everyone uses:

```python
@classmethod
def IS_CHANGED(cls, **kwargs):
    return float("nan")     # NaN != NaN, so the key never matches. always re-run.
```

Two properties are easy to miss:

- It is called with the node's **declared inputs**, deliberately *not* with
  cached upstream outputs. The comment says *"We only want constants in
  `IS_CHANGED`"*.
- An exception inside it is **not fatal**: it is logged as a warning and the
  result becomes `float("NaN")`. A broken `IS_CHANGED` fails toward always
  re-running.

## `VALIDATE_INPUTS` — the signature matters more than the body

Returning `True` accepts the prompt; returning a string rejects it with that
message. But the part that catches people is what its **argspec** does
(`execution.py`):

```python
argspec = inspect.getfullargspec(validate_function)
validate_function_inputs = argspec.args
validate_has_kwargs = argspec.varkw is not None
...
if x not in validate_function_inputs and not validate_has_kwargs:
    if "min" in extra_info and val < extra_info["min"]:
        ...   # built-in range / combo checks
```

**Naming an input in the signature exempts it from ComfyUI's built-in
checks** — the min/max clamps and the "is this value in the combo list" test.
The node is declaring it will validate that input itself. A `**kwargs` form
sets `validate_has_kwargs`, which exempts **every input on the node at
once**.

That is the mechanism behind a common trick: a node that must accept a value
absent from its own combo list — a filename that appeared after the graph was
saved — declares that input in `VALIDATE_INPUTS` purely to switch the check
off.

## See also

- [comfy-env, caching and validation](caching-and-validation.md) — what happens to all of
  this once the node runs in another process
