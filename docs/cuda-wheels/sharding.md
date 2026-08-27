# Sharding

*Last verified against `cuda-wheels-forge` @ `4e1f0bc` (2026-08-26).*

GitHub runners have a hard **6-hour job limit**. A few packages compile more
translation units than fit in that window, so the farm splits one cell's compile
across N parallel jobs and links the result in an N+1th job.

Setting `sharding: N` is one line. Everything else on this page is what that one
line drags in.

---

## The shape of a sharded build

```
generate-matrix
   │
   ├── compile-shard 1/N  ──┐
   ├── compile-shard 2/N  ──┤  each: compile my slice, upload a COMPILER CACHE
   ├── ...                ──┤       (not objects, not a build tree)
   └── compile-shard N/N  ──┘
                            │
                       link job  ── restore all N caches, re-run the FULL build,
                                    every compile is a cache hit, link one
                                    ordinary wheel, verify, upload
```

Two things about this that surprise people:

- **The shards do not hand over object files.** They hand over a
  content-addressed compiler cache (ccache on Linux, sccache on Windows). The
  link job re-runs the *entire* build from scratch and every single compile is
  a cache hit. There is no mtime dance, no ninja-state merging, and a partial
  restore self-heals — a missing entry simply recompiles, slowly but correctly.
- **The shard jobs are expected to fail at the link step.** That is not a bug
  being tolerated; it is the design. See [below](#the-shard-link-is-supposed-to-fail).

---

## The two partitioning schemes

`shard_filter` picks which mechanism decides "is this TU mine?". They are
mutually exclusive and choosing wrong is a **silent slow build**, not an error.

### `seat` (the default)

The action installs a shell wrapper at `$RUNNER_TEMP/cuw/nvcc` and points
`PYTORCH_NVCC` (torch's ninja writer) and `CUDACXX` (cmake) at it. On every
invocation the wrapper hashes the source path and keeps only its slice:

```sh
h=$(printf %s "$src" | md5sum | cut -c1-8)
if [ $(( 0x$h % CUW_SHARD_COUNT )) -ne "${CUW_SHARD_INDEX0:-0}" ]; then
  cp "$CUW_EMPTY_OBJ" "$out"      # stub: a valid, empty object file
  exit 0
fi
```

Strengths: **zero per-package code**, works below setup.py and cmake alike,
every TU still produces a file so the build system's dependency graph stays
intact.

!!! danger "Build-system probes must never be stubbed"
    cmake's compiler-id and `try_compile` TUs get linked into probe
    *executables*. A stubbed one has no `main`, so configure dies with
    `Detecting CUDA compiler ABI info - failed`. The wrapper carries an explicit
    allowlist — `CMakeScratch`, `CompilerId`, `CMakeTmp`, `cmTC_`,
    `meson-private`, `conftest` — and compiles those for real. Probes cost
    seconds. When in doubt, compile.

### `source`

The package's own patch script reads `CUDA_WHEELS_SHARD_INDEX` /
`CUDA_WHEELS_SHARD_COUNT` and **deletes** every out-of-slice source before the
build system ever sees it. natten does this to its ~144 autogen CUTLASS `.cu`
files; spconv's generator does the equivalent.

Use it when the generated file set is large and the build system would otherwise
spend real time on files that are going to be stubbed anyway.

!!! danger "Never run both partitions at once"
    When `shard_filter: source`, the seat wrapper is set up but its
    `CUW_SHARD_COUNT` is deliberately **not** exported — it stays a pure ccache
    pass-through. If both partitions run, each shard gets the *intersection*,
    roughly `1/N²` of the work, and the union of all shards is missing most of
    the build. It still goes green. The seat wrapper also stubbed cmake's
    compiler-probe TUs, which killed configure outright in natten waves 1-2.

### `sharding_platforms`

Restricts which lanes shard at all. A package can shard on Linux and build
whole on Windows.

---

## The shard link is supposed to fail

A compile shard runs `pip wheel .` like any other build, and that build ends in
a link step that **cannot succeed**:

- With `source`, out-of-slice autogen TUs were deleted, but the non-autogen
  dispatcher TU survives in all N shards and still calls kernels this shard
  never built.
- With `seat`, every TU emits an object — but an *empty* object still leaves the
  symbol undefined, so the same dispatcher problem applies.

The shard's real deliverable is the compiler cache, so the failure is tolerated.
Tolerating it must not tolerate a *real* compile failure, so the exit is gated
on three conditions (`action.yml:719-766`):

| condition | failure it catches |
|---|---|
| objects exist (`find build -name '*.o' -size +1c`) | nothing compiled at all |
| the log contains `undefined reference to` | it failed for some other reason |
| the log contains **no** `file.cu:123: error:` diagnostic | a genuine compiler error being laundered as a link failure |

!!! note "This gate was too narrow until 2026-08-26"
    It was keyed to `shard_filter: source` on the assumption that "seat shards
    never hit this — every TU still emits an object". That reasoning is wrong,
    as above: an empty object still leaves the symbol undefined. spconv cu13.0
    proved it, with all 10 shards dying on
    `undefined reference to cumm::conv::main::Volta_...::conv_kernel` on every
    ARM and Linux cell.

The gate is also what catches a *configure* failure, because a shard that never
configures produces almost no objects and no undefined references:
`compile-shard failed without undefined references`.

---

## The link job's hit-rate gate: zero misses, not 90%

```
if [ "$MISSES" -gt 0 ]; then error; fi
if [ "$TOTAL"  -eq 0 ]; then error; fi
```

**Zero tolerance, not a ratio.** A single miss is a full TU recompile — ~470s on
flash_attn ARM — so a "94.4% hit rate" once passed this gate while costing 31
minutes and hiding an arch-collision bug. A ratio threshold cannot distinguish
"one nondeterministic TU" from "four shards arrived from the wrong
architecture"; a count can.

`TOTAL == 0` is **not** a pass. It means ccache was never consulted — nothing
was restored, or ccache is not on `PATH` — i.e. the handoff did not happen and
this job did not link shards at all. The previous check was vacuous there.

!!! warning "Shard caches carry their own stats"
    Each shard's tarball includes ccache's counters. Without a `ccache -z` after
    the merge, the link job's hit rate reads the shards' history instead of its
    own.

---

## Windows needed a different transport

Linux hands off **ccache**. Windows uses **sccache**, because cmake regenerates
files with timestamps newer than the restored objects and the ccache setup does
not survive it. The launcher is wired through
`CMAKE_{C,CXX,CUDA}_COMPILER_LAUNCHER`.

!!! danger "Git-bash `tar` reads the drive colon as a remote host"
    `RUNNER_TEMP` on Windows is `D:\a\_temp`. Handed to Git-bash `tar` verbatim:

    ```
    tar: D\:\a\\_temp/sccache: Cannot open: No such file or directory
    ```

    Both fixes are needed: `--force-local` **and** a POSIX-converted path.

---

## Choosing N — it is not "higher is faster"

Wall clock is roughly `TUs_per_shard × time_per_TU`, so more shards is faster
right up to the point where a shard runs out of work in some *family* of TUs.
Two hard ceilings:

### The matrix cap

GitHub caps a workflow matrix at **256 jobs**. Sharding multiplies job count by
N per lane. This is why some packages must be dispatched one CUDA lane at a
time.

### Family granularity

If the build splits sources into separate targets per GPU family, the smallest
family sets the useful ceiling. natten's Hopper family is ~22 files out of ~144,
partitioned by **global** sorted index, so:

| `sharding` | shards with zero Hopper files |
|---|---|
| 20 | 0 of 20 |
| 23 | ~1 of 23 — every other shard holds exactly 1 |
| 40 | 18 of 40 |

Past ~23 the Hopper family stops sharding: most shards skip it entirely and the
remaining ones already hold a single file, so no further wall-clock is bought
there. To go higher you would have to **partition per-category** rather than
globally.

---

## The trap this page exists for: guard the producer *and* the consumer

When a shard receives zero files of a family, the target for that family must be
skipped. natten's patch does that:

```cmake
if(NATTEN_WITH_HOPPER_FNA AND (AUTOGEN_HOPPER_FNA OR AUTOGEN_HOPPER_FMHA))
    add_library(natten_hopper OBJECT ${AUTOGEN_HOPPER_FNA} ${AUTOGEN_HOPPER_FMHA})
    ...
endif()
```

That is correct, and it is only **half** the requirement. Something downstream
consumes those objects, and it was guarded on a different condition:

```cmake
if(NATTEN_WITH_HOPPER_FNA)                       # WRONG
    target_link_libraries(natten PRIVATE $<TARGET_OBJECTS:natten_hopper>)
endif()
```

`NATTEN_WITH_HOPPER_FNA` is derived from the **arch list**, so it is true in
every shard. The target's existence depends on the **shard contents**. At
`sharding: 20` the two conditions happened to agree, because every shard got
Hopper files. At 23 they diverged and generate failed:

```
CMake Error at CMakeLists.txt:291 (target_link_libraries):
  Error evaluating generator expression: $<TARGET_OBJECTS:natten_hopper>
  Objects of target "natten_hopper" referenced but no such target exists.
```

The fix is to guard on the thing you actually mean:

```cmake
if(TARGET natten_hopper)
    target_link_libraries(natten PRIVATE $<TARGET_OBJECTS:natten_hopper>)
endif()
```

**Generalise this.** Raising N does not just make slices smaller; it makes
*empty* slices possible for the first time. Every conditional in the build that
was previously always-true becomes a real branch. Before raising N, grep the
patch for every reference to each per-family target and ask, for each one,
"is this guarded on the arch list, or on whether the files are here?"

The same reasoning says why `add_library(natten SHARED ${ALL_SOURCES})` is
safe: the source filter deletes only *autogen* files, so the hand-written
sources survive in every shard and `ALL_SOURCES` is never empty.

---

## Other things that have gone wrong

!!! warning "One flaky shard used to discard every wheel in the CUDA line"
    The link jobs lacked `always()`, so a single shard failure skipped the link
    and threw away the work of all its siblings. Three link jobs now carry
    `always()`.

!!! warning "`links_torch: false` + a `--pytorch` filter builds nothing, greenly"
    Torch-independent packages (cumm, spconv, llama_cpp_python) collapse the
    torch axis. Dispatching them with `-f pytorch=2.11` matches zero cells, the
    matrix is empty, every job is skipped, and the run reports **success**.

---

## Checklist before setting or raising `sharding: N`

1. Which `shard_filter`? If the package's patch partitions, it must be
   `source` — and then the seat wrapper must stay a pure pass-through.
2. Does the build split sources into per-family targets? Find the **smallest**
   family; that sets your ceiling.
3. For every per-family target, is each **consumer** guarded on `if(TARGET ...)`
   rather than on an arch flag?
4. Does `N × lanes` stay under the 256-job matrix cap?
5. After the run: did the link job report **zero** ccache misses? A ratio is
   not good enough, and `TOTAL == 0` is a failure, not a pass.
