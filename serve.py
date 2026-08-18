#!/usr/bin/env python3
"""Serve the docs locally on Linux, macOS, or Windows.

Usage:
    python serve.py            # default port 8001
    python serve.py 8002       # or pick one
    python serve.py 8002 --host 127.0.0.1
    python serve.py --edit     # edit pages in the browser, saving to docs/*.md
    python serve.py -- --strict    # extra args go through to `mkdocs serve`

Looks for mkdocs in .venv/, then in the interpreter running this script, then
on PATH, and finally falls back to `uvx` so no install step is needed.

--edit loads mkdocs.edit.yml, which adds the live-edit plugin: each page grows
an in-page editor that writes straight back to its Markdown source. It is
local-only on purpose -- the published site is static and has no backend to
save to, so a write endpoint there would be a hole. deploy.yml builds
mkdocs.yml, and the plugin also self-disables outside `mkdocs serve`.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG = HERE / "mkdocs.yml"
EDIT_CONFIG = HERE / "mkdocs.edit.yml"

# Matches README.md; mkdocs 2.0 drops the plugin system, so stay on 1.x.
# mkdocs first: it owns the `mkdocs` entry point that the uvx fallback runs.
PINS = ("mkdocs<2", "mkdocs-material==9.*")
# Only needed for --edit; kept out of PINS so a read-only serve stays lean.
EDIT_PIN = "mkdocs-live-edit-plugin"


def mkdocs_command(edit: bool = False) -> tuple[list[str], str]:
    """Return the argv prefix that runs mkdocs, plus a label for logging."""
    venv_bin = HERE / ".venv" / ("Scripts" if os.name == "nt" else "bin")
    venv_mkdocs = venv_bin / ("mkdocs.exe" if os.name == "nt" else "mkdocs")
    if venv_mkdocs.is_file():
        return [str(venv_mkdocs)], str(venv_mkdocs)

    try:
        import mkdocs  # noqa: F401
    except ImportError:
        pass
    else:
        return [sys.executable, "-m", "mkdocs"], f"{sys.executable} -m mkdocs"

    on_path = shutil.which("mkdocs")
    if on_path:
        return [on_path], on_path

    uvx = shutil.which("uvx") or shutil.which("uv")
    if uvx:
        prefix = [uvx]
        if Path(uvx).stem == "uv":
            prefix.append("tool")
            prefix.append("run")
        prefix += ["--from", PINS[0], "--with", PINS[1]]
        if edit:
            prefix += ["--with", EDIT_PIN]
        prefix.append("mkdocs")
        return prefix, "uvx (ephemeral install)"

    wanted = f'"{PINS[1]}" "{PINS[0]}"' + (f' "{EDIT_PIN}"' if edit else "")
    sys.exit(
        "mkdocs not found. Either install uv (https://docs.astral.sh/uv/), or:\n"
        f"    {sys.executable} -m venv .venv\n"
        f"    {'.venv/Scripts/pip' if os.name == 'nt' else '.venv/bin/pip'} "
        f"install {wanted}"
    )


def site_path() -> str:
    """The path mkdocs serves under, taken from site_url in mkdocs.yml."""
    match = re.search(
        r"^site_url:\s*\S+://[^/\s]+(/\S*)", CONFIG.read_text(encoding="utf-8"), re.MULTILINE
    )
    if not match:
        return "/"
    return match.group(1).rstrip("/") + "/"


def port(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not a port number") from None
    if not 1 <= number <= 65535:
        raise argparse.ArgumentTypeError(f"port {number} is out of range (1-65535)")
    return number


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Serve the comfy-forge docs with live reload.",
        epilog="Unrecognised arguments are passed through to `mkdocs serve`.",
    )
    parser.add_argument(
        "port", nargs="?", default=8001, type=port, help="port to listen on (default: 8001)"
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="address to bind (default: 0.0.0.0, i.e. reachable from your network)",
    )
    parser.add_argument(
        "-e",
        "--edit",
        action="store_true",
        help="enable in-browser editing; saves to docs/*.md on disk (local only)",
    )
    args, passthrough = parser.parse_known_args()

    if not CONFIG.is_file():
        sys.exit(f"{CONFIG} not found")

    config = CONFIG
    if args.edit:
        if not EDIT_CONFIG.is_file():
            sys.exit(f"{EDIT_CONFIG} not found (needed for --edit)")
        config = EDIT_CONFIG

    prefix, label = mkdocs_command(edit=args.edit)
    command = prefix + [
        "serve",
        "-f",
        str(config),
        "--dev-addr",
        f"{args.host}:{args.port}",
        *passthrough,
    ]

    shown_host = "127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host
    print(f"mkdocs: {label}")
    print(f"docs:   http://{shown_host}:{args.port}{site_path()}")
    if args.edit:
        print("editing ENABLED -- saves straight to docs/*.md; commit when happy")
    print(flush=True)

    try:
        return subprocess.call(command)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
