"""NDJSON event bridge between the Android listener and the Python agent.

Transport design
----------------
The Android ``NotificationListenerService`` and the Termux Python process share
one directory (default ``~/.shipuwp/bridge``). Two append-only NDJSON files:

``events.ndjson``   Android -> Python : one inbound WhatsApp message
``commands.ndjson`` Python -> Android : a reply to type into the conversation

Why files instead of a socket or HTTP: the bridge must work on a plain Termux
phone with no root, no extra daemon and no network. ``/data/local/tmp`` is not
always readable and loopback ports are awkward across process boundaries, so a
shared directory of append-only logs is the only transport that is reliable in
both the Termux app and a ``adb shell`` deployment.

Concurrency: the writer only ever appends whole lines with a single ``write``
of a line that ends in ``\\n``. Readers track a byte offset. A torn read can
therefore only ever produce a partial *last* line, which is held back until the
newline arrives.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, Iterator, List, Optional

DEFAULT_BRIDGE_DIR = os.path.expanduser("~/.shipuwp/bridge")
EVENTS_FILE = "events.ndjson"
COMMANDS_FILE = "commands.ndjson"
STATUS_FILE = "status.json"


@dataclass
class MessageEvent:
    """One inbound WhatsApp message captured from a notification."""

    sender: str
    message: str
    timestamp_ms: int = 0
    conversation: str = ""
    package: str = "com.whatsapp"
    source: str = "notification"
    key: str = ""

    @classmethod
    def from_dict(cls, raw: Dict) -> "MessageEvent":
        return cls(
            sender=str(raw.get("sender", "")).strip(),
            message=str(raw.get("message", "")).strip(),
            timestamp_ms=int(raw.get("timestamp_ms") or raw.get("timestampMs") or 0),
            conversation=str(raw.get("conversation", "") or ""),
            package=str(raw.get("package", "com.whatsapp")),
            source=str(raw.get("source", "notification")),
            key=str(raw.get("key", "")),
        )

    @property
    def display(self) -> str:
        return self.sender or self.conversation or "unknown"

    @property
    def is_group(self) -> bool:
        # WhatsApp group notifications carry the sender name in the title and
        # prefix the body, e.g. "Rahim: kobi ashbe?".
        return ":" in self.message and not self.sender.endswith(":")


@dataclass
class Command:
    """A reply handed back to the Android side for typing."""

    action: str
    event_key: str
    text: str
    sender: str = ""
    created_at: float = field(default_factory=time.time)

    @classmethod
    def from_dict(cls, raw: Dict) -> "Command":
        return cls(
            action=str(raw.get("action", "")),
            event_key=str(raw.get("event_key", "")),
            text=str(raw.get("text", "")),
            sender=str(raw.get("sender", "")),
            created_at=float(raw.get("created_at") or time.time()),
        )

    def to_dict(self) -> Dict:
        return {
            "action": self.action,
            "event_key": self.event_key,
            "text": self.text,
            "sender": self.sender,
            "created_at": self.created_at,
        }


class EventBus:
    """Reads inbound events and queues outbound commands."""

    def __init__(
        self,
        bridge_dir: str = DEFAULT_BRIDGE_DIR,
        poll_interval: float = 0.4,
    ) -> None:
        self.bridge_dir = bridge_dir
        self.poll_interval = poll_interval
        self.events_path = os.path.join(bridge_dir, EVENTS_FILE)
        self.commands_path = os.path.join(bridge_dir, COMMANDS_FILE)
        self.status_path = os.path.join(bridge_dir, STATUS_FILE)
        self._offset = 0
        self._partial = ""

    # ------------------------------------------------------------- lifecycle
    def ensure_dir(self) -> None:
        os.makedirs(self.bridge_dir, exist_ok=True)
        for path in (self.events_path, self.commands_path):
            if not os.path.exists(path):
                # Touch so readers never see a missing file.
                with open(path, "a", encoding="utf-8"):
                    pass

    def rotate(self) -> None:
        """Start reading a freshly rotated events file."""
        self._offset = 0
        self._partial = ""

    # ----------------------------------------------------------------- read
    def _drain(self, path: str, offset: int) -> tuple[List[str], int, str]:
        """Read complete lines from ``path`` starting at ``offset``."""
        try:
            size = os.path.getsize(path)
        except OSError:
            return [], offset, ""
        if size < offset:
            # File was truncated or rotated.
            offset = 0
        if size == offset:
            return [], offset, ""
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            handle.seek(offset)
            chunk = handle.read(size - offset)
        *lines, tail = chunk.split("\n")
        return lines, offset + len(chunk) - len(tail), tail

    def poll(self) -> List[MessageEvent]:
        """Return every complete inbound event since the last call."""
        events: List[MessageEvent] = []
        lines, self._offset, self._partial = self._drain(
            self.events_path, self._offset
        )
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            event = MessageEvent.from_dict(raw)
            if event.sender or event.message:
                events.append(event)
        return events

    def iter_events(self, stop: Callable[[], bool] = lambda: False) -> Iterator[MessageEvent]:
        """Blocking-ish iterator for a headless agent run."""
        while not stop():
            batch = self.poll()
            if batch:
                yield from batch
            else:
                time.sleep(self.poll_interval)

    # ---------------------------------------------------------------- write
    def send_reply(self, event: MessageEvent, text: str, event_key: str = "") -> Command:
        """Queue a reply for the Android side to type and send."""
        command = Command(
            action="reply",
            event_key=event_key or event.key,
            text=text,
            sender=event.sender,
        )
        self._append(self.commands_path, command.to_dict())
        return command

    def _append(self, path: str, payload: Dict) -> None:
        line = json.dumps(payload, ensure_ascii=False) + "\n"
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())

    def write_status(self, status: Dict) -> None:
        """Publish agent status so the Android app can show it."""
        status = dict(status)
        status.setdefault("updated_at", time.time())
        tmp = f"{self.status_path}.tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(status, handle, ensure_ascii=False)
        os.replace(tmp, self.status_path)

    def read_status(self) -> Dict:
        try:
            with open(self.status_path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, json.JSONDecodeError):
            return {}

    # ---------------------------------------------------------------- misc
    def clear(self) -> None:
        for path in (self.events_path, self.commands_path):
            with open(path, "w", encoding="utf-8"):
                pass
        self.rotate()


__all__ = [
    "COMMANDS_FILE",
    "Command",
    "DEFAULT_BRIDGE_DIR",
    "EVENTS_FILE",
    "EventBus",
    "MessageEvent",
    "STATUS_FILE",
]