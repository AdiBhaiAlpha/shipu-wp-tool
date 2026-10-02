"""Input handling, keyboard navigation and the screen router.

Responsibilities
----------------
* a small raw-mode key reader (arrows, Enter, Backspace, Esc, Ctrl+C) with a
  transparent fallback to plain line input when stdin is not a TTY;
* a reusable single-select menu with a ``›`` cursor and digit shortcuts;
* :class:`ScreenResult` and :class:`Router`, the tiny navigation state machine
  that moves between the screens defined in :mod:`app.screens`.

Everything here is defensive: no input, junk input, closed stdin, Ctrl+C and
terminal resizes all resolve to a valid screen or a clean exit.
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence

from rich.text import Text

from . import ui
from .state import AppState

# POSIX-only terminal control; absent on Windows / exotic builds.
try:  # pragma: no cover - platform dependent
    import select
    import termios
    import tty

    POSIX_TERMIOS = True
except Exception:  # pragma: no cover - platform dependent
    POSIX_TERMIOS = False


# --------------------------------------------------------------------------
# Keys
# --------------------------------------------------------------------------
KEY_UP = "UP"
KEY_DOWN = "DOWN"
KEY_LEFT = "LEFT"
KEY_RIGHT = "RIGHT"
KEY_ENTER = "ENTER"
KEY_BACKSPACE = "BACKSPACE"
KEY_ESC = "ESC"
KEY_CTRL_C = "CTRL_C"
KEY_EOF = "EOF"

_ESCAPES = {
    "[A": KEY_UP,
    "[B": KEY_DOWN,
    "[C": KEY_RIGHT,
    "[D": KEY_LEFT,
    "OA": KEY_UP,
    "OB": KEY_DOWN,
    "OC": KEY_RIGHT,
    "OD": KEY_LEFT,
}

# Non-interactive line input gives up after this many invalid answers so a
# piped/garbled stream can never spin forever.
MAX_LINE_ATTEMPTS = 10


def stdin_is_tty() -> bool:
    try:
        return bool(sys.stdin.isatty())
    except Exception:
        return False


@contextmanager
def raw_mode():
    """Put the terminal in raw mode for the duration of the block."""
    if not (POSIX_TERMIOS and stdin_is_tty()):
        yield False
        return
    fd = sys.stdin.fileno()
    saved = None
    try:
        saved = termios.tcgetattr(fd)
        tty.setraw(fd)
    except Exception:
        saved = None
    try:
        yield True
    finally:
        if saved is not None:
            try:
                termios.tcsetattr(fd, termios.TCSADRAIN, saved)
                termios.tcsetattr(fd, termios.TCSANOW, saved)
            except Exception:
                pass


def _peek(timeout: float = 0.06) -> bool:
    """True when more input is already buffered (escape sequence tail)."""
    if not POSIX_TERMIOS:
        return False
    try:
        ready, _, _ = select.select([sys.stdin], [], [], timeout)
        return bool(ready)
    except Exception:
        return False


def _read_char() -> str:
    try:
        return sys.stdin.read(1)
    except (KeyboardInterrupt, EOFError):
        raise


def read_key() -> str:
    """Read one logical key press, normalising escape sequences."""
    try:
        char = _read_char()
    except EOFError:
        return KEY_EOF
    if char in ("\x03", "\x04"):
        return KEY_CTRL_C
    if char in ("\r", "\n"):
        return KEY_ENTER
    if char in ("\x7f", "\b"):
        return KEY_BACKSPACE
    if char == "\x1b":
        if not _peek():
            return KEY_ESC
        seq = _read_char()
        if seq in ("[", "O") and _peek():
            seq += _read_char()
        return _ESCAPES.get(seq, KEY_ESC)
    if char.isprintable():
        return char
    return ""


# --------------------------------------------------------------------------
# Menus
# --------------------------------------------------------------------------
@dataclass
class MenuOption:
    """One selectable row: hotkey, label, optional right-hand hint."""

    key: str
    label: str
    hint: str = ""
    accent: str = "primary"
    enabled: bool = True

    @property
    def digits(self) -> str:
        """Lower-cased hotkey used for matching input."""
        return self.key.lower()


def _menu_block(options: Sequence[MenuOption], cursor: int) -> Text:
    """Render the menu rows with the cursor on ``options[cursor]``."""
    width = ui.content_width()
    block = Text()
    for index, option in enumerate(options):
        if index:
            block.append("\n")
        active = index == cursor
        marker = "›" if active else " "
        style_key = option.accent if option.enabled else "faint"
        label_style = ui.COLORS["text"] if option.enabled else ui.COLORS["faint"]
        if active:
            label_style = f"bold {ui.COLORS.get(style_key, ui.COLORS['primary'])}"

        block.append(f" {marker} ", style=ui.COLORS["primary"] if active else ui.COLORS["border"])
        block.append(f"[{option.key.upper()}] ", style=ui.COLORS[style_key])
        block.append(option.label, style=label_style)

        if option.hint and width > 46:
            used = 5 + len(option.key) + 3 + len(option.label)
            gap = max(1, width - used - len(option.hint) - 2)
            block.append(" " * gap, style=ui.COLORS["border"])
            block.append(option.hint, style=ui.COLORS["faint"])
    return block


def _print_block(block: Text) -> None:
    """Print a menu block at the shared content width."""
    console = ui.get_console()
    console.print(block, width=ui.content_width())


def _rewrite(block: Text, previous_lines: int) -> int:
    """Move the cursor back over the previous block and redraw in place."""
    console = ui.get_console()
    try:
        if previous_lines > 0 and console.is_terminal:
            console.file.write(f"\x1b[{previous_lines}A\x1b[J")
            console.file.flush()
        _print_block(block)
        return previous_lines
    except Exception:
        _print_block(block)
        return 0


def _menu_height(options: Sequence[MenuOption]) -> int:
    """Number of terminal rows the menu occupies at the current width."""
    width = ui.content_width()
    rows = len(options)
    for option in options:
        used = 5 + len(option.key) + 3 + len(option.label)
        if option.hint and width <= 46:
            continue
        if used + len(option.hint) + 2 > width:
            rows += 1
    return max(1, rows)


def select(
    options: Sequence[MenuOption],
    prompt: str = "Select",
    default_key: Optional[str] = None,
    allow_cancel: bool = True,
) -> str:
    """Render an interactive menu and return the chosen option key.

    Navigation: ``↑/↓`` move, ``Enter`` confirms, a digit or letter hotkey
    selects instantly, ``q``/``Esc`` cancel (when ``allow_cancel``).
    """
    if not options:
        return "q"
    console = ui.get_console()

    def find(key: str) -> int:
        for index, option in enumerate(options):
            if option.digits == key:
                return index
        return -1

    cursor = 0
    if default_key:
        found = find(default_key.lower())
        if found >= 0:
            cursor = found

    console.print()
    console.print(
        Text.assemble(
            (prompt, f"bold {ui.COLORS['muted']}"),
            ("  ", ""),
            ("↑↓", ui.COLORS["faint"]),
            (" move  ", ui.COLORS["faint"]),
            ("enter", ui.COLORS["faint"]),
            (" select", ui.COLORS["faint"]),
        ),
        width=ui.content_width(),
    )
    console.print()

    interactive = POSIX_TERMIOS and stdin_is_tty() and console.is_terminal

    if not interactive:
        # Piped / non-interactive: plain line input, no cursor tricks, but the
        # same validation as the interactive path so bad input can never be
        # mistaken for a command.
        _print_block(_menu_block(options, cursor))
        for _ in range(MAX_LINE_ATTEMPTS):
            try:
                raw = sys.stdin.readline()
            except (KeyboardInterrupt, EOFError):
                return "q"
            if raw == "":  # EOF
                return "q"
            answer = raw.strip().lower()
            if not answer:
                return options[cursor].digits
            match = find(answer)
            if match >= 0:
                return options[match].digits
            ui.get_console().print(
                ui.notice(f"'{answer}' is not an option - use one of: "
                          + ", ".join(o.digits.upper() for o in options), "warning"),
                width=ui.content_width(),
            )
            _print_block(_menu_block(options, cursor))
        return options[cursor].digits

    with raw_mode():
        previous = _menu_height(options) + 1  # +1 for the prompt row
        _print_block(_menu_block(options, cursor))
        console.file.write("\x1b[?25l")  # hide cursor during selection
        console.file.flush()
        try:
            while True:
                key = read_key()
                if key in (KEY_CTRL_C, KEY_EOF):
                    return "q"
                if key == KEY_UP:
                    cursor = (cursor - 1) % len(options)
                elif key == KEY_DOWN:
                    cursor = (cursor + 1) % len(options)
                elif key == KEY_ENTER:
                    return options[cursor].digits
                elif key == KEY_ESC:
                    return "q" if allow_cancel else options[cursor].digits
                elif key == KEY_BACKSPACE:
                    return "q" if allow_cancel else options[cursor].digits
                elif key:
                    match = find(key.lower())
                    if match >= 0 and options[match].enabled:
                        return options[match].digits
                    elif key.lower() in ("q", "x") and allow_cancel:
                        return "q"
                    else:
                        continue
                previous = _rewrite(_menu_block(options, cursor), previous)
        finally:
            try:
                console.file.write("\x1b[?25h")  # always restore the cursor
                console.file.flush()
            except Exception:
                pass


def ask(question: str, default: str = "", secret: bool = False) -> str:
    """Single-line styled prompt. Returns ``default`` on empty input/Ctrl+C.

    With ``secret=True`` the terminal is switched to no-echo so passwords are
    never written to the scrollback or captured by a screencast.
    """
    console = ui.get_console()
    console.print()
    console.print(
        Text.assemble(
            ("> ", ui.COLORS["primary"]),
            (question, ui.COLORS["muted"]),
            (" ", ""),
        ),
        end="",
        width=ui.content_width(),
    )

    if not secret:
        try:
            answer = sys.stdin.readline()
        except (KeyboardInterrupt, EOFError):
            console.print()
            return default
        if answer == "":  # stdin closed
            console.print()
            return default
        return answer.strip() or default

    try:
        with termios_noecho():
            answer = sys.stdin.readline()
    except (KeyboardInterrupt, EOFError, Exception):
        console.print()
        return default
    console.print()
    if answer == "":
        return default
    return answer.strip() or default


@contextmanager
def termios_noecho():
    """Disable terminal echo for the duration of the block."""
    try:
        import termios
        import tty
    except ImportError:
        yield
        return

    if not sys.stdin.isatty():
        yield
        return

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        new = termios.tcgetattr(fd)
        new[3] &= ~termios.ECHO  # lflags
        termios.tcsetattr(fd, termios.TCSADRAIN, new)
        yield
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def confirm(question: str, yes_key: str = "y") -> bool:
    """Yes/no prompt; empty input, Ctrl+C and EOF all mean *no*."""
    answer = ask(f"{question} [{yes_key}/n] ").lower()
    return answer in (yes_key, "yes")


def pause(message: str = "Press Enter to continue") -> None:
    """Wait for Enter so a transient screen stays readable."""
    ask(message)


# --------------------------------------------------------------------------
# Screen routing
# --------------------------------------------------------------------------
@dataclass
class ScreenResult:
    """What a screen tells the router to do next."""

    next: Optional[str] = None
    quit: bool = False
    message: Optional[str] = None
    tone: str = "info"
    refresh: bool = False

    @classmethod
    def go(cls, screen: str, message: Optional[str] = None, tone: str = "info") -> "ScreenResult":
        return cls(next=screen, message=message, tone=tone)

    @classmethod
    def exit(cls, message: Optional[str] = None, tone: str = "info") -> "ScreenResult":
        return cls(quit=True, message=message, tone=tone)


# A screen takes the app state plus any message from the previous screen.
Screen = Callable[..., "ScreenResult"]


@dataclass
class Router:
    """Minimal navigation state machine over a ``{name: callable}`` map.

    A *screen* is any callable ``(state, toast) -> ScreenResult``. ``toast`` is
    the message left behind by the previous screen (or ``None``) so it can be
    rendered as a banner on the screen the user lands on.
    """

    state: AppState
    screens: Dict[str, Screen]
    start: str
    home: str = "welcome"
    history_limit: int = 32
    _history: List[str] = field(default_factory=list, repr=False)

    def run(self) -> None:
        """Drive the application until a screen asks to quit."""
        current = self.start
        toast: Optional[ScreenResult] = None

        while True:
            screen = self.screens.get(current)
            if screen is None:
                self._console_line(
                    ui.notice(f"Unknown screen '{current}' - returning home", "error")
                )
                current, toast = self.home, None
                continue

            ui.clear_screen()
            try:
                result = screen(self.state, toast)
            except KeyboardInterrupt:
                result = ScreenResult.exit("Interrupted.")
            except Exception as exc:  # last-resort guard: never crash the UI
                result = ScreenResult(
                    next=self.home,
                    message=f"Unexpected error: {exc.__class__.__name__}: {exc}",
                    tone="error",
                )

            if result.quit:
                self._teardown()
                return

            toast = result if result.message else None
            if result.next and result.next != current:
                self._history.append(current)
                self._history = self._history[-self.history_limit :]
                current = result.next

    # ------------------------------------------------------------- helpers
    @staticmethod
    def _console_line(line: Text) -> None:
        ui.get_console().print(line, width=ui.content_width())

    def _teardown(self) -> None:
        try:
            self.state.store.close()
        except Exception:
            pass

    def previous(self, fallback: str = "welcome") -> str:
        """Previous screen, falling back to ``fallback`` when empty."""
        return self._history[-1] if self._history else fallback