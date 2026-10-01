"""Local persistence layer for ShiPu WP (SQLite, no server, no accounts).

Responsibilities
----------------
* store the selected plan, trial usage counter, usage date, first-run flag
  and a small key/value settings bag;
* roll the daily trial counter over when the local calendar date changes;
* never crash the UI - if the filesystem is read-only or the database is
  corrupt, the store transparently degrades to an in-memory session so the
  application remains usable (state is simply not persisted).

This module intentionally contains **no** authentication or billing logic.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path
from typing import Any, Dict, Optional

from . import plans as plans_mod

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------
PACKAGE_DIR: Path = Path(__file__).resolve().parent
PROJECT_DIR: Path = PACKAGE_DIR.parent
DATA_DIR: Path = PROJECT_DIR / "data"
DB_FILENAME: str = "shipu_wp.db"

# Schema version - bump when columns change so migrations stay explicit.
SCHEMA_VERSION: int = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def today_str() -> str:
    """Local calendar date as ``YYYY-MM-DD`` (used for daily resets)."""
    return date.today().isoformat()


class Store:
    """Thin SQLite wrapper with an in-memory fallback."""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path: Path = Path(db_path) if db_path else DATA_DIR / DB_FILENAME
        self._conn: Optional[sqlite3.Connection] = None
        self._fallback: Dict[str, str] = {}
        self.persistent: bool = True
        self.error: str = ""
        self._connect()

    # ---------------------------------------------------------------- setup
    def _connect(self) -> None:
        """Open the database, creating schema; degrade on failure."""
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.row_factory = sqlite3.Row
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
            self._set_raw("schema_version", str(SCHEMA_VERSION))
        except Exception as exc:
            # Read-only FS, missing sqlite3 module, corrupt file, ... -> mem mode
            self.error = f"{exc.__class__.__name__}: {exc}"
            self._conn = None
            self.persistent = False

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.commit()
                self._conn.close()
            except Exception:
                pass
            finally:
                self._conn = None

    # ------------------------------------------------------------ raw access
    def _get_raw(self, key: str) -> Optional[str]:
        if self._conn is None:
            return self._fallback.get(key)
        try:
            row = self._conn.execute(
                "SELECT value FROM meta WHERE key = ?", (key,)
            ).fetchone()
            return row["value"] if row else None
        except Exception:
            return self._fallback.get(key)

    def _set_raw(self, key: str, value: str) -> None:
        if self._conn is None:
            self._fallback[key] = value
            return
        try:
            self._conn.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            self._conn.commit()
        except Exception:
            self.persistent = False
            self._fallback[key] = value

    # -------------------------------------------------------------- meta api
    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        value = self._get_raw(key)
        return default if value is None else value

    def set(self, key: str, value: Optional[str]) -> None:
        self._set_raw(key, "" if value is None else str(value))

    # ------------------------------------------------------------ settings
    def get_setting(self, key: str, default: Any = None) -> Any:
        """Read a settings value (JSON encoded when not a plain string)."""
        raw = self._get_raw(f"setting:{key}")
        if raw is None:
            return default
        return _decode(raw, default)

    def set_setting(self, key: str, value: Any) -> None:
        self._set_raw(f"setting:{key}", _encode(value))

    def all_settings(self) -> Dict[str, Any]:
        """Return every ``setting:*`` entry decoded into Python values."""
        if self._conn is None:
            return {
                k.split(":", 1)[1]: _decode(v, v)
                for k, v in self._fallback.items()
                if k.startswith("setting:")
            }
        try:
            rows = self._conn.execute(
                "SELECT key, value FROM meta WHERE key LIKE 'setting:%'"
            ).fetchall()
        except Exception:
            return {}
        return {r["key"].split(":", 1)[1]: _decode(r["value"], r["value"]) for r in rows}

    # ------------------------------------------------------------ first run
    @property
    def first_run(self) -> bool:
        """True until the welcome/plan flow has been completed once."""
        return self.get("first_run", "1") == "1"

    def mark_first_run_complete(self) -> None:
        self.set("first_run", "0")
        self.set("first_run_completed_at", _now())

    # ------------------------------------------------------------------ plan
    def get_plan_key(self) -> Optional[str]:
        """Stored plan, validated against the plan catalogue."""
        raw = self.get("plan_key")
        return raw if plans_mod.is_valid_plan(raw) else None

    def set_plan_key(self, plan_key: Optional[str]) -> None:
        if plan_key is None:
            self.set("plan_key", plans_mod.NO_PLAN)
        else:
            self.set("plan_key", plan_key)

    # ---------------------------------------------------------------- usage
    def get_usage(self) -> Dict[str, Any]:
        """Today's usage record with automatic daily rollover.

        Returns a dict with ``date``, ``replies``, ``limit``, ``plan`` keys.
        The stored counter belongs to ``usage_date``; when that date differs
        from today the counter is reset to zero and the new date persisted.
        """
        today = today_str()
        stored_date = self.get("usage_date")
        replies = _to_int(self.get("usage_replies"), 0)

        if stored_date != today:
            # Calendar date changed -> fresh allowance for the new day.
            replies = 0
            self.set("usage_date", today)
            self.set("usage_replies", "0")
            self.set("last_reset_at", _now())

        plan_key = self.get_plan_key()
        plan = plans_mod.get_plan(plan_key)
        limit = plan.daily_limit if plan else plans_mod.TRIAL_DAILY_LIMIT

        return {
            "date": today,
            "replies": replies,
            "limit": limit,
            "plan": plan_key,
        }

    def add_usage(self, count: int = 1) -> Dict[str, Any]:
        """Increment today's reply counter by ``count`` and return usage."""
        usage = self.get_usage()
        new_count = max(0, usage["replies"] + count)
        self.set("usage_replies", str(new_count))
        usage["replies"] = new_count
        return usage

    def reset_usage(self) -> Dict[str, Any]:
        """Manually clear today's counter (Settings -> Reset usage)."""
        self.set("usage_date", today_str())
        self.set("usage_replies", "0")
        return self.get_usage()

    # ------------------------------------------------------------ lifecycle
    def touch(self) -> None:
        """Record the last time the app ran."""
        self.set("last_run_at", _now())


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _now() -> str:
    """Local timestamp ``YYYY-MM-DD HH:MM:SS`` (no extra deps)."""
    from datetime import datetime

    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _to_int(raw: Optional[str], default: int = 0) -> int:
    try:
        return int(str(raw))
    except (TypeError, ValueError):
        return default


def _encode(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        import json

        return json.dumps(value)
    except Exception:
        return str(value)


def _decode(raw: str, default: Any) -> Any:
    import json

    try:
        return json.loads(raw)
    except Exception:
        return raw if raw != "" else default


# --------------------------------------------------------------------------
# process-wide store instance (created lazily so importing is side-effect free)
# --------------------------------------------------------------------------
_store: Optional[Store] = None


def get_store() -> Store:
    """Return the shared :class:`Store`, creating it on first use."""
    global _store
    if _store is None:
        _store = Store()
    return _store


def reset_store_for_tests(db_path: Optional[Path] = None) -> Store:
    """Replace the shared store (used by tests / demos)."""
    global _store
    if _store is not None:
        _store.close()
    _store = Store(db_path)
    return _store


__all__ = ["Store", "get_store", "reset_store_for_tests", "today_str", "DATA_DIR", "DB_FILENAME"]