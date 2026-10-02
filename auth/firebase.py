"""Firebase client for the ShiPu WP Termux tool.

Deliberately REST-only
----------------------
Termux has no wheels for the Firebase Python SDK, and shipping it would bloat
the install. The Identity Toolkit + Realtime Database REST endpoints cover
exactly what we need and keep the dependency list to a single HTTP client.

Schema
------
Live paths (keep in sync with ``firebase/database.rules.json`` in the website
repo):

``users/{uid}``                canonical record
``users/user-{username_slug}`` lookup key the tool reads first
``payments/{paymentId}``       one order, status owned by the server

``plan`` / ``subscription`` / ``role`` and a payment's ``status`` are NOT
writable by any client - the website server writes them through the Firebase
Admin SDK. So the tool can read entitlements but can never grant itself Pro.

REST auth gotcha
----------------
This RTDB accepts ``?auth=<idToken>`` but rejects the ``Authorization: Bearer``
header with ``Unauthorized request.``. Always go through ``_db_get``.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from config import Config

IDENTITY_TOOLKIT = "https://identitytoolkit.googleapis.com/v1"

# Canonical root nodes. Kept as constants so a schema move is a one-line change.
USERS_ROOT = "users"
PAYMENTS_ROOT = "payments"

#: Free accounts get this many AI replies per day.
FREE_DAILY_REPLIES = 25

_ACTIVE_STATUSES = {"active", "trial", "trialing"}


class AuthError(Exception):
    """Login/registration failure with a message safe to show a user."""

    def __init__(self, message: str, code: str = "auth_error") -> None:
        super().__init__(message)
        self.code = code


class NotConfigured(AuthError):
    def __init__(self) -> None:
        super().__init__("Firebase is not configured for this build.", "not_configured")


def _request(
    url: str,
    payload: Optional[Dict[str, Any]] = None,
    method: str = "POST",
    timeout: int = 15,
) -> Any:
    """Minimal JSON HTTP helper that never leaks the API key into errors.

    Returns the parsed JSON as-is. RTDB ``GET /path.json`` replies with a bare
    value (``null``, a list, or an object) rather than a ``{"value": ...}
    envelope, so callers must not assume a dict.
    """
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            return json.loads(body) if body else None
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8"))
        except Exception:
            detail = {}
        message = (
            detail.get("error", {}).get("message")
            or detail.get("error", {}).get("description")
            or f"HTTP {exc.code}"
        )
        raise AuthError(_friendly(message), code=message) from exc
    except urllib.error.URLError as exc:
        raise AuthError(f"Network error: {exc.reason}", "network") from exc


def _friendly(message: str) -> str:
    """Translate Firebase's verbose error strings into terminal-friendly text."""
    mapping = {
        "EMAIL_NOT_FOUND": "No account found for that username.",
        "INVALID_PASSWORD": "Incorrect password.",
        "EMAIL_EXISTS": "That username is already taken.",
        "INVALID_EMAIL": "Username must be 3-32 characters (letters, digits, _).",
        "WEAK_PASSWORD": "Password must be at least 6 characters.",
        "INVALID_LOGIN_CREDENTIALS": "Incorrect username or password.",
        "TOO_MANY_ATTEMPTS_TRY_LATER": "Too many attempts. Try again later.",
        "USER_DISABLED": "This account has been suspended.",
        "Permission denied": "Firebase denied the request. Try signing in again.",
        "Unauthorized request": "Firebase rejected the session token. Sign in again.",
    }
    return mapping.get(message, message.replace("_", " ").capitalize())


def username_slug(username: str) -> str:
    """``"Chowdhury Onup Amir"`` -> ``"chowdhury-onup-amir"``.

    Lowercase, and every non-alphanumeric run becomes a single hyphen. Must
    match the website's ``syncUserToFirebase`` exactly or the tool will look up
    a key that was never written.
    """
    clean = re.sub(r"[^a-z0-9]+", "-", (username or "").strip().lower())
    return clean.strip("-")


def slug_key(username: str) -> str:
    """Full RTDB key for a username: ``user-chowdhury-onup-amir``."""
    return f"user-{username_slug(username)}"


def is_pro(record: Optional[Dict[str, Any]], now_ms: Optional[int] = None) -> bool:
    """Entitlement check.

    Pro requires *all three*: ``plan == "pro"``, an active subscription status,
    and an expiry that is still in the future. Anything else is free, so a
    stale or half-written record can never grant Pro.
    """
    if not isinstance(record, dict):
        return False
    if str(record.get("plan", "")).strip().lower() != "pro":
        return False
    subscription = record.get("subscription")
    if not isinstance(subscription, dict):
        return False
    if str(subscription.get("status", "")).strip().lower() not in _ACTIVE_STATUSES:
        return False
    expires_at = subscription.get("expiresAt")
    if not isinstance(expires_at, (int, float)):
        return False
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    return expires_at > now


class FirebaseClient:
    """Auth + Realtime Database access for the signed-in user."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self._fb = config.firebase
        if not self._fb.configured:
            raise NotConfigured()

    # ------------------------------------------------------------- helpers
    @property
    def _auth_url(self) -> str:
        return f"{IDENTITY_TOOLKIT}/accounts:signInWithPassword?key={self._fb.api_key}"

    @property
    def _sign_up_url(self) -> str:
        return f"{IDENTITY_TOOLKIT}/accounts:signUp?key={self._fb.api_key}"

    @property
    def _anon_url(self) -> str:
        return f"{IDENTITY_TOOLKIT}/accounts:signUp?key={self._fb.api_key}"

    @property
    def _db_root(self) -> str:
        return (self._fb.database_url or "").rstrip("/")

    def synthetic_email(username: str) -> str:
        """Firebase only speaks email/password, so map username -> synthetic email."""
        clean = username.strip().lower()
        if not (3 <= len(clean) <= 32) or not all(
            c.isalnum() or c in "._-" for c in clean
        ):
            raise AuthError(
                "Username must be 3-32 characters (letters, digits, _ . -).",
                "INVALID_EMAIL",
            )
        return f"{clean}@shipu-wp.local"

    def _db_get(self, path: str, id_token: str) -> Any:
        """Authenticated read via ``?auth=`` (the header form is rejected)."""
        if not self._db_root:
            raise AuthError("Firebase databaseURL is not configured.", "not_configured")
        if not id_token:
            raise AuthError("Sign in first.", "unauthenticated")
        url = (
            f"{self._db_root}/{path}.json"
            f"?auth={urllib.parse.quote(id_token)}"
        )
        return _request(url, method="GET")

    def _db_patch(self, path: str, value: Any, id_token: str) -> None:
        """Authenticated partial write. Privileged fields stay write-locked."""
        if not self._db_root:
            raise AuthError("Firebase databaseURL is not configured.", "not_configured")
        if not id_token:
            raise AuthError("Sign in first.", "unauthenticated")
        url = (
            f"{self._db_root}/{path}.json"
            f"?auth={urllib.parse.quote(id_token)}"
        )
        _request(url, value, method="PATCH")

    # ---------------------------------------------------------------- auth
    def sign_in(self, username: str, password: str) -> Dict[str, Any]:
        """Email/password sign-in. Returns the Firebase token bundle."""
        payload = {
            "email": self.synthetic_email(username),
            "password": password,
            "returnSecureToken": True,
        }
        return _request(self._auth_url, payload) or {}

    def sign_up(self, username: str, password: str) -> Dict[str, Any]:
        """Register a username. Profile creation happens server-side."""
        payload = {
            "email": self.synthetic_email(username),
            "password": password,
            "returnSecureToken": True,
        }
        return _request(self._sign_up_url, payload) or {}

    def anonymous_token(self) -> str:
        """Get an anonymous ID token so reads satisfy ``auth != null``.

        RTDB rules require authentication for ``users/``. Anonymous sign-up is
        the cheapest way to satisfy that without inventing a password, and it
        grants no write access beyond the public profile fields.
        """
        payload = {"returnSecureToken": True}
        bundle = _request(self._anon_url, payload) or {}
        return bundle.get("idToken", "")

    def refresh(self, refresh_token: str) -> Dict[str, Any]:
        """Exchange a refresh token for a fresh ID token."""
        url = f"https://securetoken.googleapis.com/v1/token?key={self._fb.api_key}"
        return _request(
            url,
            {"grant_type": "refresh_token", "refresh_token": refresh_token},
        )

    # ------------------------------------------------------------ database
    def load_profile(
        self,
        uid: str = "",
        id_token: str = "",
        username: str = "",
    ) -> Optional[Dict[str, Any]]:
        """Read the account record.

        Tries ``users/user-{slug}`` first (the key the website writes for every
        account), then falls back to ``users/{uid}``. Returns ``None`` when the
        account has not been provisioned yet.
        """
        token = id_token or ""
        if not token:
            token = self.anonymous_token()

        if username:
            by_slug = self._db_get(f"{USERS_ROOT}/{slug_key(username)}", token)
            if isinstance(by_slug, dict) and by_slug:
                return by_slug

        if uid:
            by_uid = self._db_get(f"{USERS_ROOT}/{uid}", token)
            if isinstance(by_uid, dict) and by_uid:
                return by_uid

        return None

    def find_user_by_name(
        self, needle: str, id_token: str = ""
    ) -> Optional[Dict[str, Any]]:
        """Fallback search across ``users/`` by username or termuxUsername.

        Needed because the exact slug may not match (typo, underscore vs hyphen,
        or the account only exists under its Firebase UID key).
        """
        needle = (needle or "").strip().lower()
        if not needle:
            return None
        token = id_token or self.anonymous_token()
        everyone = self._db_get(USERS_ROOT, token)
        if not isinstance(everyone, dict):
            return None
        for key, record in everyone.items():
            if not isinstance(record, dict):
                continue
            candidates = [
                str(record.get("username", "")).lower(),
                str(record.get("termuxUsername", "")).lower(),
                str(record.get("name", "")).lower(),
                str(record.get("nickname", "")).lower(),
                str(record.get("email", "")).lower().split("@")[0],
                key.lower(),
                key.lower().removeprefix("user-"),
            ]
            if any(c and (needle == c or needle in c or c in needle) for c in candidates):
                return record
        return None

    def load_subscription(
        self,
        uid: str = "",
        id_token: str = "",
        username: str = "",
        termux_username: str = "",
    ) -> Optional[Dict[str, Any]]:
        """Resolve the entitlement, falling back to a name scan."""
        record = self.load_profile(uid=uid, id_token=id_token, username=username)
        if record is None and (termux_username or username):
            record = self.find_user_by_name(
                termux_username or username, id_token=id_token
            )
        return record

    def is_pro(self, uid: str = "", id_token: str = "", username: str = "") -> bool:
        """Convenience wrapper: is this account currently Pro?"""
        return is_pro(self.load_profile(uid=uid, id_token=id_token, username=username))

    def load_payment(self, payment_id: str, id_token: str = "") -> Optional[Dict[str, Any]]:
        """Read one order from ``payments/{paymentId}``."""
        if not payment_id:
            return None
        token = id_token or self.anonymous_token()
        record = self._db_get(f"{PAYMENTS_ROOT}/{payment_id}", token)
        return record if isinstance(record, dict) else None

    def payment_status(self, payment_id: str, id_token: str = "") -> str:
        """``"pending"`` / ``"verified"`` / ``"rejected"``, or ``"unknown"``."""
        record = self.load_payment(payment_id, id_token=id_token)
        if not record:
            return "unknown"
        return str(record.get("status", "unknown")).lower()

    def username_exists(self, username: str, id_token: str = "") -> bool:
        """Whether a username is already claimed (best-effort hint)."""
        try:
            token = id_token or self.anonymous_token()
            record = self._db_get(f"{USERS_ROOT}/{slug_key(username)}", token)
            return bool(record)
        except AuthError:
            return False

    def record_usage(
        self, uid: str, date_str: str, replies_used: int, id_token: str
    ) -> None:
        """Mirror today's counter to ``users/{uid}/usage``.

        Best-effort: a failure here must never block a reply.
        """
        if not uid:
            return
        try:
            self._db_patch(
                f"{USERS_ROOT}/{uid}/usage/{date_str}",
                {"date": date_str, "model": "deepseek-chat", "repliesUsed": int(replies_used)},
                id_token,
            )
        except AuthError:
            pass

    def register_profile(self, id_token: str, username: str) -> Dict[str, Any]:
        """Create the shared profile via the backend."""
        if not self.config.api_url:
            raise AuthError("backend is not configured", "not_configured")
        url = f"{self.config.api_url.rstrip('/')}/account/register"
        request = urllib.request.Request(
            url,
            data=json.dumps({"username": username.strip()}).encode(),
            method="POST",
        )
        request.add_header("Content-Type", "application/json")
        request.add_header("Authorization", f"Bearer {id_token}")
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                body = response.read().decode("utf-8")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode("utf-8"))
            except Exception:
                detail = {}
            message = detail.get("error") or detail.get("message") or f"HTTP {exc.code}"
            raise AuthError(_friendly(str(message)), code=str(message)) from exc

    # Backwards-compatible alias used by older call sites.
    push_daily_usage = record_usage


__all__ = [
    "AuthError",
    "FirebaseClient",
    "NotConfigured",
    "USERS_ROOT",
    "PAYMENTS_ROOT",
    "FREE_DAILY_REPLIES",
    "is_pro",
    "username_slug",
    "slug_key",
    "_request",
]
# Compatibility shim for older callers
def _synthetic_email_flex(*args, **kwargs):
    username = None
    for a in args:
        if isinstance(a, str):
            if a.startswith('FirebaseClient'):
                continue
            username = a
            break
    if username is None:
        for v in kwargs.values():
            if isinstance(v, str):
                username = v
                break
    if username is None:
        username = ''
    clean = str(username).strip().lower()
    if not (3 <= len(clean) <= 32) or not all(c.isalnum() or c in '._-' for c in clean):
        raise AuthError("Username must be 3-32 characters (letters, digits, _ . -).", "INVALID_EMAIL")
    return f"{clean}@shipu-wp.local"

FirebaseClient.synthetic_email = staticmethod(_synthetic_email_flex)
