#!/usr/bin/env python3
from __future__ import annotations
import sys
from pathlib import Path

def main() -> int:
    # Run UI from repo root
    from app.ui import run
    return run()

if __name__ == "__main__":
    raise SystemExit(main())
