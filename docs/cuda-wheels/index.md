# cuda-wheels

[cuda-wheels](https://github.com/PozzettiAndrea/cuda-wheels) is a repo which makes use of free GitHub workers to compile popular CUDA packages like flash-attention or pytorch3d across every version combination that PyTorch itself ships, and serves them.

!!! abstract "The aim"
    Build popular packages that are painful to compile from source, **for every
    version combination PyTorch ships for CUDA**, cheaply and on repeat, so
    that installing them is a download instead of a compile.

Design decisions live in the [ADR series](adr/index.md)

## Why can't I just pip install flash-attention?

**C++ and CUDA, unlike Python, have to be compiled.**
A pure-Python package ships `.py` files that
any machine can run as they are, while C++ must first be turned into machine code
**ahead of time, for one specific target**, and it then only runs where the
assumptions it was built under still hold.

## The seven demands a wheel makes

A Python CUDA wheel is a compiled CUDA binary (`.so` / `.pyd`) plus some Python Code, zipped together.
The Python half runs anywhere; every constraint comes from the compiled binary, which makes a **set of
demands on the machine it will land on, the torch it will load into, and the
CPython it will talk to**.

!!! info ""
    *For more info: [Wheel my wheel run?](wheel-compatibility.md)*

Break one of these and it will fail:

1. **"Your GPU speaks my instruction set"**

    machine code is compiled per GPU generation (`sm_86`, `sm_120`, ...)
    &rarr; `no kernel image is available for execution on the device`

2. **"You have exactly this torch"**

    libtorch's C++ ABI changes every minor release
    &rarr; `undefined symbol: _ZN3c104impl...`

3. **"I match your torch's CUDA"**

    a cu126 torch means 12.6 runtime libraries in the process
    &rarr; an import error, or worse, a late one

4. **"You are running exactly this Python"**

    `cp312` is CPython 3.12's C ABI
    &rarr; `no matching distribution found`

5. **"Your CPU speaks my instruction set"**

    x86-64 is not arm64
    &rarr; `not a supported wheel on this platform`

6. **"Your OS is the one I was built for"**

    a Linux wheel (`.so`, gcc) cannot load on Windows (`.pyd`, MSVC), and
    vice versa
    &rarr; `not a supported wheel on this platform`

7. **"Your glibc is at least this new"** *(Linux only)*

    the floor the build runner baked in
    &rarr; `libc.so.6: version 'GLIBC_2.35' not found`

In the cuda-wheels repo, we compile across all of these axes.

!!! info ""
    *What we cover right now: [Coverage](coverage.md)*

## Why does this repo exist?

Because **compiling these packages takes absurdly long and is genuinely
complicated**: a flash-attention build is hours of CPU time, needs the right
CUDA toolkit, the right compiler, enough RAM, gigabytes of headroom, one matching
torch... and a single mismatch anywhere produces a wall of C++ template errors.
Compiling is the single largest cause of *"this package won't install."*, at least in the ComfyUI ecosystem.

We wanted to hand Python CUDA users a way out, **especially ComfyUI users**, who
routinely have to install *many* Python CUDA packages.

!!! info ""
    *How the compiling happens here: [The build process](build-process.md)*

## How do I use it?

The index is plain PEP 503: anything that can install from a URL can use
it, with **no coupling to any particular isolation layer or launcher**.

**Put your (CUDA, torch) combo in the URL** -- there is one index directory
per pairing, the same convention as `download.pytorch.org/whl/cu128/`:

```bash
pip install flash_attn \
    --extra-index-url https://pozzettiandrea.github.io/cuda-wheels/cu128/torch2.8/
```

That directory contains only wheels built for cu128 + torch 2.8, so pip's
normal resolution does the rest: your Python version and OS are the two tags
pip matches by itself. The same command works verbatim on Windows and Linux.

A flat all-combos index also exists (the site root on the Comfy-Forge line;
[/v2/](https://pozzettiandrea.github.io/cuda-wheels/v2/) on the legacy farm),
but be careful: it should be **selected from, not resolved against**.
It mixes every combo, and pip cannot read the `+cu128torch2.8` tag against
your machine -- an unpinned install there grabs the highest combo, not the
one you can load. Use it only with a full version pin
(`"flash_attn==2.8.3+cu128torch2.8"`), or let the
[install helper](https://pozzettiandrea.github.io/cuda-wheels/dashboard/install.html)
pick for you.

No `nvcc`, no Visual Studio, no waiting.