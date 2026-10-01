"""Brand mark and startup motion for ShiPu WP.

* :func:`logo_lines` builds the wordmark by *stacking* individual glyph rows,
  which guarantees every letter lines up perfectly no matter how the logo is
  edited later.
* The mark is responsive: three tiers are chosen from the live terminal width
  so it still looks intentional on a phone in portrait.
* :func:`boot` plays the short professional loading sequence. It degrades to
  instant/static output on slow or piped terminals.
"""

from __future__ import annotations

import os
import time
from typing import Dict, List, Optional, Sequence

from rich.align import Align
from rich.console import Group
from rich.live import Live
from rich.text import Text

from . import ui
from .plans import APP_NAME, APP_TAGLINE

# --------------------------------------------------------------------------
# Logo glyphs - a clean 6-row block face, drawn for this product
# --------------------------------------------------------------------------
_GLYPHS: Dict[str, List[str]] = {
    "S": [
        "███████╗",
        "██╔════╝",
        "███████╗",
        "╚════██║",
        "███████║",
        "╚══════╝",
    ],
    "H": [
        "██╗   ██╗",
        "██║   ██║",
        "███████║",
        "██╔══██║",
        "██║  ██║",
        "╚═╝  ╚═╝",
    ],
    "I": [
        "███████╗",
        "██╔════╝",
        "██║     ",
        "██║     ",
        "██║     ",
        "╚═╝     ",
    ],
    "P": [
        "██████╗ ",
        "██╔══██╗",
        "██████╔╝",
        "██╔══██╗",
        "██║  ██║",
        "╚═╝  ╚═╝",
    ],
    "U": [
        "██╗   ██╗",
        "██║   ██║",
        "██║   ██║",
        "██║   ██║",
        "███████║",
        "╚══════╝",
    ],
    "W": [
        "██╗   ██╗",
        "██║   ██║",
        "██║   ██║",
        "██║ █ ██║",
        "████████║",
        "╚══════╝ ",
    ],
    " ": ["   ", "   ", "   ", "   ", "   ", "   "],
}

def build_wordmark(text: str) -> List[str]:
    """Stack per-character glyph rows into a block-letter wordmark."""
    rows: List[str] = []
    height = max((len(g) for g in _GLYPHS.values()), default=0)
    for row in range(height):
        line = ""
        for char in text.upper():
            glyph = _GLYPHS.get(char, _GLYPHS[" "])
            line_row = glyph[row] if row < len(glyph) else ""
            # pad so every glyph contributes the same number of columns
            width = max(len(r) for r in glyph)
            line += line_row.ljust(width)
        rows.append(line.rstrip())
    return rows


# Display tiers, widest first. The required column count is *measured* from the
# built wordmark, so editing a glyph can never desynchronise the tier thresholds.
_TIER_LABELS = ("SHIPU WP", "SHIPU", "")
_WORDMARKS = {label: build_wordmark(label) for label in _TIER_LABELS if label}
_TIERS: Sequence[tuple] = tuple(
    (label, (max(len(l) for l in _WORDMARKS[label]) + 2) if label else 0)
    for label in _TIER_LABELS
)


def logo_lines() -> List[str]:
    """Return the widest wordmark that fits the current terminal."""
    width = ui.content_width()
    for label, needed in _TIERS:
        if needed == 0 or width >= needed:
            if label:
                return list(_WORDMARKS[label])
            break
    return []


def logo_variant() -> str:
    """Which wordmark tier is active (``SHIPU WP`` / ``SHIPU`` / ``TEXT``)."""
    width = ui.content_width()
    for label, needed in _TIERS:
        if needed == 0 or width >= needed:
            return label or "TEXT"
    return "TEXT"


def logo_renderable() -> Group:
    """Gradient-coloured logo + tagline, ready to hand to ``console.print``."""
    parts: List[object] = []
    lines = logo_lines()

    if lines:
        parts.append(Align.center(ui.gradient(lines, ("primary", "primary_dim", "secondary"))))
        parts.append(Text())
        parts.append(Align.center(Text(APP_TAGLINE, style=ui.COLORS["muted"])))
    else:
        # Very narrow terminal: a compact, still-branded header.
        head = Text()
        head.append("█ ", style=ui.COLORS["primary"])
        head.append(APP_NAME.upper(), style=f"bold {ui.COLORS['primary']}")
        parts.append(Align.center(head))
        parts.append(Text())
        parts.append(Align.center(Text(APP_TAGLINE, style=ui.COLORS["muted"])))
    return Group(*parts)


# --------------------------------------------------------------------------
# Boot sequence
# --------------------------------------------------------------------------
SPINNER_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")

BOOT_STEPS: Sequence[tuple] = (
    ("Initializing ShiPu WP Agent", 0.24),
    ("Loading interface", 0.20),
    ("Checking local state", 0.18),
    ("Agent ready", 0.14),
)


def fast_mode() -> bool:
    """True when animation should be skipped (piped output / env override)."""
    if os.environ.get("SHIPU_FAST"):
        return True
    return not ui.get_console().is_terminal


def _boot_frame(
    steps: Sequence[tuple], current: int, frame_index: int
) -> Group:
    """One frame of the loading screen: spinner + progressive step list."""
    spinner = Text()
    spinner.append(
        f"{SPINNER_FRAMES[frame_index % len(SPINNER_FRAMES)]} ", style=ui.COLORS["primary"]
    )
    spinner.append(steps[current][0], style=ui.COLORS["text"])

    lines = Text()
    for index, (label, _) in enumerate(steps):
        if index:
            lines.append("\n")
        if index < current:
            lines.append(f"{ui.dot('on')} ", style=ui.COLORS["success"])
            lines.append(label, style=ui.COLORS["muted"])
        elif index == current:
            lines.append("  ", style=ui.COLORS["faint"])
            lines.append(label, style=ui.COLORS["faint"])
    return Group(Align.left(spinner), Text(), lines)


def boot(steps: Optional[Sequence[tuple]] = None, instant: Optional[bool] = None) -> None:
    """Play the loading animation (or print it statically when not a TTY)."""
    steps = list(steps or BOOT_STEPS)
    console = ui.get_console()
    skip = fast_mode() if instant is None else instant

    if skip:
        for label, _ in steps[:-1]:
            console.print(f"{ui.dot('on')} {label}", style=ui.COLORS["muted"])
        console.print(f"{ui.dot('on')} {steps[-1][0]}", style=ui.COLORS["success"])
        return

    frame = 0
    with Live(
        console=console,
        transient=True,
        refresh_per_second=14,
        vertical_overflow="visible",
    ) as live:
        for index, (label, delay) in enumerate(steps):
            live.update(_boot_frame(steps, index, frame))
            frame += 1
            time.sleep(delay)
        live.update(
            Align.left(
                Text.assemble(
                    (f"{ui.dot('on')} ", ui.COLORS["success"]),
                    (steps[-1][0], ui.COLORS["text"]),
                )
            )
        )
        time.sleep(0.08)


# --------------------------------------------------------------------------
# Small flourishes
# --------------------------------------------------------------------------
def typewriter(text: str, style: str = "", delay: float = 0.012) -> Text:
    """Render text progressively (used for short welcome lines)."""
    out = Text(style=style)
    for char in text:
        out.append(char)
    return out


def sweep(label: str, width: Optional[int] = None) -> Text:
    """A one-shot activity line, e.g. used before entering a screen."""
    width = width or ui.content_width()
    line = Text()
    line.append("▸ ", style=ui.COLORS["primary"])
    line.append(label, style=ui.COLORS["muted"])
    line.append(" " * max(0, width - len(label) - 2), style=ui.COLORS["border"])
    line.append("···", style=ui.COLORS["faint"])
    return line