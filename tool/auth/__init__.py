"""Authentication for ShiPu WP (PHASE 2).

    from auth import AuthService

``service.AuthService`` is the only object the UI should touch; it degrades to
a clearly-labelled signed-out state whenever Firebase is not configured.
"""

from .firebase import AuthError, FirebaseClient, NotConfigured
from .service import PLAN_FREE, PLAN_PRO, AuthService, Session

__all__ = [
    "AuthError",
    "AuthService",
    "FirebaseClient",
    "NotConfigured",
    "PLAN_FREE",
    "PLAN_PRO",
    "Session",
]
