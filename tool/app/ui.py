"""Terminal presentation layer for ShiPu WP.

Holds everything about *how* ShiPu WP looks:

* terminal / console initialisation (Termux and desktop terminals);
* the colour palette and shared style tokens;
* reusable components - panels, rules, status chips, meters, menu rows;
* responsive width handling and safe screen clearing.

No business logic and no storage access lives here.
"""

from __future__ import annotations

import os
import shutil
import sys
from typing import Iterable, List, Optional, Sequence, Tuple

from rich import box
from rich.align import Align
from rich.console import Console, Group, RenderableType
from rich.panel import Panel
from rich.text import Text

# --------------------------------------------------------------------------
# Palette - a restrained dark-terminal scheme
# --------------------------------------------------------------------------
COLORS = {
    "primary": "bright_cyan",
    "primary_dim": "cyan",
    "secondary": "bright_magenta",
    "secondary_dim": "magenta",
    "success": "bright_green",
    "warning": "bright_yellow",
    "error": "bright_red",
    "muted": "grey58",
    "faint": "grey42",
    "text": "white",
    "border": "grey30",
    "border_accent": "bright_cyan",
}

# Width tuning so the UI stays readable on a phone and on a wide monitor.
MIN_WIDTH = 34
MAX_WIDTH = 94
INDENT = 1

_console: Optional[Console] = None


# --------------------------------------------------------------------------
# Console / terminal setup
# --------------------------------------------------------------------------
def _no_color_requested() -> bool:
    return bool(os.environ.get("NO_COLOR")) or os.environ.get("TERM", "") == "dumb"


def get_console() -> Console:
    """Return the shared Rich console (created once, reused everywhere)."""
    global _console
    if _console is None:
        _console = Console(
            highlight=False,
            soft_wrap=False,
            emoji=False,
            no_color=_no_color_requested(),
            legacy_windows=False,
        )
    return _console


def terminal_size() -> Tuple[int, int]:
    """Current ``(columns, lines)`` with a safe fallback."""
    try:
        size = shutil.get_terminal_size(fallback=(80, 24))
        return max(20, size.columns), max(10, size.lines)
    except Exception:  # pragma: no cover - defensive
        return 80, 24


def content_width() -> int:
    """Usable content width for the current terminal."""
    columns, _ = terminal_size()
    return max(MIN_WIDTH, min(MAX_WIDTH, columns - (INDENT * 2) - 2))


def clear_screen() -> None:
    """Clear the screen and home the cursor without flashing artefacts."""
    console = get_console()
    try:
        if console.is_terminal:
            console.file.write("\x1b[2J\x1b[H")
            console.file.flush()
        else:
            console.clear()
    except Exception:
        try:
            sys.stdout.write("\n")
        except Exception:
            pass


def supports_ansi() -> bool:
    """True when we can safely emit escape sequences."""
    try:
        return get_console().is_terminal
    except Exception:
        return False


# --------------------------------------------------------------------------
# Text helpers
# --------------------------------------------------------------------------
def styled(text: str, style: str = "", **kwargs) -> Text:
    """Shortcut for ``rich.text.Text`` with a single style string."""
    return Text(text, style=style, **kwargs)


def muted(text: str) -> Text:
    return Text(text, style=COLORS["muted"])


def banner_line(text: str, style: str = COLORS["muted"]) -> Text:
    """Centred single-line heading."""
    return Align.center(Text(text, style=style))


def truncate(text: str, width: int) -> str:
    """Hard-truncate to ``width`` columns (emoji/box safe)."""
    if width <= 0:
        return ""
    return text if len(text) <= width else text[: max(0, width - 1)] + "…"


# --------------------------------------------------------------------------
# Rules, chips, meters
# --------------------------------------------------------------------------
def rule(label: str = "", style: Optional[str] = None) -> Text:
    """Horizontal divider, optionally with an inline label."""
    width = content_width()
    border = style or COLORS["border"]
    if not label:
        return Text("─" * width, style=border)
    head = f"─── {label} "
    tail = "─" * max(0, width - len(head))
    line = Text()
    line.append(head, style=COLORS["primary_dim"])
    line.append(tail, style=border)
    return line


def dot(status: str) -> str:
    """Status bullet: ● ready/active, ◐ pending, ○ offline."""
    return {"on": "●", "half": "◐", "off": "○"}.get(status, "●")


def chip(text: str, kind: str = "muted") -> Text:
    """Coloured label, e.g. ``● READY`` or ``NOT CONNECTED``."""
    return Text(text, style=COLORS.get(kind, COLORS["muted"]))


def kv(label: str, value: Text, label_width: int = 12) -> Text:
    """Aligned ``LABEL   value`` row used across dashboards."""
    line = Text()
    line.append(label.ljust(label_width), style=COLORS["muted"])
    line.append_text(value)
    return line


def meter(fraction: float, width: int = 28) -> Text:
    """Block-character usage meter, e.g. ``████████░░░░░░``."""
    width = max(6, width)
    fraction = 0.0 if fraction < 0 else (1.0 if fraction > 1 else fraction)
    filled = int(round(fraction * width))
    if fraction > 0 and filled == 0:
        filled = 1  # always show at least one sliver of progress
    bar = Text()
    if fraction >= 1.0:
        bar.append("█" * width, style=COLORS["warning"])
    elif fraction >= 0.8:
        bar.append("█" * filled, style=COLORS["warning"])
        bar.append("░" * (width - filled), style=COLORS["faint"])
    else:
        bar.append("█" * filled, style=COLORS["primary"])
        bar.append("░" * (width - filled), style=COLORS["faint"])
    return bar


def bullet_list(items: Sequence[str], style: str = COLORS["success"], mark: str = "✓") -> Text:
    """Compact feature list rendered as one Text block."""
    line = Text()
    for index, item in enumerate(items):
        if index:
            line.append("\n")
        line.append(f"{mark} ", style=style)
        line.append(item, style=COLORS["text"])
    return line


def columns(items: Sequence[str], per_row: int = 2, style: str = COLORS["muted"]) -> Text:
    """Lay small labels out in a fixed number of columns."""
    block = Text()
    cell = max(10, content_width() // max(1, per_row))
    for index, item in enumerate(items):
        block.append(item.ljust(cell), style=style)
        if (index + 1) % per_row == 0:
            block.append("\n")
    if len(items) % per_row:
        block.append("\n")
    return block


# --------------------------------------------------------------------------
# Panels and cards
# --------------------------------------------------------------------------
def panel(
    body: RenderableType,
    title: str = "",
    subtitle: str = "",
    accent: str = "primary",
    padding: Tuple[int, int] = (1, 2),
) -> Panel:
    """Bordered panel using the accent colour for the frame."""
    title_style = COLORS.get(accent, COLORS["primary"])
    subtitle_style = COLORS["faint"]
    border_style = COLORS.get("border_accent", COLORS["primary"])
    return Panel(
        body,
        title=Text(title, style=title_style) if title else None,
        subtitle=Text(subtitle, style=subtitle_style) if subtitle else None,
        title_align="left",
        subtitle_align="right",
        box=box.ROUNDED,
        border_style=border_style,
        padding=padding,
        expand=True,
    )


def card(
    heading: str,
    body: RenderableType,
    hint: str = "",
    accent: str = "primary",
    width: Optional[int] = None,
) -> Panel:
    """Compact menu/plan card with a heading, body text and a hint line."""
    width = width or content_width()
    inner = Text()
    inner.append(heading, style=COLORS.get(accent, COLORS["primary"]))
    inner.append("\n")
    inner.append_text(body if isinstance(body, Text) else Text(str(body)))
    if hint:
        inner.append("\n")
        inner.append(hint, style=COLORS["faint"])
    return panel(inner, accent=accent, padding=(1, 2))


def menu_row(key: str, label: str, hint: str = "", accent: str = "primary") -> Text:
    """``[1]  Label .................. hint`` navigation row."""
    style = COLORS.get(accent, COLORS["primary"])
    row = Text()
    row.append("[", style=COLORS["faint"])
    row.append(key.ljust(2), style=style)
    row.append("] ", style=COLORS["faint"])
    row.append(label, style=COLORS["text"])
    if hint:
        pad = max(1, content_width() - len(label) - 8 - len(hint))
        row.append(" " * pad, style=COLORS["border"])
        row.append(hint, style=COLORS["faint"])
    return row


def notice(message: str, kind: str = "info") -> Text:
    """Inline status/error line (used after invalid input)."""
    marks = {
        "info": ("·", COLORS["muted"]),
        "success": ("✓", COLORS["success"]),
        "warning": ("!", COLORS["warning"]),
        "error": ("×", COLORS["error"]),
    }
    mark, style = marks.get(kind, marks["info"])
    line = Text()
    line.append(f"{mark} ", style=style)
    line.append(message, style=style)
    return line


def gradient(text_lines: Iterable[str], stops: Sequence[str]) -> Text:
    """Multi-stop horizontal colour gradient across ``text_lines``."""
    lines = list(text_lines)
    if not lines:
        return Text()
    palette = [COLORS.get(stop, stop) for stop in stops] or [COLORS["primary"]]
    width = max(len(line) for line in lines) or 1
    total = width * max(1, len(lines) - 1) if len(lines) > 1 else width
    out = Text()
    for row, line in enumerate(lines):
        for col, char in enumerate(line):
            position = row * width + col
            index = int(position * len(palette) / max(1, total))
            out.append(char, style=palette[min(index, len(palette) - 1)])
        if row != len(lines) - 1:
            out.append("\n")
    return out


def render(*parts: RenderableType, width: Optional[int] = None) -> None:
    """Print renderables with the shared console and shared indentation."""
    console = get_console()
    console.print()
    for part in parts:
        console.print(part, width=width or content_width())

# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------
def run(argv: Optional[Sequence[str]] = None) -> int:
    """Boot ShiPu WP and drive the interface until the user exits.

    ``start.py`` calls nothing else. Imports are deferred so that merely
    importing :mod:`app.ui` (for its colour helpers) stays cheap and free of
    side effects.
    """
    from . import animations, screens
    from .navigation import Router
    from .state import AppState
    from .storage import get_store

    console = get_console()
    store = get_store()
    state = AppState(store=store)
    context = state.bootstrap()

    if not store.persistent:
        # Degraded mode is worth telling the user about exactly once.
        console.print(
            notice(
                f"Local database unavailable - running in memory mode ({store.db_path}).",
                "warning",
            )
        )

    if state.get_setting("animations") is False:
        animations.BOOT_STEPS = tuple(
            (label, 0.0) for label, _ in animations.BOOT_STEPS
        )

    router = Router(state=state, screens=dict(screens.SCREENS), start="splash")
    try:
        router.run()
    except KeyboardInterrupt:
        console.print()
        console.print(notice("Interrupted - session closed.", "info"))
    except Exception as exc:  # never leave a raw traceback in the terminal
        console.print()
        console.print(notice(f"ShiPu WP stopped: {exc}", "error"))
    finally:
        try:
            store.close()
        except Exception:
            pass

    del context  # context is only used for future telemetry hooks
    return 0
