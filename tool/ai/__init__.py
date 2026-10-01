"""AI gateway for ShiPu WP (PHASE 11).

    from ai import get_client, reply, status_line

The model and API key come from configuration only - never hard-coded.
"""

from .openrouter import (
    AIError,
    AINotConfigured,
    AIQuotaExceeded,
    AIResult,
    OpenRouterClient,
    SYSTEM_PROMPT,
    clean_reply,
    get_client,
    reply,
    status_line,
)

__all__ = [
    "AIError",
    "AINotConfigured",
    "AIQuotaExceeded",
    "AIResult",
    "OpenRouterClient",
    "SYSTEM_PROMPT",
    "clean_reply",
    "get_client",
    "reply",
    "status_line",
]
