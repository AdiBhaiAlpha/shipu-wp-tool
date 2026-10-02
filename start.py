#!/usr/bin/env python3
"""ShiPu WP - repository entry point.

The Termux application lives in ``tool/`` (see idea.txt section 1). This file
exists only so that ``python start.py`` keeps working from the repository root
without mixing any website or backend code into the tool.
"""

from __future__ import annotations

import sys
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parent / "tool"


def main() -> int:
    """Run the Termux UI located in ``tool/``."""
    if not (TOOL_DIR / "start.py").exists():
        print(f"error: {TOOL_DIR / 'start.py'} not found", file=sys.stderr)
        return 1
    sys.path.insert(0, str(TOOL_DIR))
    from app.ui import run  # noqa: E402  (path set up above)

    return run()


if __name__ == "__main__":
    raise SystemExit(main())