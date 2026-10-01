"""Message fingerprinting and duplicate suppression.

WhatsApp notifications are re-posted constantly: every delivery tick, every
re-layout, every "delivered" state change. Without suppression the agent would
burn AI quota (and money) answering the same message several times.

The fingerprint deliberately mixes three fields so that both of these are
treated as duplicates:

* the same text arriving twice from the same conversation, and
* a *legitimate* follow-up where the sender repeats themselves after a while.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

# How long a fingerprint stays "seen". Notifications for one message can
# repeat for minutes; a genuine re-send later should be treated as new.
DEFAULT_TTL_SECONDS = 180
DEFAULT_MAX_ENTRIES = 512


def fingerprint(sender: str, message: str, timestamp_ms: int = 0) -> str:
    """Stable digest for a message.

    The timestamp is rounded to :data:`BUCKET_MS` so near-simultaneous
    re-notifications collapse together while a message sent minutes later
    still produces a new fingerprint.
    """
    bucket = int(timestamp_ms // BUCKET_MS) if timestamp_ms else 0
    raw = f"{sender.strip().lower()}|{' '.join(message.lower().split())}|{bucket}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


# Notifications within this window collapse to one bucket.
BUCKET_MS = 30_000


@dataclass
class DedupResult:
    """Outcome of :meth:`Deduper.check`."""

    is_duplicate: bool
    key: str
    age_seconds: float = 0.0
    seen_count: int = 1


class Deduper:
    """Bounded TTL cache of recent message fingerprints."""

    def __init__(
        self,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        clock=time.time,
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._clock = clock
        self._seen: Dict[str, float] = {}
        self._counts: Dict[str, int] = {}
        self.duplicates_suppressed = 0

    def check(self, sender: str, message: str, timestamp_ms: int = 0) -> DedupResult:
        """Record a message, reporting whether it is a repeat."""
        key = fingerprint(sender, message, timestamp_ms)
        now = self._clock()
        previous = self._seen.get(key)

        if previous is not None and (now - previous) < self.ttl_seconds:
            self.duplicates_suppressed += 1
            self._counts[key] = self._counts.get(key, 1) + 1
            return DedupResult(True, key, now - previous, self._counts[key])

        self._seen[key] = now
        self._counts[key] = 1
        self._evict()
        return DedupResult(False, key, 0.0, 1)

    def seen(self, key: str) -> bool:
        """True when ``key`` is inside the TTL window."""
        seen_at = self._seen.get(key)
        return seen_at is not None and (self._clock() - seen_at) < self.ttl_seconds

    def stats(self) -> Dict[str, int]:
        return {
            "tracked": len(self._seen),
            "duplicatesSuppressed": self.duplicates_suppressed,
        }

    def clear(self) -> None:
        self._seen.clear()
        self._counts.clear()

    def _evict(self) -> None:
        """Drop expired entries, then the oldest, so memory stays bounded."""
        if len(self._seen) <= self.max_entries:
            cutoff = self._clock() - self.ttl_seconds
            for key in [k for k, t in self._seen.items() if t < cutoff]:
                self._seen.pop(key, None)
                self._counts.pop(key, None)
            return
        # Oldest first: dict preserves insertion order.
        for key in list(self._seen)[: len(self._seen) - self.max_entries]:
            self._seen.pop(key, None)
            self._counts.pop(key, None)


__all__ = ["BUCKET_MS", "DedupResult", "Deduper", "DEFAULT_TTL_SECONDS", "fingerprint"]