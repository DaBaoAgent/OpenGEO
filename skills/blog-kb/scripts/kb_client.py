#!/usr/bin/env python3
"""Thin wrapper that runs the core company knowledge base client (scripts/kb_client.py).

There is one implementation. This file only locates the core script and
forwards the command line, so the subcommands, the transport handshake, the
token resolution, the download guards and the exit codes are identical to the
core:

  python3 kb_client.py probe|stat|list|search|read|files|get|put|pull|ground|write ...

The core script is found at ``CLAUDE_BLOG_SCRIPTS_DIR`` when that variable
names an absolute directory, otherwise at ``<skills dir>/../scripts`` relative
to this file (the plugin layout and the standalone ``~/.claude`` layout both
satisfy this). If it cannot be found the wrapper exits 2 with a pointer.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

_CORE = None


def core_path() -> Path:
    """Return the core client path (not yet verified to exist)."""
    override = os.environ.get("CLAUDE_BLOG_SCRIPTS_DIR")
    if override:
        base = Path(override)
        if not base.is_absolute() or ".." in base.parts:
            raise ValueError("CLAUDE_BLOG_SCRIPTS_DIR must be a resolved absolute path")
    else:
        base = Path(__file__).resolve().parents[3] / "scripts"
    return base / "kb_client.py"


def load_core():
    """Import the core implementation once and return the module."""
    global _CORE
    if _CORE is not None:
        return _CORE
    path = core_path()
    if not path.is_file() or path.resolve() == Path(__file__).resolve():
        raise FileNotFoundError(path)
    name = "claude_blog_kb_client_core"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    # Register before executing. dataclasses resolves ClassVar and KW_ONLY
    # through sys.modules[cls.__module__]; without this entry a core module
    # that declares a dataclass raises AttributeError at import time.
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    _CORE = module
    return module


def main(argv: list[str] | None = None) -> int:
    try:
        core = load_core()
    except (FileNotFoundError, ValueError) as exc:
        print(
            "ERROR: this is a thin wrapper and the core scripts/kb_client.py was "
            f"not found ({exc}). Run the core script from the claude-blog install "
            "directly, or set CLAUDE_BLOG_SCRIPTS_DIR to its absolute scripts "
            "directory.",
            file=sys.stderr,
        )
        return 2
    return core.main(argv)


if __name__ == "__main__":
    sys.exit(main())
