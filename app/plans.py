"""Plan catalogue for ShiPu WP.

Every user-facing number (daily reply allowance, feature lists, pricing
labels) lives here so screens never hard-code values. Adding a real billing
system later only means extending this module plus a ``storage`` column.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

# --------------------------------------------------------------------------
# Plan identifiers (persisted as strings in the database)
# --------------------------------------------------------------------------
FREE_TRIAL: str = "free_trial"
PREMIUM: str = "premium"
NO_PLAN: str = "none"

PLAN_IDS = (FREE_TRIAL, PREMIUM)

# --------------------------------------------------------------------------
# Marketing / UI constants
# --------------------------------------------------------------------------
APP_NAME: str = "ShiPu WP"
APP_TAGLINE: str = "AI-powered WhatsApp Assistant"
APP_VERSION: str = "0.1.0"
APP_BUILD: str = "ui-prototype"

# Free trial allowance resets every calendar day (local time).
TRIAL_DAILY_LIMIT: int = 25
# Premium plans are not throttled.
PREMIUM_DAILY_LIMIT: Optional[int] = None


@dataclass(frozen=True)
class Plan:
    """Immutable description of a subscription tier."""

    key: str
    name: str
    short_name: str
    tagline: str
    daily_limit: Optional[int]  # None -> unlimited
    price_label: str
    features: List[str] = field(default_factory=list)
    accent: str = "primary"  # theme key used by ui.COLORS / panel accents
    hue: str = "bright_cyan"  # literal rich colour for headline text

    @property
    def is_unlimited(self) -> bool:
        return self.daily_limit is None

    @property
    def limit_label(self) -> str:
        """Short limit string, e.g. ``25 / DAY`` or ``UNLIMITED``."""
        return "UNLIMITED" if self.is_unlimited else f"{self.daily_limit} / DAY"

    @property
    def headline(self) -> str:
        """One-line allowance summary used on cards and dashboards."""
        if self.is_unlimited:
            return "Unlimited AI replies"
        return f"{self.daily_limit} AI replies per day"


PLANS: Dict[str, Plan] = {
    FREE_TRIAL: Plan(
        key=FREE_TRIAL,
        name="Free Trial",
        short_name="TRIAL",
        tagline="Explore the agent. No payment required.",
        daily_limit=TRIAL_DAILY_LIMIT,
        price_label="FREE",
        accent="primary",
        hue="bright_cyan",
        features=[
            "AI-generated replies",
            "WhatsApp assistant (simulated)",
            "Daily usage tracking",
            "Agent dashboard",
        ],
    ),
    PREMIUM: Plan(
        key=PREMIUM,
        name="Premium Subscription",
        short_name="PREMIUM",
        tagline="Full access, no reply ceiling.",
        daily_limit=PREMIUM_DAILY_LIMIT,
        price_label="COMING SOON",
        accent="secondary",
        hue="bright_magenta",
        features=[
            "Unlimited AI replies",
            "AI assistant",
            "WhatsApp automation",
            "Usage dashboard",
            "Priority features",
        ],
    ),
}


def get_plan(key: Optional[str]) -> Optional[Plan]:
    """Return the :class:`Plan` for ``key`` or ``None`` if unknown/absent."""
    if not key:
        return None
    return PLANS.get(key)


def is_valid_plan(key: Optional[str]) -> bool:
    """True when ``key`` maps to a known plan."""
    return key in PLANS