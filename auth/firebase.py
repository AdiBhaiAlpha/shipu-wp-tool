"""Firebase client for the ShiPu WP Termux tool.

Deliberately REST-only
----------------------
Termux has no wheels for the Firebase Python SDK, and shipping it would bloat
the install. The Identity Toolkit + Realtime Database REST endpoints cover
exactly what we need and keep the dependency list to a single HTTP client.

Nothing here is privileged: the client can only ever read its own
``users/{uid}`` record, and ``plan`` / ``subscription`` / ``payments`` are
write-locked by ``firebase/database.rules.json``.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional

from config import Config

IDENTITY_TOOLKIT = "https://identitytoolkit.googleapis.com/v1"

# Every ShiPu path lives under this namespace. The ``shipu-ai`` project is
# shared with another app that owns the root ``users/`` and ``bot/`` nodes, so
# ShiPu must never read or write them. Keep in sync with ``firebase/schema.md``,
# ``firebase/database.rules.json`` and ``dist/assets/firebase-config.js``.
SHIPU_ROOT = "shipuwp"


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
) -> Dict[str, Any]:
    """Minimal JSON HTTP helper that never leaks the API key into errors."""
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            return json.loads(body) if body else {}
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
    }
    return mapping.get(message, message.replace("_", " ").capitalize())


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
    def _db_root(self) -> str:
        return (self._fb.database_url or "").rstrip("/")

    def _path(self, *parts: str) -> str:
        """Build a ShiPu-namespaced RTDB path."""
        segments = [SHIPU_ROOT] + [str(x).strip("/") for x in parts if str(x).strip("/")]
        return "/".join(segments)

    def synthetic_email(username: str) -> str:
        """Firebase only speaks email/password, so map username -> synthetic email.

        This keeps one account per username, shared by the website and the
        Termux tool (idea.txt item 6).
        """
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
        url = f"{self._db_root}/{path}.json?auth={urllib.parse.quote(id_token)}"
        return _request(url, method="GET").get("value")

    # ---------------------------------------------------------------- auth
    def sign_in(self, username: str, password: str) -> Dict[str, Any]:
        """Email/password sign-in. Returns the Firebase token bundle."""
        payload = {
            "email": self.synthetic_email(username),
            "password": password,
            "returnSecureToken": True,
        }
        return _request(self._auth_url, payload)

    def sign_up(self, username: str, password: str) -> Dict[str, Any]:
        """Register a username. Profile creation happens server-side."""
        payload = {
            "email": self.synthetic_email(username),
            "password": password,
            "returnSecureToken": True,
        }
        return _request(self._sign_up_url, payload)

    def refresh(self, refresh_token: str) -> Dict[str, Any]:
        """Exchange a refresh token for a fresh ID token."""
        url = (
            f"https://securetoken.googleapis.com/v1/token"
            f"?key={self._fb.api_key}"
        )
        return _request(
            url,
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
        )

    # ------------------------------------------------------------ database
    def _db_get(self, path: str, id_token: str) -> Any:
        """Authenticated read. ``path`` is relative to the ShiPu namespace."""
        if not self._db_root:
            raise AuthError("Firebase databaseURL is not configured.", "not_configured")
        if not id_token:
            raise AuthError("Sign in first.", "unauthenticated")
        url = (
            f"{self._db_root}/{self._path(path)}.json"
            f"?auth={urllib.parse.quote(id_token)}"
        )
        return _request(url, method="GET").get("value")

    def _db_put(self, path: str, value: Any, id_token: str) -> None:
        """Authenticated write. Privileged fields stay write-locked by rules."""
        if not self._db_root:
            raise AuthError("Firebase databaseURL is not configured.", "not_configured")
        if not id_token:
            raise AuthError("Sign in first.", "unauthenticated")
        url = (
            f"{self._db_root}/{self._path(path)}.json"
            f"?auth={urllib.parse.quote(id_token)}"
        )
        _request(url, value, method="PUT")

    def load_profile(self, uid: str, id_token: str) -> Optional[Dict[str, Any]]:
        """Read the shared account record ``shipuwp/users/{uid}``.

        The same record backs the website, so the tool and the web never
        disagree about plan or expiry (idea.txt item 6).
        """
        record = self._db_get(f"users/{uid}", id_token)
        return record if isinstance(record, dict) else None

    def username_exists(self, username: str, id_token: str = "") -> bool:
        """Whether a username is already claimed.

        ``shipuwp/usernameIndex`` is admin-readable only, so this honestly
        returns ``False`` for ordinary users: the hint is best-effort and
        uniqueness is enforced server-side by the Admin SDK transaction, which
        no client can bypass.
        """
        if not id_token:
            return False
        try:
            record = self._db_get(f"usernameIndex/{username.strip().lower()}", id_token)
        except AuthError:
            return False
        return bool(record)

    def record_usage(
        self, uid: str, date_str: str, replies_used: int, id_token: str
    ) -> None:
        """Mirror today's counter to ``shipuwp/users/{uid}/usage``.

        Rules cap this at ``previous + 25`` per write so a client cannot erase
        or inflate its usage; ``plan`` and ``subscription`` remain unwritable.
        """
        self._db_put(
            f"users/{uid}/usage",
            {"date": date_str, "repliesUsed": int(replies_used)},
            id_token,
        )

    def register_profile(self, id_token: str, username: str) -> Dict[str, Any]:
        """Create the shared profile via the backend.

        Username uniqueness is enforced server-side by an Admin SDK
        transaction, so a duplicate here is a 409 rather than a silent
        overwrite. Skipped entirely when no backend URL is configured.
        """
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


__all__ = ["AuthError", "FirebaseClient", "NotConfigured", "SHIPU_ROOT", "_request"]
