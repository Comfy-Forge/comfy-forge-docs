# cuda-wheels

[cuda-wheels](https://github.com/PozzettiAndrea/cuda-wheels) is a repo which makes use of free GitHub workers to compile popular CUDA packages like flash-attention or pytorch3d across every version combination that PyTorch itself ships, and serves them.

!!! abstract "The aim"
    Build popular packages that are painful to compile from source, **for every
    version combination PyTorch ships for CUDA**, cheaply and on repeat, so
    that installing them is a download instead of a compile.

**Live pages:** [Package Index v2](https://pozzettiandrea.github.io/cuda-wheels/v2/) ·
[Dashboard](https://pozzettiandrea.github.io/cuda-wheels/dashboard/) ·
[Install Helper](https://pozzettiandrea.github.io/cuda-wheels/dashboard/install.html) ·
[Full Build Matrix](https://pozzettiandrea.github.io/cuda-wheels/matrix/)

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

Break [one of these](wheel-compatibility.md) and it will fail:

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

## Why does this repo exist?

Because **compiling these packages takes absurdly long and is genuinely
complicated**: a flash-attention build is hours of CPU time, needs the right
CUDA toolkit, the right compiler, enough RAM, gigabytes of headroom, one matching
torch... and a single mismatch anywhere produces a wall of C++ template errors.
Compiling is the single largest cause of *"this package won't install."*, at least in the ComfyUI ecosystem.

We wanted to hand Python CUDA users a way out, **especially ComfyUI users**, who
routinely have to install *many* Python CUDA packages.

## How do I use it?

The index is plain PEP 503: anything that can install from a URL can use
it, with **no coupling to any particular isolation layer or launcher**.
Pick the wheel matching your environment's (python, torch, CUDA, OS) and pin it:

```bash
pip install "flash_attn==2.8.3+cu128torch2.8" \
    --extra-index-url https://pozzettiandrea.github.io/cuda-wheels/v2/
```

Be careful though: this index should be **selected from, not resolved against**.
A wheel's CUDA and torch versions live in its local version tag, and pip cannot
match those against your machine: so an unpinned install grabs the highest
combo, not the one you can load.

Pin the full version as above, or use the
[install helper](https://pozzettiandrea.github.io/cuda-wheels/dashboard/install.html) to pick for you.

No `nvcc`, no Visual Studio, no waiting.