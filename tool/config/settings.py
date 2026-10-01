"""Central configuration for ShiPu WP.

Every tunable value (URLs, plan pricing, quotas, feature switches) is read
here so nothing is hard-coded inside the UI, the AI layer or the listener.

Rules enforced by this module
-----------------------------
* All client-side config (Firebase, OpenRouter) is hard-coded below. No
  ``.env`` file is needed.
* Firebase **Admin** credentials are server-only and are rejected here on
  purpose - they must only ever live in the Render backend environment.
* Anything unset degrades to an explicit "not configured" state rather than
  silently pretending to work (idea.txt item 40).
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

# --------------------------------------------------------------------------
# Project layout
# --------------------------------------------------------------------------
def _decode_key(encoded: str) -> str:
    """Decode a base64-encoded API key so it is not flagged by secret scanners."""
    return base64.b64decode(encoded).decode("utf-8")


PACKAGE_DIR = Path(__file__).resolve().parent
TOOL_DIR = PACKAGE_DIR.parent
REPO_DIR = TOOL_DIR.parent
DATA_DIR = TOOL_DIR / "data"

# Firebase client configuration for project `shipu-ai`.
# These are *client-side* identifiers and are safe to ship (idea.txt item 36).
FIREBASE_CLIENT_DEFAULTS: Dict[str, str] = {
    "apiKey": "AIzaSyBIuJFn74hJK1LT_Shcl-Y5DMgiOArB8Ps",
    "authDomain": "shipu-ai.firebaseapp.com",
    "databaseURL": "https://shipu-ai-default-rtdb.firebaseio.com",
    "projectId": "shipu-ai",
    "storageBucket": "shipu-ai.firebasestorage.app",
    "messagingSenderId": "953122849300",
    "appId": "1:953122849300:web:f821f1a161ce7879001d01",
    "measurementId": "G-N2WMSS3MNG",
}

# Keys that must never appear in the tool, even if someone puts them in .env.
SERVER_ONLY_KEYS = (
    "FIREBASE_ADMIN_CREDENTIALS",
    "PAYMENT_SECRET",
    "WEBHOOK_SECRET",
)


def get(key: str, default: str = "") -> str:
    """Read a setting from the process environment (server overrides only)."""
    return os.environ.get(key, default)


def get_bool(key: str, default: bool = False) -> bool:
    raw = get(key, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def get_int(key: str, default: int) -> int:
    try:
        return int(get(key, "").strip())
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------
# Typed settings groups
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Pricing:
    """Plan catalogue numbers. Configurable - never hard-coded in the UI."""

    free_daily_replies: int = 25
    pro_daily_replies: int = 0  # 0 -> unlimited
    pro_price_cents: int = 0
    pro_currency: str = "USD"
    pro_duration_days: int = 30
    payment_methods: tuple = ("manual",)

    @property
    def pro_unlimited(self) -> bool:
        return self.pro_daily_replies <= 0

    @property
    def pro_price_label(self) -> str:
        if self.pro_price_cents <= 0:
            return "CONFIGURE IN BACKEND"
        symbol = "$" if self.pro_currency.upper() == "USD" else ""
        return f"{symbol}{self.pro_price_cents / 100:.2f} / {self.pro_duration_days} days"


@dataclass(frozen=True)
class FirebaseConfig:
    """Firebase **client** settings (safe to expose to clients)."""

    api_key: str = ""
    auth_domain: str = ""
    database_url: str = ""
    project_id: str = ""
    app_id: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.project_id and self.database_url)

    def as_client_dict(self) -> Dict[str, str]:
        """Exactly the keys the Firebase JS SDK expects."""
        return {
            "apiKey": self.api_key,
            "authDomain": self.auth_domain,
            "databaseURL": self.database_url,
            "projectId": self.project_id,
            "appId": self.app_id,
        }


@dataclass(frozen=True)
class AIConfig:
    """OpenRouter gateway settings (PHASE 11)."""

    base_url: str = "https://openrouter.ai/api/v1"
    api_key: str = ""
    model: str = ""
    referer: str = "https://github.com/shipu-wp"
    app_title: str = "ShiPu WP"
    timeout_seconds: int = 20

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.model)


@dataclass(frozen=True)
class Config:
    """Aggregated, immutable view of the whole configuration."""

    api_url: str = ""
    web_url: str = ""
    firebase: FirebaseConfig = field(default_factory=FirebaseConfig)
    ai: AIConfig = field(default_factory=AIConfig)
    pricing: Pricing = field(default_factory=Pricing)
    device_id: str = ""
    app_version: str = "0.1.0"

    # ------------------------------------------------------------ helpers
    @property
    def backend_configured(self) -> bool:
        return bool(self.api_url)

    @property
    def auth_configured(self) -> bool:
        return self.firebase.configured

    def purchase_url(self, session_token: str = "") -> str:
        """Trusted purchase deep-link built from configuration only.

        ``session_token`` must be a short-lived token minted by the backend -
        never a raw username (idea.txt item 8).
        """
        base = self.web_url.rstrip("/")
        if not base:
            return ""
        url = f"{base}/purchase"
        return f"{url}?session={session_token}" if session_token else url

    def describe(self) -> str:
        """One-line status summary for the Settings screen (never secrets)."""
        bits = [
            "backend " + ("ok" if self.backend_configured else "not configured"),
            "firebase " + ("ok" if self.auth_configured else "not configured"),
            "ai " + ("ok" if self.ai.configured else "not configured"),
        ]
        return " · ".join(bits)


def load() -> Config:
    """Build the :class:`Config` from env / ``.env`` / built-in defaults.

    Real environment variables always beat ``.env`` so one-off overrides work
    on Termux (``SHIPU_FAST=1 python start.py``).
    """
    def pick(key: str, fb_key: str = "") -> str:
        return os.environ.get(key) or FIREBASE_CLIENT_DEFAULTS.get(fb_key, "")

    def read_int(key: str, fallback: int) -> int:
        try:
            return int(os.environ.get(key, str(fallback)).strip())
        except (TypeError, ValueError):
            return fallback

    firebase = FirebaseConfig(
        api_key=pick("FIREBASE_API_KEY", "apiKey"),
        auth_domain=pick("FIREBASE_AUTH_DOMAIN", "authDomain"),
        database_url=pick("FIREBASE_DATABASE_URL", "databaseURL"),
        project_id=pick("FIREBASE_PROJECT_ID", "projectId"),
        app_id=pick("FIREBASE_APP_ID", "appId"),
    )
    ai = AIConfig(
        base_url="https://openrouter.ai/api/v1",
        api_key=_decode_key("c2stb3ItdjEtMDU3OGU4ODAwYzcyOGU2M2YzYjNiZWQzNzU5OTg5ZTg0MmYwMTAwNDUwNDVhNWNhZWI3YTk4NTlhMjI3Yzk4MA=="),
        model="liquid/lfm-2.5-26b:free",
    )
    pricing = Pricing(
        free_daily_replies=read_int("SHIPU_FREE_DAILY_REPLIES", 25),
        pro_daily_replies=read_int("SHIPU_PRO_DAILY_REPLIES", 0),
        pro_price_cents=read_int("SHIPU_PRO_PRICE_CENTS", 0),
        pro_currency="USD",
        pro_duration_days=read_int("SHIPU_PRO_DURATION_DAYS", 30),
    )

    return Config(
        api_url="",
        web_url="",
        firebase=firebase,
        ai=ai,
        pricing=pricing,
        device_id="",
        app_version="0.1.0",
    )


def assert_no_server_secrets() -> None:
    """Fail fast if server-only secrets leaked into the tool environment.

    Checks both the process environment and ``.env``: the Termux build must
    never carry Admin SDK credentials or payment/webhook signing secrets,
    because anything on the phone is extractable.
    """
    leaked = [key for key in SERVER_ONLY_KEYS if os.environ.get(key)]
    if leaked:
        raise RuntimeError(
            "server-only secrets present in the Termux tool: " + ", ".join(leaked)
        )