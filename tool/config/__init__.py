"""Configuration package for ShiPu WP.

    from config import get_config

Everything tunable lives in :mod:`config.settings`.
"""

from .settings import (
    AIConfig,
    Config,
    DATA_DIR,
    ENV_FILE,
    FIREBASE_CLIENT_DEFAULTS,
    FirebaseConfig,
    Pricing,
    REPO_DIR,
    TOOL_DIR,
    assert_no_server_secrets,
    get,
    get_bool,
    get_int,
    load,
    load_env,
)

__all__ = [
    "AIConfig",
    "Config",
    "DATA_DIR",
    "ENV_FILE",
    "FIREBASE_CLIENT_DEFAULTS",
    "FirebaseConfig",
    "Pricing",
    "REPO_DIR",
    "TOOL_DIR",
    "assert_no_server_secrets",
    "get",
    "get_bool",
    "get_int",
    "load",
    "load_env",
    "get_config",
]

_cached: "Config | None" = None


def get_config(refresh: bool = False) -> Config:
    """Return the process-wide configuration object."""
    global _cached
    if _cached is None or refresh:
        _cached = load()
    return _cached