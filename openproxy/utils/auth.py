from __future__ import annotations

import hmac

from openproxy.config import settings


def verify_auth(token: str) -> bool:
    """Verify a token against the configured AUTH_TOKEN.

    Uses constant-time comparison to prevent timing attacks.
    Returns True if AUTH_TOKEN is not configured (auth disabled).
    """
    if not settings.auth_token:
        return True
    if not token:
        return False
    return hmac.compare_digest(token, settings.auth_token)
