# What occupies RAM and VRAM in a ComfyUI process

*Every process that imports torch and touches the card pays a fixed
price before it holds a single model. Measured, per stage.*
{: .subtitle }

A ComfyUI host and every comfy-env worker are the same kind of process: a
Python interpreter that imports torch and opens a CUDA context. What each
one occupies before any model is loaded was measured on one worker
environment (Python 3.13, torch 2.8.0+cu128, RTX 3090, driver 580.126.20,
`CUDA_MODULE_LOADING=LAZY`, 2026-09-13):

| Stage | RAM, PSS | of which private (anonymous) | VRAM |
|---|---|---|---|
| bare interpreter | 8 MB | 5 MB | 0 |
| after `import numpy` | 23 MB | 13 MB | 0 |
| after `import torch` | 483 MB | 254 MB | 0 |
| after the CUDA context (first tensor on the card) | 609 MB | 291 MB | 308 MiB |
| holding a 1 GiB tensor | 609 MB | 291 MB | 1332 MiB |
| after `del` and `empty_cache()` | 609 MB | 291 MB | 308 MiB |

## RAM: what the 600 MB is

**Importing torch** maps roughly 480 MB. About half of it is private
memory the process owns (the interpreter's objects, torch's registries,
the allocator state); the other half is the shared libraries themselves,
`libtorch`, `libc10`, the CUDA runtime, cuDNN and cuBLAS, mapped from
disk. File-backed pages are shared by every process that maps the same
file, which is why PSS (proportional set size, each shared page counted
once per sharer) is the honest number and RSS overstates it: two workers
on the same torch install cost far less than twice one worker, and a
`pixi` workspace that hardlinks one torch across environments keeps them
sharing the same pages. Workers on different torch builds share nothing.

**Opening the CUDA context** adds about 125 MB of RAM on top: the driver's
own state, the loaded kernel images (kept small by lazy module loading;
without `CUDA_MODULE_LOADING=LAZY` every kernel in every library is
loaded up front and this line grows several times over), and the pinned
staging buffers the runtime keeps.

This is the standing cost of a warm worker that the
[lifecycle page](worker-lifecycle.md) quotes as a few hundred MB, and it is
why workers are started on first use and reaped after a long idle rather
than kept warm for every pack on disk.

## VRAM: the context is not free either

The first CUDA operation in a process creates a context on the card,
and the context itself occupies about **300 MiB of VRAM** before any
tensor exists. That is the driver's per-process working set: kernel
images, the command queues, the allocator's bookkeeping, the primary
context's own buffers. It is paid once per process, never shrinks, and
is released only when the process exits. A host and four warm workers
therefore hold about 1.5 GiB of the card between them with nothing
loaded, and no amount of eviction inside any of the five gets it back.

Above the context, VRAM is what torch's caching allocator holds: model
weights, activations, and the cache of freed blocks it keeps for reuse.
`empty_cache()` returns the freed blocks to the driver (the table's last
row), which is what a worker's `full_release` does when the host asks it
to shrink; the context line under it stays.

Both numbers are what makes the aim on the
[memory management page](memory-approach.md) unattainable in principle
rather than merely unfinished: even a host that managed every worker's
models perfectly would still be sharing the card and the RAM with N
copies of this fixed cost, one per process, that it can neither see as
a model nor evict.
