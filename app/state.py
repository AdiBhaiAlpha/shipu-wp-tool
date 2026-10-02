"""Application state and mock domain model for ShiPu WP.

:class:`AppState` is the single place screens talk to. It owns a
:class:`~app.storage.Store`, exposes the derived numbers every screen needs
(plan, usage, remaining, status) and provides the handful of *actions* the UI
can perform today.

All AI / WhatsApp / billing behaviour is **mocked**: :meth:`AppState.simulate_agent_run`
just advances a counter and appends a fake transcript entry so the interface
has realistic data to render.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from . import plans as plans_mod
from .plans import Plan
from .storage import Store, get_store, today_str

# --------------------------------------------------------------------------
# Mock data (clearly labelled as demo content in the UI)
# --------------------------------------------------------------------------
MOCK_CONTACTS = (
    "+880 1712 448 210",
    "+880 1990 335 771",
    "+880 1601 902 455",
    "+971 50 774 2210",
)

MOCK_SNIPPETS = (
    "Can we move tomorrow's call to 4pm?",
    "Please send the invoice for October.",
    "Is the new feature live yet?",
    "Thanks, that worked perfectly.",
    "Where can I download the report?",
    "Let's confirm the venue for Friday.",
)

DEFAULT_SETTINGS: Dict[str, Any] = {
    "animations": True,
    "demo_mode": True,
    "compact_layout": False,
    "confirm_quit": True,
}


@dataclass
class TranscriptEntry:
    """One simulated agent reply shown in the Usage screen."""

    stamp: str
    contact: str
    text: str
    tone: str = "balanced"


@dataclass
class AppState:
    """Everything the UI needs, independent of how it is rendered."""

    store: Store = field(default_factory=get_store)

    # ------------------------------------------------------------ account
    # The auth service is optional so the app still runs (in a clearly labelled
    # local-only mode) when Firebase is not configured.
    auth: Any = None
    username: str = ""

    def bind_auth(self, auth) -> None:
        """Attach an :class:`~auth.service.AuthService` (or ``None``)."""
        self.auth = auth
        if auth is not None and getattr(auth, "session", None) is not None:
            self.username = getattr(auth.session, "username", "") or self.username

    def apply_session(self, session) -> None:
        """Mirror the authoritative session into the local view.

        The session is the *server's* answer, so this never grants anything -
        it only reflects what the server already decided.
        """
        if session is None:
            return
        self.username = getattr(session, "username", "") or self.username
        plan = getattr(session, "plan", "free")
        if plan:
            self.store.set("mirror:plan", plan)
        expires = getattr(session, "expires_at", None)
        if expires:
            self.store.set("mirror:expires_at", expires)
        used = getattr(session, "usage_used", 0)
        if used:
            self.store.set("mirror:usage_used", used)

    # ------------------------------------------------------------ lifecycle
    def bootstrap(self) -> Dict[str, Any]:
        """Record this run and return the initial context for the UI."""
        self.store.touch()
        return {
            "first_run": self.store.first_run,
            "plan": self.plan,
            "usage": self.usage,
            "persistent": self.store.persistent,
            "db_path": str(self.store.db_path),
        }

    # ----------------------------------------------------------------- plan
    @property
    def plan_key(self) -> Optional[str]:
        return self.store.get_plan_key()

    @property
    def plan(self) -> Optional[Plan]:
        return plans_mod.get_plan(self.plan_key)

    @property
    def has_plan(self) -> bool:
        return self.plan is not None

    def select_plan(self, plan_key: str) -> Plan:
        """Persist a plan choice and complete the first-run flag."""
        plan = plans_mod.get_plan(plan_key)
        if plan is None:
            raise ValueError(f"unknown plan: {plan_key}")
        self.store.set_plan_key(plan.key)
        self.store.mark_first_run_complete()
        return plan

    def clear_plan(self) -> None:
        """Drop the plan (Settings -> reset) without touching trial usage."""
        self.store.set_plan_key(None)

    # ---------------------------------------------------------------- usage
    @property
    def usage(self) -> Dict[str, Any]:
        return self.store.get_usage()

    @property
    def used(self) -> int:
        return int(self.usage["replies"])

    @property
    def limit(self) -> Optional[int]:
        return self.usage["limit"]

    @property
    def is_unlimited(self) -> bool:
        return self.limit is None

    @property
    def remaining(self) -> Optional[int]:
        if self.limit is None:
            return None
        return max(0, self.limit - self.used)

    @property
    def fraction(self) -> float:
        """Usage as a 0.0-1.0 fraction (0.0 when unlimited)."""
        if self.limit in (None, 0):
            return 0.0
        return min(1.0, self.used / float(self.limit))

    @property
    def is_exhausted(self) -> bool:
        return self.limit is not None and self.used >= self.limit

    @property
    def usage_tone(self) -> str:
        """Palette key describing how close the user is to the ceiling."""
        if self.is_unlimited:
            return "secondary"
        if self.is_exhausted:
            return "error"
        if self.fraction >= 0.8:
            return "warning"
        return "success"

    @property
    def status_label(self) -> str:
        """``ACTIVE`` / ``LIMIT REACHED`` / ``PENDING`` / ``READY``."""
        if not self.has_plan:
            return "PENDING"
        if self.is_exhausted:
            return "LIMIT REACHED"
        return "ACTIVE"

    @property
    def status_tone(self) -> str:
        return {
            "ACTIVE": "success",
            "LIMIT REACHED": "warning",
            "PENDING": "muted",
        }[self.status_label]

    # ------------------------------------------------------------- actions
    def consume_reply(self, count: int = 1) -> Dict[str, Any]:
        """Record a simulated AI reply against today's allowance."""
        return self.store.add_usage(count)

    def reset_usage(self) -> Dict[str, Any]:
        return self.store.reset_usage()

    # ------------------------------------------------------------ settings
    @property
    def settings(self) -> Dict[str, Any]:
        merged = dict(DEFAULT_SETTINGS)
        merged.update(self.store.all_settings())
        return merged

    def get_setting(self, key: str) -> Any:
        return self.settings.get(key, DEFAULT_SETTINGS.get(key))

    def toggle_setting(self, key: str) -> bool:
        """Flip a boolean setting and persist it."""
        new_value = not bool(self.get_setting(key))
        self.store.set_setting(key, new_value)
        return new_value

    def set_setting(self, key: str, value: Any) -> None:
        self.store.set_setting(key, value)

    # --------------------------------------------------------- mock history
    def history(self, days: int = 7) -> List[Dict[str, Any]]:
        """Seven-day usage history: real today + deterministic demo past."""
        today = date.today()
        # Unlimited plans still need a scale for the sparkline; the demo cap is
        # used for seeding past days only.
        seed_limit = self.limit if self.limit is not None else plans_mod.TRIAL_DAILY_LIMIT
        rows: List[Dict[str, Any]] = []
        for offset in range(days - 1, -1, -1):
            day = today - timedelta(days=offset)
            key = day.isoformat()
            if key == today_str():
                count = self.used
            else:
                # Deterministic pseudo-history so the demo looks consistent
                # between runs without pretending to be real analytics.
                rng = random.Random(f"shipu-wp-{key}")
                count = rng.randint(0, seed_limit)
            rows.append(
                {
                    "date": key,
                    "label": day.strftime("%a").upper(),
                    "replies": count,
                    "limit": self.limit,
                    "is_today": key == today_str(),
                }
            )

        # Cap-based plans measure against their limit; unlimited plans measure
        # against the busiest day in the window (a relative sparkline).
        if self.limit:
            for row in rows:
                row["fraction"] = min(1.0, row["replies"] / float(self.limit))
        else:
            peak = max((row["replies"] for row in rows), default=0) or 1
            for row in rows:
                row["fraction"] = row["replies"] / peak
        return rows

    @property
    def week_total(self) -> int:
        return sum(row["replies"] for row in self.history())

    def mock_transcript(self, count: int = 4) -> List[TranscriptEntry]:
        """Deterministic sample replies for the Usage screen (demo data)."""
        rng = random.Random(f"shipu-wp-transcript-{today_str()}")
        entries: List[TranscriptEntry] = []
        used = self.used
        for index in range(min(count, used)):
            stamp_index = 9 * 60 + 40 - index * 7
            hour, minute = divmod(max(0, stamp_index), 60)
            entries.append(
                TranscriptEntry(
                    stamp=f"{hour:02d}:{minute:02d}",
                    contact=MOCK_CONTACTS[index % len(MOCK_CONTACTS)],
                    text=MOCK_SNIPPETS[index % len(MOCK_SNIPPETS)],
                    tone=("concise", "balanced", "friendly")[index % 3],
                )
            )
        if not entries:
            rng.shuffle(list(MOCK_CONTACTS))  # keeps rng usage explicit
        return entries

    # -------------------------------------------------------------- status
    def agent_status(self) -> Dict[str, str]:
        """Status rows for the agent dashboard header."""
        return {
            "PLAN": self.plan.short_name if self.plan else "NONE",
            "STATUS": self.status_label,
            "TODAY": (
                "NO PLAN"
                if not self.has_plan
                else "UNLIMITED"
                if self.is_unlimited
                else f"{self.used} / {self.limit} REPLIES"
            ),
            "AI ENGINE": "READY",
            "WHATSAPP": "NOT CONNECTED",
        }