"""Central configuration for ShiPu WP.

Every tunable value (URLs, plan pricing, quotas, feature switches) is read
here so nothing is hard-coded inside the UI, the AI layer or the listener.

Rules enforced by this module
-----------------------------
* Secrets are only ever read from the environment or ``.env``. They are never
  committed, never printed, and :func:`describe` redacts them.
* Firebase *client* config is safe to embed (see ``.env.example``); Firebase
  **Admin** credentials are server-only and are rejected here on purpose.
* Anything unset degrades to an explicit "not configured" state rather than
  silently pretending to work (idea.txt item 40).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

# --------------------------------------------------------------------------
# Project layout
# --------------------------------------------------------------------------
PACKAGE_DIR = Path(__file__).resolve().parent
TOOL_DIR = PACKAGE_DIR.parent
REPO_DIR = TOOL_DIR.parent
ENV_FILE = REPO_DIR / ".env"
DATA_DIR = TOOL_DIR / "data"

# Firebase client configuration for project `shipu-ai`.
# These are *client-side* identifiers and are safe to ship (idea.txt item 36).
# They are defaults only - a real deployment overrides them via .env.
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


def load_env(path: Optional[Path] = None) -> Dict[str, str]:
    """Minimal ``.env`` reader (no third-party dependency).

    Supports ``KEY=value``, ``# comments``, blank lines and quoted values.
    Existing environment variables always win, so ``SHIPU_FAST=1 python ...``
    behaves as expected on Termux.
    """
    path = Path(path) if path else ENV_FILE
    values: Dict[str, str] = {}
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return values

    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def get(key: str, default: str = "") -> str:
    """Read a setting: process env first, then ``.env``."""
    return os.environ.get(key) or load_env().get(key, default)


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
    env = load_env()

    def read(key: str, fallback: str = "") -> str:
        """Process env first, then .env, then the supplied fallback."""
        return os.environ.get(key) or env.get(key) or fallback

    def pick(key: str, fb_key: str = "") -> str:
        return read(key, FIREBASE_CLIENT_DEFAULTS.get(fb_key, ""))

    def read_int(key: str, fallback: int) -> int:
        try:
            return int(read(key, str(fallback)).strip())
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
        base_url=read("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
        api_key=read("OPENROUTER_API_KEY"),
        model=read("OPENROUTER_MODEL"),
    )
    pricing = Pricing(
        free_daily_replies=read_int("SHIPU_FREE_DAILY_REPLIES", 25),
        pro_daily_replies=read_int("SHIPU_PRO_DAILY_REPLIES", 0),
        pro_price_cents=read_int("SHIPU_PRO_PRICE_CENTS", 0),
        pro_currency=read("SHIPU_PRO_CURRENCY", "USD"),
        pro_duration_days=read_int("SHIPU_PRO_DURATION_DAYS", 30),
    )

    return Config(
        api_url=read("SHIPU_API_URL"),
        web_url=read("SHIPU_WEB_URL"),
        firebase=firebase,
        ai=ai,
        pricing=pricing,
        device_id=read("SHIPU_DEVICE_ID"),
        app_version=read("SHIPU_APP_VERSION", "0.1.0"),
    )


def assert_no_server_secrets() -> None:
    """Fail fast if server-only secrets leaked into the tool environment.

    Checks both the process environment and ``.env``: the Termux build must
    never carry Admin SDK credentials or payment/webhook signing secrets,
    because anything on the phone is extractable.
    """
    env_file = load_env()
    leaked = [
        key
        for key in SERVER_ONLY_KEYS
        if os.environ.get(key) or env_file.get(key)
    ]
    if leaked:
        raise RuntimeError(
            "server-only secrets present in the Termux tool: " + ", ".join(leaked)
        )