"""Local storage namespace for the ShiPu WP tool.

The SQLite implementation lives in :mod:`app.storage`. This module is the thin
import path the rest of ``tool/`` uses so the local layer has one obvious home
and can be swapped for an encrypted store later without touching callers.
"""

from app.storage import (  # noqa: F401
    DATA_DIR,
    DB_FILENAME,
    Store,
    get_store,
    reset_store_for_tests,
    today_str,
)

__all__ = [
    "DATA_DIR",
    "DB_FILENAME",
    "Store",
    "get_store",
    "reset_store_for_tests",
    "today_str",
]
