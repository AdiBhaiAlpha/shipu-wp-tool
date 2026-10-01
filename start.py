#!/usr/bin/env python3
"""ShiPu WP - Termux launcher.

    python start.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tool.app.ui import run
from tool.config import assert_no_server_secrets

if __name__ == "__main__":
    assert_no_server_secrets()
    raise SystemExit(run())
