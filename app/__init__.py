"""ShiPu WP - AI-powered WhatsApp Assistant (terminal UI prototype).

The package is intentionally small and layered:

``ui``          presentation primitives + the :func:`ui.run` entry point
``screens``     one function per screen, pure render + one question
``navigation``  key reading, menus and the screen router
``state``       in-memory model and every action the UI can take
``storage``     local SQLite persistence with daily rollover
``plans``       plan catalogue (single source of truth for numbers)
``animations``  brand mark and boot sequence

No WhatsApp, AI, payment or authentication code exists yet by design.
"""

__all__ = ["APP_NAME", "APP_VERSION", "__version__"]

APP_NAME = "ShiPu WP"
__version__ = "0.1.0"