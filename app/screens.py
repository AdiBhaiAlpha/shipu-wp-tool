"""Every screen ShiPu WP can display.

Each screen is a pure ``(state, toast) -> ScreenResult`` function: it renders
itself, asks the user one question, and returns where to go next. The router in
:mod:`app.navigation` owns the loop, so screens never control navigation state.

Screens present only; they never mutate storage directly - all writes go through
:class:`app.state.AppState`, which keeps the mock data flow easy to extend.
"""

from __future__ import annotations

import time
from typing import List, Optional, Sequence

from rich.align import Align
from rich.console import Group
from rich.panel import Panel
from rich.text import Text

from . import animations, ui
from . import navigation as nav
from . import plans as plans_mod
from .navigation import MenuOption, ScreenResult, confirm, pause, select
from .plans import APP_BUILD, APP_NAME, APP_TAGLINE, APP_VERSION, Plan
from .state import AppState


# ==========================================================================
# Shared chrome
# ==========================================================================
def header(state: AppState, section: str = "") -> Text:
    """Top bar: product name, build tag and live agent status."""
    line = Text()
    line.append(f" {APP_NAME}", style=f"bold {ui.COLORS['primary']}")
    line.append(f"  v{APP_VERSION}", style=ui.COLORS["faint"])
    if section:
        line.append("  │  ", style=ui.COLORS["border"])
        line.append(section.upper(), style=ui.COLORS["secondary"])

    status = state.agent_status()
    tone = state.status_tone
    right = f"{ui.dot('on' if status['STATUS'] == 'ACTIVE' else 'off')} {status['STATUS']}"
    gap = max(1, ui.content_width() - len(line.plain) - len(right) - 2)
    line.append(" " * gap, style=ui.COLORS["border"])
    line.append(right, style=ui.COLORS[tone])
    return line


def storage_footer(state: AppState) -> Text:
    """Subtle footer showing where local state lives."""
    line = Text()
    line.append(" local state  ", style=ui.COLORS["faint"])
    marker = "sqlite" if state.store.persistent else "memory (read-only fs)"
    line.append(f"{state.store.db_path.name}", style=ui.COLORS["muted"])
    line.append(f"  ·  {marker}", style=ui.COLORS["faint"])
    return line


def toast_line(toast: Optional[ScreenResult]) -> Optional[Text]:
    """Render a previous screen's message as a one-line banner."""
    if toast is None or not toast.message:
        return None
    tone = toast.tone if toast.tone in ui.COLORS else "primary"
    line = Text()
    line.append("▌ ", style=ui.COLORS[tone])
    line.append(toast.message, style=ui.COLORS[tone])
    return line


def status_panel(state: AppState) -> Panel:
    """The agent status readout used on the welcome and dashboard screens."""
    status = state.agent_status()
    body = Text()
    rows = [
        ("PLAN", status["PLAN"], ui.COLORS["text"]),
        (
            "STATUS",
            f"{ui.dot('on' if status['STATUS'] == 'ACTIVE' else 'off')} {status['STATUS']}",
            ui.COLORS[state.status_tone],
        ),
        (
            "TODAY",
            status["TODAY"],
            ui.COLORS["secondary"] if state.is_unlimited else ui.COLORS["text"],
        ),
        ("AI ENGINE", f"{ui.dot('on')} READY", ui.COLORS["success"]),
        ("WHATSAPP", f"{ui.dot('off')} NOT CONNECTED", ui.COLORS["faint"]),
    ]
    for index, (label, value, style) in enumerate(rows):
        if index:
            body.append("\n")
        body.append(label.ljust(12), style=ui.COLORS["muted"])
        body.append(value, style=style)
    return ui.panel(body, title="AGENT", subtitle="MOCK RUNTIME", accent="primary")


def usage_block(state: AppState, width: int = 30, title: str = "TODAY'S REPLIES") -> Group:
    """Numeric usage meter plus remaining/quota detail."""
    head = Text()
    head.append(title, style=f"bold {ui.COLORS['muted']}")
    head.append("\n")

    if state.is_unlimited:
        meter_line = Text()
        meter_line.append("█" * min(width, 24), style=ui.COLORS["secondary"])
        meter_line.append("  UNLIMITED", style=f"bold {ui.COLORS['secondary']}")
        detail = Text("No daily ceiling on Premium.", style=ui.COLORS["faint"])
    else:
        bar = ui.meter(state.fraction, width)
        meter_line = Text()
        meter_line.append_text(bar)
        meter_line.append(f"  {state.used} / {state.limit}", style=ui.COLORS["text"])
        detail = Text()
        detail.append(f"Remaining {state.remaining or 0} replies", style=ui.COLORS[state.usage_tone])
        detail.append(f"  ·  resets {state.usage['date']}", style=ui.COLORS["faint"])

    return Group(head, meter_line, detail)


def plan_card(plan: Plan, state: AppState) -> Group:
    """Headline + price + feature checklist for a plan."""
    title = Text()
    title.append(plan.name.upper(), style=f"bold {plan.hue}")
    title.append(f"   {plan.price_label}", style=ui.COLORS["faint"])

    subtitle = Text()
    subtitle.append(plan.tagline, style=ui.COLORS["muted"])

    return Group(title, subtitle, Text(), ui.bullet_list(plan.features))


def history_table(state: AppState) -> Text:
    """Seven-day usage chart (real today + deterministic demo history).

    Hand-laid as a Text block so it aligns exactly with the rest of the UI at
    any terminal width, instead of inheriting table cell padding.
    """
    rows = state.history()
    label_w = 5
    count_w = 5
    bar_w = max(8, min(30, ui.content_width() - label_w - count_w - 8))

    chart = Text()
    for row in rows:
        fraction = row["fraction"]
        is_today = row["is_today"]

        day_style = f"bold {ui.COLORS['primary']}" if is_today else ui.COLORS["muted"]
        if is_today:
            level_style = ui.COLORS["primary"]
        elif fraction >= 0.8:
            level_style = ui.COLORS["warning"]
        else:
            level_style = ui.COLORS["faint"]

        bar = ui.meter(fraction, bar_w)
        bar.stylize(level_style)

        chart.append(" " + ("›" if is_today else " "), style=ui.COLORS["primary"])
        chart.append(row["label"].rjust(label_w), style=day_style)
        chart.append("   ")
        chart.append(str(row["replies"]).rjust(count_w), style=ui.COLORS["text"])
        chart.append("   ")
        chart.append_text(bar)
        chart.append("\n")

    chart.append("\n")
    chart.append("  ", style=ui.COLORS["faint"])
    if state.is_unlimited:
        chart.append("scaled to the busiest day · no daily cap", style=ui.COLORS["faint"])
    else:
        chart.append(f"daily cap {state.limit} replies", style=ui.COLORS["faint"])
    return chart


def compose(*parts) -> Group:
    """Small helper so screens read top-to-bottom."""
    return Group(*parts)


def _nav(options: Sequence[MenuOption], prompt: str = "What would you like to do?") -> str:
    return select(options, prompt=prompt)


# ==========================================================================
# Screens
# ==========================================================================
def splash(state: AppState, toast: Optional[ScreenResult] = None) -> ScreenResult:
    """Brand mark + short loading sequence, then route onward."""
    console = ui.get_console()
    console.print()
    console.print(animations.logo_renderable())
    console.print()
    animations.boot()
    console.print()

    destination = "dashboard" if state.has_plan else "welcome"
    return ScreenResult.go(destination)


def welcome(state: AppState, toast: Optional[ScreenResult] = None) -> ScreenResult:
    """Home hub: agent status + adaptive primary actions."""
    ui.render(header(state, "welcome"))

    banner = toast_line(toast)
    if banner is not None:
        ui.render(banner)

    greeting = Text()
    greeting.append("\n  Welcome back.\n", style=f"bold {ui.COLORS['text']}")
    if not state.has_plan:
        greeting.append(
            "  Choose how you want to run your assistant.\n",
            style=ui.COLORS["muted"],
        )
    else:
        greeting.append(
            f"  {APP_TAGLINE} · {state.usage['date']}\n",
            style=ui.COLORS["muted"],
        )
    ui.render(greeting)

    parts: List[object] = [status_panel(state)]
    if state.has_plan:
        parts.extend([Text(), usage_block(state)])
    ui.render(compose(*parts))

    if state.has_plan:
        options = [
            MenuOption("1", "Start agent", "simulated session", "primary"),
            MenuOption("2", "Usage", "detail & history", "primary"),
            MenuOption("3", "Settings", "local preferences", "primary"),
            MenuOption("4", "Upgrade", "premium preview", "secondary"),
            MenuOption("5", "Account", "sign in / subscription", "primary"),
            MenuOption("Q", "Exit", "", "error"),
        ]
    else:
        options = [
            MenuOption("1", "Start free trial", f"{plans_mod.TRIAL_DAILY_LIMIT} AI replies per day", "primary"),
            MenuOption("2", "Upgrade to premium", "unlimited replies", "secondary"),
            MenuOption("3", "Settings", "local preferences", "primary"),
            MenuOption("4", "Login", "existing account", "primary"),
            MenuOption("5", "Register", "create account", "primary"),
            MenuOption("Q", "Quit", "", "error"),
        ]

    choice = _nav(options)
    if choice == "4" and not state.has_plan:
        return ScreenResult.go("login")
    if choice == "5":
        return ScreenResult.go("register" if not state.has_plan else "account")
    return _dispatch_welcome(choice, state)


def _dispatch_welcome(choice: str, state: AppState) -> ScreenResult:
    """Map a welcome/dashboard menu key to its destination.

    Only an explicit quit key exits; anything unrecognised simply stays put.
    """
    home = "dashboard" if state.has_plan else "welcome"
    if choice == "1":
        return ScreenResult.go("agent" if state.has_plan else "free_trial")
    if choice == "2":
        return ScreenResult.go("premium" if state.has_plan else "dashboard")
    if choice == "3":
        return ScreenResult.go("settings")
    if choice == "4":
        return ScreenResult.go("upgrade")
    if choice == "q":
        return ScreenResult.go("goodbye")
    return ScreenResult.go(home)


def free_trial(state: AppState, toast: Optional[ScreenResult] = None) -> ScreenResult:
    """Free trial plan screen with a live local usage preview."""
    ui.render(header(state, "plan · free trial"))
    banner = toast_line(toast)
    if banner is not None:
        ui.render(banner)

    plan = plans_mod.PLANS[plans_mod.FREE_TRIAL]
    ui.render(compose(plan_card(plan, state), Text(), usage_block(state)))

    note = Text()
    note.append("  Limit is simulated locally", style=ui.COLORS["faint"])
    note.append(" · no account, no payment\n", style=ui.COLORS["faint"])
    ui.render(note)

    options = [
        MenuOption("1", "Start free trial", "activate plan", "primary"),
        MenuOption("2", "Upgrade to premium", "unlimited", "secondary"),
        MenuOption("B", "Back", "", "muted"),
    ]
    choice = _nav(options, prompt="Select an option")
    if choice == "1":
        if state.is_exhausted:
            return ScreenResult.go(
                "welcome",
                f"Daily limit reached ({state.used}/{state.limit}). Upgrade for unlimited replies.",
                "warning",
            )
        state.select_plan(plan.key)
        return ScreenResult.go(
            "dashboard", f"Free trial active. {plan.daily_limit} replies available today.", "success"
        )
    if choice == "2":
        return ScreenResult.go("premium")
    return ScreenResult.go("welcome")


def premium(state: AppState, toast: Optional[ScreenResult] = None) -> ScreenResult:
    """Premium plan preview. No payment is taken."""
    ui.render(header(state, "plan · premium"))
    banner = toast_line(toast)
    if banner is not None:
        ui.render(banner)

    plan = plans_mod.PLANS[plans_mod.PREMIUM]
    ui.render(compose(plan_card(plan, state), Text(), usage_block(state, title="PREVIEW · TODAY'S REPLIES")))

    options = [
        MenuOption("1", "Continue", "review checkout", "secondary"),
        MenuOption("B", "Back", "", "muted"),
    ]
    choice = _nav(options, prompt="Select an option")
    if choice == "1":
        return ScreenResult.go("subscribe")
    return ScreenResult.go("upgrade" if state.has_plan else "welcome")


def subscribe(state: AppState, toast: Optional[ScreenResult] = None) -> ScreenResult:
    """Placeholder for the future payment / verification flow."""
    ui.render(header(state, "checkout"))
    banner = toast_line(toast)
    if banner is not None:
        ui.render(banner)

    body = Text()
    body.append("Subscription system coming soon.\n", style=f"bold {ui.COLORS['warning']}")
    body.append("\n")
    body.append("Payment integration will be added later.\n", style=ui.COLORS["muted"])
    body.append("No charge is made in this prototype.\n", style=ui.COLORS["faint"])

    ui.render(ui.panel(body, title="CHECKOUT", accent="secondary"))

    roadmap = Text()
    roadmap.append("  Planned steps\n", style=ui.COLORS["muted"])
    roadmap.append("  ·  plan selection → payment → verification → entitlement\n", style=ui.COLORS["faint"])
    ui.render(roadmap)

    options = [
        MenuOption("B", "Back", "", "muted"),
        MenuOption("Q", "Quit", "", "error"),
    ]
    choice = _nav(options, prompt="Select an option")
    if choice == "q":
        return ScreenResult.go("goodbye")
    return ScreenResult.go("premium")


def upgrade(state: AppState, toast: Optional[ScreenResult] = None) -> ScreenResult:
    """Comparison view reached from the dashboard."""
    ui.render(header(state, "upgrade"))
    banner = toast_line(toast)
    if banner is not None:
        ui.render(banner)

    trial = plans_mod.PLANS[plans_mod.FREE_TRIAL]
    premium_plan = plans_mod.PLANS[plans_mod.PREMIUM]

    table = Table(box=None, expand=True, show_edge=False, pad_edge=False)
    table.add_column("", width=1)
    table.add_column("FREE TRIAL", style=ui.COLORS["primary"], no_wrap=True)
    table.add_column("PREMIUM", style=ui.COLORS["secondary"], no_wrap=True)

    table.add_row("·", trial.headline, premium_plan.headline)
    table.add_row("·", trial.price_label, premium_plan.price_label)
    table.add_row("·", "AI-generated replies", "AI assistant")
    table.add_row("·", "Usage dashboard", "WhatsApp automation")
    table.add_row("·", "Daily cap of " + str(trial.daily_limit), "Priority features")

    ui.render(ui.panel(table, title="COMPARE", accent="secondary"))

    options = [
        MenuOption("1", "Continue to checkout", "", "secondary"),
        MenuOption("B", "Back", "", "muted"),
    ]
    choice = _nav(options, prompt="Select an option")
    if choice == "1":
        return ScreenResult.go("premium")
    return ScreenResult.go("welcome" if not state.has_plan else "dashboard")


def dashboard(state: AppState, toast: Optional[ScreenResult] = None) -> ScreenResult:
    """Main agent dashboard shown once a plan is active."""
    ui.render(header(state, "dashboard"))
    banner = toast_line(toast)
    if banner is not None:
        ui.render(banner)

    plan_line = Text()
    plan_line.append("\n  ")
    plan_line.append(
        state.plan.name.upper() if state.plan else "NO PLAN",
        style=f"bold {state.plan.hue if state.plan else ui.COLORS['muted']}",
    )
    plan_line.append("   ")
    plan_line.append(
        f"{ui.dot('on' if state.status_label == 'ACTIVE' else 'off')} {state.status_label}",
        style=ui.COLORS[state.status_tone],
    )
    ui.render(plan_line)

    ui.render(compose(status_panel(state), Text(), usage_block(state)))
    ui.render(ui.rule("LAST 7 DAYS"), history_table(state))
    ui.render(storage_footer(state))

    options = [
        MenuOption("1", "Start agent", "simulated session", "primary"),
        MenuOption("2", "Usage", "detail & history", "primary"),
        MenuOption("3", "Settings", "local preferences", "primary"),
        MenuOption("4", "Upgrade", "premium preview", "secondary"),
        MenuOption("Q", "Exit", "", "error"),
    ]
    choice = _nav(options)
    return _dispatch_welcome(choice, state)


def agent(state: AppState, toast: Optional[ScreenResult] = None) -> ScreenResult:
    """Simulated agent session.

    This is *presentation only*: nothing is read from or sent to WhatsApp.
    It produces mock replies so the prototype has live data to display.
    """
    ui.render(header(state, "agent"))
    banner = toast_line(toast)
    if banner is not None:
        ui.render(banner)

    if state.is_exhausted:
        body = Text()
        body.append("Daily free-trial limit reached.\n", style=f"bold {ui.COLORS['error']}")
        body.append(f"\nUsed {state.used} of {state.limit} replies on {state.usage['date']}.\n", style=ui.COLORS["muted"])
        body.append("Upgrade to premium for unlimited replies.\n", style=ui.COLORS["secondary"])
        ui.render(ui.panel(body, title="AGENT PAUSED", accent="error"))
        options = [
            MenuOption("4", "Upgrade", "", "secondary"),
            MenuOption("B", "Back", "", "muted"),
        ]
        choice = _nav(options, prompt="Select an option")
        if choice == "4":
            return ScreenResult.go("upgrade")
        return ScreenResult.go("dashboard")

    batch = 3
    allowance = state.limit if state.limit is not None else batch
    batch = min(batch, max(1, allowance)) if state.limit is not None else batch

    plan_line = Text()
    plan_line.append("\n  Queued batch", style=ui.COLORS["muted"])
    plan_line.append(f"  {batch} mock replies", style=ui.COLORS["text"])
    plan_line.append("  ·  WhatsApp link simulated\n", style=ui.COLORS["faint"])
    ui.render(plan_line)

    transcript = Text()
    transcript.append(ui.rule("SIMULATION · NO MESSAGES ARE SENT"))
    transcript.append("\n")
    for index, entry in enumerate(state.mock_transcript(batch)):
        if index:
            transcript.append("\n")
        transcript.append(f"  {entry.stamp}  ", style=ui.COLORS["faint"])
        transcript.append(f"{entry.contact}  ", style=ui.COLORS["primary"])
        transcript.append(f"“{entry.text}”", style=ui.COLORS["text"])
        transcript.append(f"  [{entry.tone}]", style=ui.COLORS["faint"])
    ui.render(transcript)

    before = state.used
    state.consume_reply(batch)
    ui.render()
    summary = Text()
    summary.append(f"  {ui.dot('on')} Generated", style=ui.COLORS["success"])
    summary.append(f" {batch} mock replies", style=ui.COLORS["text"])
    summary.append(f"  ·  usage {before} → {state.used}", style=ui.COLORS["muted"])
    ui.render(summary)

    options = [
        MenuOption("1", "Run again", "consume more", "primary"),
        MenuOption("2", "Usage", "detail & history", "primary"),
        MenuOption("B", "Back", "dashboard", "muted"),
    ]
    choice = _nav(options, prompt="Select an option")
    if choice == "1":
        return ScreenResult.go("agent")
    if choice == "2":
        return ScreenResult.go("usage")
    return ScreenResult.go("dashboard")


def usage(state: AppState, toast: Optional[ScreenResult] = None) -> ScreenResult:
    """Usage detail: meter, seven-day history and mock transcript."""
    ui.render(header(state, "usage"))
    banner = toast_line(toast)
    if banner is not None:
        ui.render(banner)

    ui.render(compose(usage_block(state)))
    ui.render(ui.rule("LAST 7 DAYS"), history_table(state))

    transcript = state.mock_transcript(3)
    if transcript:
        body = Text()
        for index, entry in enumerate(transcript):
            if index:
                body.append("\n")
            body.append(f"{entry.stamp}  ", style=ui.COLORS["faint"])
            body.append(f"{entry.contact}  ", style=ui.COLORS["primary"])
            body.append(f"“{entry.text}”", style=ui.COLORS["text"])
        ui.render(ui.panel(body, title="RECENT REPLIES", subtitle="DEMO DATA", accent="secondary"))

    total = Text()
    total.append(f"  7-day total {state.week_total} replies", style=ui.COLORS["muted"])
    total.append(f"  ·  today {state.used}", style=ui.COLORS["faint"])
    ui.render(total)

    options = [
        MenuOption("1", "Start agent", "generate more", "primary"),
        MenuOption("3", "Settings", "reset usage", "primary"),
        MenuOption("B", "Back", "", "muted"),
    ]
    choice = _nav(options, prompt="Select an option")
    if choice == "1":
        return ScreenResult.go("agent")
    if choice == "3":
        return ScreenResult.go("settings")
    return ScreenResult.go("dashboard" if state.has_plan else "welcome")


def settings(state: AppState, toast: Optional[ScreenResult] = None) -> ScreenResult:
    """Local preferences plus prototype housekeeping actions."""
    ui.render(header(state, "settings"))
    banner = toast_line(toast)
    if banner is not None:
        ui.render(banner)

    settings_map = state.settings
    body = Text()
    entries = [
        ("animations", "Animations", "boot sequence and motion"),
        ("demo_mode", "Demo mode", "mock agent data"),
        ("compact_layout", "Compact layout", "denser panels"),
        ("confirm_quit", "Confirm on exit", "ask before closing"),
    ]
    for index, (key, label, hint) in enumerate(entries):
        if index:
            body.append("\n")
        enabled = bool(settings_map.get(key))
        body.append(label.ljust(18), style=ui.COLORS["text"])
        body.append("ON " if enabled else "OFF", style=ui.COLORS["success"] if enabled else ui.COLORS["faint"])
        body.append(f"   {hint}", style=ui.COLORS["faint"])
    ui.render(ui.panel(body, title="PREFERENCES", accent="primary"))

    data_line = Text()
    data_line.append("  Plan        ", style=ui.COLORS["muted"])
    data_line.append(state.plan.name if state.plan else "none selected", style=ui.COLORS["text"])
    data_line.append("\n  Usage date  ", style=ui.COLORS["muted"])
    data_line.append(state.usage["date"], style=ui.COLORS["text"])
    data_line.append("\n  Replies     ", style=ui.COLORS["muted"])
    data_line.append("UNLIMITED" if state.is_unlimited else f"{state.used} / {state.limit}", style=ui.COLORS["text"])
    data_line.append("\n  Database    ", style=ui.COLORS["muted"])
    data_line.append(str(state.store.db_path), style=ui.COLORS["faint"])
    ui.render(ui.panel(data_line, title="LOCAL DATA", accent="secondary"))

    options = [
        MenuOption("1", "Toggle animations", "", "primary"),
        MenuOption("2", "Reset today's usage", "local only", "warning"),
        MenuOption("3", "Forget plan", "return to plan select", "warning"),
        MenuOption("B", "Back", "", "muted"),
    ]
    choice = _nav(options, prompt="Select an option")

    if choice == "1":
        enabled = state.toggle_setting("animations")
        tone = "success" if enabled else "info"
        return ScreenResult.go("settings", f"Animations {'enabled' if enabled else 'disabled'}.", tone)
    if choice == "2":
        if confirm("Reset today's usage counter?"):
            state.reset_usage()
            return ScreenResult.go("settings", "Today's usage reset to 0.", "success")
        return ScreenResult.go("settings", "Reset cancelled.", "info")
    if choice == "3":
        if confirm("Forget the selected plan?"):
            state.clear_plan()
            return ScreenResult.go("welcome", "Plan cleared.", "info")
        return ScreenResult.go("settings", "Kept current plan.", "info")
    return ScreenResult.go("dashboard" if state.has_plan else "welcome")


def goodbye(state: AppState, toast: Optional[ScreenResult] = None) -> ScreenResult:
    """Final frame before the process exits."""
    ui.render(header(state, "exit"))
    body = Text()
    body.append("\n  Agent stopped.\n", style=f"bold {ui.COLORS['text']}")
    body.append(f"  Replies generated today: ", style=ui.COLORS["muted"])
    body.append("unlimited" if state.is_unlimited else str(state.used), style=ui.COLORS["text"])
    body.append("\n  Local state saved. Run ", style=ui.COLORS["muted"])
    body.append("python start.py", style=ui.COLORS["primary"])
    body.append(" to continue.\n", style=ui.COLORS["muted"])
    ui.render(ui.panel(body, title="GOODBYE", accent="secondary"))
    pause("Press Enter to close")
    return ScreenResult.exit()


# ==========================================================================
# Registry
# ==========================================================================

# ==========================================================================
# PHASE 12 - account (Firebase auth) and the live agent
# ==========================================================================
def _auth_note(auth, config) -> str:
    """The honest integration state, used as a panel title suffix."""
    return auth.status_note


def login(state: AppState, toast: Optional[ScreenResult] = None) -> ScreenResult:
    """Sign in with username + password via Firebase email/password auth."""
    from auth import AuthService
    from config import get_config

    config = get_config()
    auth = AuthService(config)
    ui.render(header(state, "login"))
    banner = toast_line(toast)
    if banner is not None:
        ui.render(banner)

    if not config.auth_configured:
        ui.render(
            ui.notice(
                "Firebase is NOT CONFIGURED. Sign-in is unavailable in this build.",
                "error",
            )
        )
        if nav.confirm("Back to welcome", yes_key="y"):
            return ScreenResult.go("welcome")
        return ScreenResult.exit()

    ui.render()
    intro = Text()
    intro.append("  Sign in with your ShiPu WP account.\n", style=ui.COLORS["text"])
    intro.append(
        "  Accounts are shared with the website.\n", style=ui.COLORS["muted"]
    )
    ui.render(intro)

    username = nav.ask("Username", state.username)
    if not username:
        return ScreenResult.go("welcome", "Cancelled.", "muted")
    password = nav.ask("Password", "", secret=True)
    if not password:
        return ScreenResult.go("welcome", "Cancelled.", "muted")

    state.username = username.strip()
    ui.render(ui.notice("Signing in...", "info"))
    try:
        session = auth.login(state.username, password)
    except Exception as exc:
        return ScreenResult.go("login", f"Sign-in failed: {exc}", "error")

    state.bind_auth(auth)
    state.apply_session(session)
    message = f"Welcome back, @{session.username}." if session.username else "Signed in."
    return ScreenResult.go("account", message, "success")


def register(state: AppState, toast: Optional[ScreenResult] = None) -> ScreenResult:
    """Create a new ShiPu WP account."""
    from auth import AuthService
    from config import get_config

    config = get_config()
    auth = AuthService(config)
    ui.render(header(state, "register"))
    banner = toast_line(toast)
    if banner is not None:
        ui.render(banner)

    if not config.auth_configured:
        ui.render(ui.notice("Firebase is NOT CONFIGURED. Cannot register.", "error"))
        if nav.confirm("Back to welcome", yes_key="y"):
            return ScreenResult.go("welcome")
        return ScreenResult.exit()

    rules = Text()
    rules.append("\n  Choose a username", style=ui.COLORS["primary"])
    rules.append("\n  3-32 characters · letters, digits, _ . -\n", style=ui.COLORS["muted"])
    rules.append(
        "  This is the same account you use on the website.\n\n",
        style=ui.COLORS["faint"],
    )
    ui.render(rules)

    username = nav.ask("Username", state.username)
    if not username:
        return ScreenResult.go("welcome", "Cancelled.", "muted")
    if not (3 <= len(username.strip()) <= 32) or not all(
        ch.isalnum() or ch in "._-" for ch in username.strip()
    ):
        return ScreenResult.go(
            "register", "Username must be 3-32 characters (letters, digits, _ . -).", "error"
        )

    password = nav.ask("Password", "", secret=True)
    if not password:
        return ScreenResult.go("welcome", "Cancelled.", "muted")
    if len(password) < 6:
        return ScreenResult.go("register", "Password must be at least 6 characters.", "error")
    confirm = nav.ask("Confirm password", "", secret=True)
    if confirm != password:
        return ScreenResult.go("register", "Passwords do not match.", "error")

    state.username = username.strip()
    ui.render(ui.notice("Creating account...", "info"))
    try:
        session = auth.register(state.username, password)
    except Exception as exc:
        return ScreenResult.go("register", f"Registration failed: {exc}", "error")

    state.bind_auth(auth)
    state.apply_session(session)
    if session.plan == "free":
        return ScreenResult.go(
            "free_trial", f"Account created: @{session.username}", "success"
        )
    return ScreenResult.go("account", f"Account created: @{session.username}", "success")


def account(state: AppState, toast: Optional[ScreenResult] = None) -> ScreenResult:
    """Server-authoritative account view (idea.txt items 16, 20)."""
    from config import get_config

    config = get_config()
    ui.render(header(state, "account"))
    banner = toast_line(toast)
    if banner is not None:
        ui.render(banner)

    auth = state.auth or AuthServiceProxy(config)
    session = auth.session

    if not config.auth_configured:
        ui.render(ui.notice("Firebase is NOT CONFIGURED - showing local mirror only.", "warning"))
    elif not session.signed_in:
        ui.render(ui.notice("You are signed out.", "warning"))

    rows = session.summary_rows(config.pricing.free_daily_replies)
    body = Text()
    for label, value in rows.items():
        body.append(f"  {label:<10}", style=ui.COLORS["muted"])
        accent = ui.COLORS["primary"]
        if label == "PLAN" and value == "PRO":
            accent = ui.COLORS["secondary"]
        if label == "STATUS" and value.startswith("PRO"):
            accent = ui.COLORS["success"]
        body.append(f"{value}\n", style=accent)

    source = "SERVER (authoritative)" if session.online else "LOCAL MIRROR (not proof of Pro)"
    source_color = ui.COLORS["success"] if session.online else ui.COLORS["warning"]
    body.append(f"\n  Source      {source}\n", style=source_color)
    if session.last_error:
        body.append(f"\n  Last error: {session.last_error}\n", style=ui.COLORS["error"])
    ui.render(ui.panel(body, title="ACCOUNT", accent="primary"))

    options = [
        MenuOption("1", "Refresh", "re-sync from server", "primary"),
        MenuOption("2", "Live agent", "watch messages", "primary"),
        MenuOption("4", "Upgrade", "go premium", "secondary"),
        MenuOption("3", "Sign out", "", "muted"),
        MenuOption("B", "Back", "dashboard", "muted"),
    ]
    choice = _nav(options, prompt="Select an option")
    if choice == "1":
        auth.sync()
        state.apply_session(auth.session)
        return ScreenResult.go("account", "Account refreshed.", "info")
    if choice == "2":
        return ScreenResult.go("agent_live")
    if choice == "4":
        return ScreenResult.go("premium")
    if choice == "3":
        auth.logout()
        state.bind_auth(None)
        return ScreenResult.go("welcome", "Signed out.", "info")
    return ScreenResult.go("dashboard")


def agent_live(state: AppState, toast: Optional[ScreenResult] = None) -> ScreenResult:
    """Real agent: watches the Android notification bridge and replies.

    Nothing is mocked here. If the listener is not attached, or the AI is not
    configured, or the subscription is inactive, the screen says so.
    """
    from config import get_config
    from listener import EventBus, ReplyAgent

    config = get_config()
    auth = state.auth or AuthServiceProxy(config)
    bus = EventBus()
    bus.ensure_dir()
    agent = ReplyAgent(bus=bus, auth=auth, config=config)

    ok, reason = agent.gate()
    ui.render(header(state, "agent · live"))

    gate_body = Text()
    gate_body.append(f"  Bridge      {bus.bridge_dir}\n", style=ui.COLORS["faint"])
    gate_body.append(f"  AI          {_ai_status(config)}\n", style=ui.COLORS["faint"])
    gate_body.append(f"  Duplicates  suppressed automatically\n", style=ui.COLORS["faint"])
    gate_body.append("\n  ")
    if ok:
        gate_body.append("READY - listening for messages", style=f"bold {ui.COLORS['success']}")
    else:
        gate_body.append(f"BLOCKED: {reason}", style=f"bold {ui.COLORS['warning']}")
    ui.render(ui.panel(gate_body, title="AGENT STATUS", accent="success" if ok else "warning"))

    if not ok:
        hint = Text()
        hint.append("\n  The agent will not reply while blocked.\n", style=ui.COLORS["warning"])
        if reason.startswith("not signed in"):
            hint.append("  Sign in first.\n", style=ui.COLORS["muted"])
        elif reason == "Not configured":
            hint.append("  Set OPENROUTER_API_KEY in .env\n", style=ui.COLORS["muted"])
        elif reason.startswith("offline"):
            hint.append("  Network unavailable - subscription cannot be verified.\n", style=ui.COLORS["muted"])
        else:
            hint.append(f"  {reason}\n", style=ui.COLORS["muted"])
        ui.render(hint)
        if nav.confirm("Back to account", yes_key="y"):
            return ScreenResult.go("account")
        return ScreenResult.go("dashboard")

    agent.running = True
    agent.publish_status()
    ui.render(
        ui.notice(
            "Listening. Press Ctrl+C to stop - or attach the Android listener.",
            "info",
        )
    )

    try:
        while agent.running:
            outcomes = agent.tick()
            for outcome in outcomes:
                _render_outcome(outcome)
            if not outcomes:
                time.sleep(bus.poll_interval)
    except KeyboardInterrupt:
        pass
    finally:
        agent.stop()
        agent.publish_status()

    ui.render()
    summary = Text()
    summary.append(f"  {agent.stats.header()}\n", style=ui.COLORS["muted"])
    ui.render(summary)
    return ScreenResult.go("account", "Agent stopped.", "info")


def _render_outcome(outcome) -> None:
    """One inbound message, rendered honestly."""
    from config import get_config

    free = get_config().pricing.free_daily_replies
    body = Text()
    event = outcome.event
    body.append(f"  {event.display}\n", style=f"bold {ui.COLORS['primary']}")
    body.append(f"  ← {event.message}\n", style=ui.COLORS["text"])
    if outcome.replied:
        body.append(f"  → {outcome.text}\n", style=ui.COLORS["success"])
        body.append(
            f"  [{outcome.model} · {outcome.duration_ms}ms · {free - state_free_left(outcome)} left]\n",
            style=ui.COLORS["faint"],
        )
        tone, accent = "REPLIED", "success"
    elif outcome.error:
        body.append(f"  ✗ {outcome.error}\n", style=ui.COLORS["error"])
        tone, accent = "ERROR", "error"
    else:
        body.append(f"  · skipped: {outcome.reason}\n", style=ui.COLORS["faint"])
        tone, accent = "SKIPPED", "muted"
    ui.render(ui.panel(body, title=tone, accent=accent))


def state_free_left(outcome) -> int:
    """Best-effort remaining-allowance display; never used for gating."""
    try:
        from config import get_config

        return max(0, get_config().pricing.free_daily_replies - _usage_today())
    except Exception:
        return 0


def _usage_today() -> int:
    import time as _time

    from storage import get_store

    key = f"agent:usage:{_time.strftime('%Y-%m-%d')}"
    try:
        return int(get_store().get(key) or 0)
    except Exception:
        return 0


def _ai_status(config) -> str:
    from ai import status_line

    return status_line()


class AuthServiceProxy:
    """A signed-out AuthService used when the app state has none yet."""

    def __init__(self, config) -> None:
        from auth import AuthService

        self._inner = AuthService(config)

    def __getattr__(self, item):
        return getattr(self._inner, item)


# ==========================================================================
# Router table
# ==========================================================================
SCREENS = {
    "splash": splash,
    "welcome": welcome,
    "free_trial": free_trial,
    "premium": premium,
    "subscribe": subscribe,
    "upgrade": upgrade,
    "dashboard": dashboard,
    "agent": agent,
    "agent_live": agent_live,
    "usage": usage,
    "settings": settings,
    "login": login,
    "register": register,
    "account": account,
    "goodbye": goodbye,
}
