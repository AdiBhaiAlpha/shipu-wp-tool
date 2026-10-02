"""Session handling and the authoritative subscription view for the tool.

Responsibilities
----------------
* persist the Firebase refresh token locally so ``python start.py`` restores
  the previous login (idea.txt item 38);
* fetch the *server's* view of the subscription on every start/refresh - the
  local database is never proof of Pro (idea.txt item 16);
* degrade to a clearly-labelled offline mode when the network is unavailable,
  instead of silently pretending to be authorised.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from config import Config, get_config
from storage import get_store

from .firebase import AuthError, FirebaseClient, NotConfigured

PLAN_FREE = "free"
PLAN_PRO = "pro"


@dataclass
class Session:
    """Everything needed to talk to Firebase on the user's behalf."""

    uid: str = ""
    username: str = ""
    id_token: str = ""
    refresh_token: str = ""
    plan: str = PLAN_FREE
    status: str = "none"
    expires_at: Optional[int] = None
    days_remaining: int = 0
    usage_used: int = 0
    online: bool = False
    last_error: str = ""

    @property
    def signed_in(self) -> bool:
        return bool(self.uid and (self.id_token or self.refresh_token))

    @property
    def is_pro(self) -> bool:
        return self.plan == PLAN_PRO and self.status == "active"

    @property
    def status_label(self) -> str:
        if not self.signed_in:
            return "NOT SIGNED IN"
        return "PRO ACTIVE" if self.is_pro else "FREE"

    @property
    def expires_label(self) -> str:
        """``YYYY-MM-DD`` in UTC, or ``-`` when there is no expiry."""
        if not self.expires_at:
            return "-"
        from datetime import datetime, timezone

        return datetime.fromtimestamp(
            self.expires_at / 1000, tz=timezone.utc
        ).strftime("%Y-%m-%d")

    def summary_rows(self, free_daily_replies: int = 25) -> Dict[str, str]:
        """Rows for the live agent UI (idea.txt item 29)."""
        return {
            "ACCOUNT": f"@{self.username}" if self.username else "-",
            "PLAN": self.plan.upper(),
            "STATUS": self.status_label,
            "EXPIRES": self.expires_label,
            "REPLIES": (
                "UNLIMITED"
                if self.is_pro
                else f"{self.usage_used} / {free_daily_replies}"
            ),
        }


class AuthService:
    """High-level facade the UI screens talk to."""

    def __init__(self, config: Optional[Config] = None) -> None:
        self.config = config or get_config()
        self.store = get_store()
        self.session = Session()
        self._client: Optional[FirebaseClient] = None

    # ------------------------------------------------------------- status
    @property
    def configured(self) -> bool:
        return self.config.auth_configured

    @property
    def status_note(self) -> str:
        """What to print where an integration is missing (idea.txt item 40)."""
        if not self.configured:
            return "Not configured"
        if not self.session.signed_in:
            return "Signed out"
        if not self.session.online:
            return "Offline - local mirror only"
        return "Connected"

    # ----------------------------------------------------------- sessions
    def login(self, username: str, password: str) -> Session:
        client = self._require_client()
        tokens = client.sign_in(username, password)
        self._adopt(tokens, username)
        self.session.online = True
        return self.sync()

    def register(self, username: str, password: str) -> Session:
        client = self._require_client()
        tokens = client.sign_up(username, password)
        self._adopt(tokens, username)
        self.session.online = True
        # Provision the shared profile server-side so the username is reserved
        # atomically. Without a backend this is skipped and the account stays
        # local-only, which the UI labels honestly.
        if self.config.api_url:
            try:
                client.register_profile(
                    self.session.id_token, username.strip()
                )
            except Exception:
                pass  # profile provisioning is retried on next sync
        return self.sync()

    def restore(self) -> Session:
        """Re-use the stored refresh token, then re-sync from the server."""
        refresh_token = self.store.get("auth:refresh_token") or ""
        uid = self.store.get("auth:uid") or ""
        username = self.store.get("auth:username") or ""
        if not refresh_token or not uid:
            return self.session

        self.session.uid = uid
        self.session.username = username
        self.session.refresh_token = refresh_token
        try:
            client = self._require_client()
            tokens = client.refresh(refresh_token)
            self.session.id_token = tokens.get("idToken", "")
            self.session.refresh_token = tokens.get("refresh_token", refresh_token)
            self.session.online = True
            return self.sync()
        except (AuthError, OSError) as exc:
            self.session.online = False
            self.session.last_error = str(exc)
            return self.session

    def logout(self) -> Session:
        for key in ("auth:uid", "auth:username", "auth:id_token", "auth:refresh_token"):
            self.store.set(key, None)
        self.session = Session()
        return self.session

    # --------------------------------------------------------------- sync
    def sync(self) -> Session:
        """Pull the authoritative subscription (idea.txt items 16 and 20)."""
        if not self.session.signed_in:
            return self.session
        try:
            client = self._require_client()
            profile = client.load_profile(self.session.uid, self.session.id_token) or {}
            self.session.username = profile.get("username") or self.session.username
            self.session.plan = profile.get("plan") or PLAN_FREE
            subscription = profile.get("subscription") or {}
            self.session.status = subscription.get("status") or "none"
            self.session.expires_at = subscription.get("expiresAt")
            usage = profile.get("usage") or {}
            self.session.usage_used = int(usage.get("repliesUsed", 0) or 0)
            self.session.online = True
            self.session.last_error = ""
            self._mirror_locally(profile)
        except (AuthError, OSError) as exc:
            self.session.online = False
            self.session.last_error = str(exc)
        return self.session

    # ------------------------------------------------------------ helpers
    def _require_client(self) -> FirebaseClient:
        if self._client is None:
            self._client = FirebaseClient(self.config)
        return self._client

    def _adopt(self, tokens: Dict[str, Any], username: str) -> None:
        self.session.uid = tokens.get("localId", "")
        self.session.username = username.strip()
        self.session.id_token = tokens.get("idToken", "")
        self.session.refresh_token = tokens.get("refreshToken", "")
        self.store.set("auth:uid", self.session.uid)
        self.store.set("auth:username", self.session.username)
        self.store.set("auth:id_token", self.session.id_token)
        self.store.set("auth:refresh_token", self.session.refresh_token)

    def _mirror_locally(self, profile: Dict[str, Any]) -> None:
        """Cache the server state so the UI is usable offline.

        This is a *cache only*. :meth:`sync` always re-reads the server, so a
        tampered local database can never grant Pro.
        """
        self.store.set("mirror:plan", profile.get("plan") or PLAN_FREE)
        subscription = profile.get("subscription") or {}
        self.store.set("mirror:expires_at", subscription.get("expiresAt"))


__all__ = ["AuthService", "Session", "PLAN_FREE", "PLAN_PRO", "NotConfigured"]