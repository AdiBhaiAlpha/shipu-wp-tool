#!/usr/bin/env python3
"""ShiPu WP - launch script.

    python start.py

Nothing else belongs in this file: it only hands control to the UI layer.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.ui import run
from config import assert_no_server_secrets

if __name__ == "__main__":
    # Refuse to start if a server-only secret leaked into the client build.
    assert_no_server_secrets()
    raise SystemExit(run())
