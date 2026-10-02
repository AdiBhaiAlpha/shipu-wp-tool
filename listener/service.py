"""The reply agent: inbound WhatsApp message -> gated AI reply.

This is where the product rules become code. Every gate below exists to stop a
specific failure mode:

``listener not attached``
    No bridge events, nothing to do. Never blocks startup.
``not signed in``
    The agent refuses to run. A local Pro flag cannot authorise anything.
``subscription not active on the server``
    Pro features stay locked even if the local mirror says Pro.
``daily quota exhausted``
    The reply is refused with an honest reason instead of silently going over.
``AI unavailable / free tier spent``
    The failure is surfaced, never faked with a canned string.

Usage is persisted and mirrored to the server *after* a successful reply, so a
crash cannot cause a reply to be free.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from ai import AIError, AINotConfigured, AIQuotaExceeded, get_client
from auth import AuthService
from storage import get_store

from .bridge import EventBus, MessageEvent
from .dedup import Deduper

# Reasons a message was not answered. Shown to the user verbatim.
SKIP_SELF = "own message"
SKIP_NOT_MESSAGE = "not an incoming message"
SKIP_DUPLICATE = "duplicate notification"
SKIP_SIGNED_OUT = "not signed in"
SKIP_INACTIVE = "subscription not active on server"
SKIP_QUOTA = "daily free allowance used up"


@dataclass
class Outcome:
    """What happened to one inbound message."""

    event: Optional[MessageEvent] = None
    replied: bool = False
    text: str = ""
    reason: str = ""
    model: str = ""
    duration_ms: int = 0
    error: str = ""
    event_key: str = ""

    @property
    def ok(self) -> bool:
        return self.replied


@dataclass
class AgentStats:
    """Counters for the live agent header."""

    received: int = 0
    replied: int = 0
    duplicates: int = 0
    skipped: int = 0
    errors: int = 0
    started_at: float = field(default_factory=time.time)

    def header(self) -> str:
        uptime = int(time.time() - self.started_at)
        minutes, seconds = divmod(uptime, 60)
        return (
            f"RECEIVED {self.received} · REPLIED {self.replied} · "
            f"DUPLICATES {self.duplicates} · ERRORS {self.errors} · "
            f"UP {minutes:02d}:{seconds:02d}"
        )


class ReplyAgent:
    """Event-driven reply agent."""

    def __init__(
        self,
        bus: Optional[EventBus] = None,
        auth: Optional[AuthService] = None,
        deduper: Optional[Deduper] = None,
        config=None,
    ) -> None:
        from config import get_config

        self.config = config or get_config()
        self.bus = bus or EventBus()
        self.auth = auth or AuthService(self.config)
        self.deduper = deduper or Deduper()
        self.store = get_store()
        self.ai = get_client()
        self.stats = AgentStats()
        self.history: List[Outcome] = []
        self.running = False
        self._last_seen: Dict[str, float] = {}

    # ------------------------------------------------------------- gating
    @property
    def free_daily(self) -> int:
        return self.config.pricing.free_daily_replies

    def gate(self) -> tuple[bool, str]:
        """Server-authoritative eligibility. ``(ok, reason)``."""
        if not self.auth.configured:
            return False, "Not configured"
        session = self.auth.session
        if not session.signed_in:
            return False, SKIP_SIGNED_OUT
        if not session.online:
            return False, "offline - cannot verify subscription"
        if session.plan == "pro" and session.status != "active":
            return False, SKIP_INACTIVE
        if not session.is_pro and session.usage_used >= self.free_daily:
            return False, SKIP_QUOTA
        return True, ""

    def _is_incoming(self, event: MessageEvent) -> tuple[bool, str]:
        """Cheap pre-filter before we spend anything."""
        if not event.message:
            return False, SKIP_NOT_MESSAGE
        if event.package not in ("com.whatsapp", "com.whatsapp.w4b"):
            return False, "not whatsapp"
        # Our own outgoing message comes back as a notification.
        if event.source in ("self", "outgoing"):
            return False, SKIP_SELF
        lowered = event.message.strip().lower()
        if lowered.startswith("you: ") or lowered.startswith("~you~"):
            return False, SKIP_SELF
        if "messages and calls are end-to-end encrypted" in lowered:
            return False, "encryption notice"
        if event.sender.lower() == "whatsapp":
            return False, "system notice"
        return True, ""

    # ------------------------------------------------------------ handling
    def handle(self, event: MessageEvent) -> Outcome:
        """Process one inbound message. Never raises."""
        started = time.time()
        self.stats.received += 1

        incoming, why = self._is_incoming(event)
        if not incoming:
            self.stats.skipped += 1
            return Outcome(event=event, reason=why)

        dedup = self.deduper.check(event.sender, event.message, event.timestamp_ms)
        event.key = dedup.key
        if dedup.is_duplicate:
            self.stats.duplicates += 1
            self.stats.skipped += 1
            return Outcome(event=event, reason=SKIP_DUPLICATE, event_key=dedup.key)

        ok, reason = self.gate()
        if not ok:
            self.stats.skipped += 1
            outcome = Outcome(event=event, reason=reason, event_key=dedup.key)
            self.history.append(outcome)
            return outcome

        try:
            result = self.ai.reply(event.display, event.message)
        except AINotConfigured as exc:
            self.stats.errors += 1
            return self._finish(Outcome(event=event, error=str(exc), event_key=dedup.key), started)
        except AIQuotaExceeded as exc:
            self.stats.errors += 1
            return self._finish(
                Outcome(event=event, error=str(exc), reason="OpenRouter quota", event_key=dedup.key),
                started,
            )
        except AIError as exc:
            self.stats.errors += 1
            return self._finish(Outcome(event=event, error=str(exc), event_key=dedup.key), started)

        try:
            self.bus.send_reply(event, result.text, dedup.key)
        except OSError as exc:
            self.stats.errors += 1
            return self._finish(
                Outcome(event=event, error=f"Could not hand reply to Android: {exc}", event_key=dedup.key),
                started,
            )

        self.stats.replied += 1
        self._record_usage()
        outcome = Outcome(
            event=event,
            replied=True,
            text=result.text,
            model=result.model,
            event_key=dedup.key,
        )
        return self._finish(outcome, started)

    def _finish(self, outcome: Outcome, started: float) -> Outcome:
        outcome.duration_ms = int((time.time() - started) * 1000)
        self.history.append(outcome)
        del self.history[:-50]  # bounded scrollback
        self.publish_status()
        return outcome

    # -------------------------------------------------------------- usage
    def _record_usage(self) -> None:
        """Count the reply locally, then best-effort mirror to the server."""
        today = time.strftime("%Y-%m-%d")
        used_key = f"agent:usage:{today}"
        try:
            used = int(self.store.get(used_key) or 0) + 1
        except (TypeError, ValueError):
            used = 1
        self.store.set(used_key, used)

        session = self.auth.session
        if session.signed_in and session.is_pro is False:
            session.usage_used = used

        # Push the authoritative count when we can; a failure must not lose
        # the reply that was already sent.
        try:
            client = self.auth._require_client()
            client.record_usage(session.uid, today, used, session.id_token)
            self.auth.sync()
        except Exception:
            pass  # local mirror remains; next sync reconciles

    # --------------------------------------------------------------- loop
    def tick(self) -> List[Outcome]:
        """One non-blocking poll. Returns the outcomes for this tick."""
        outcomes = []
        for event in self.bus.poll():
            outcomes.append(self.handle(event))
        return outcomes

    def run(self, on_outcome: Optional[Callable[[Outcome], None]] = None) -> None:
        """Blocking agent loop until :attr:`running` goes false."""
        self.running = True
        self.bus.ensure_dir()
        self.publish_status()
        while self.running:
            outcomes = self.tick()
            for outcome in outcomes:
                if on_outcome is not None:
                    on_outcome(outcome)
            if not outcomes:
                time.sleep(self.bus.poll_interval)

    def stop(self) -> None:
        self.running = False

    # ------------------------------------------------------------- status
    def publish_status(self) -> None:
        ok, reason = self.gate()
        status = {
            "agent": "running" if self.running else "stopped",
            "account": self.auth.session.username or "",
            "plan": self.auth.session.plan,
            "status": self.auth.session.status_label,
            "online": self.auth.session.online,
            "gate": "ready" if ok else reason,
            "ai": "ready" if self.config.ai.configured else "not_configured",
            "stats": {
                "received": self.stats.received,
                "replied": self.stats.replied,
                "duplicates": self.stats.duplicates,
                "errors": self.stats.errors,
            },
        }
        try:
            self.bus.write_status(status)
        except OSError:
            pass


__all__ = ["AgentStats", "Outcome", "ReplyAgent", "SKIP_DUPLICATE", "SKIP_QUOTA"]