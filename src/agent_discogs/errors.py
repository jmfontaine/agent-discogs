"""SDK exceptions → recovery-oriented error text."""

from __future__ import annotations


def format_error(exc: Exception, context: str | None = None) -> str:
    """Map an exception to a recovery-oriented error message.

    Returns a ✗-prefixed string ready for stderr output.
    """
    from discogs_sdk import (
        AuthenticationError,
        DiscogsAPIError,
        DiscogsConnectionError,
        ForbiddenError,
        NotFoundError,
        RateLimitError,
    )

    if isinstance(exc, NotFoundError):
        entity = context or "Resource"
        return f'✗ {entity} not found. Try: agent-discogs search "<title>"'

    if isinstance(exc, AuthenticationError):
        return (
            "✗ Authentication failed. Check your DISCOGS_TOKEN.\n"
            "  Set token: export DISCOGS_TOKEN=<token> "
            "(discogs.com/settings/developers)"
        )

    if isinstance(exc, ForbiddenError):
        return "✗ Access forbidden. This endpoint may require different permissions."

    if isinstance(exc, RateLimitError):
        return "✗ Rate limit exceeded. Wait a moment and retry."

    if isinstance(exc, DiscogsAPIError):
        return f"✗ API error ({exc.status_code}): {exc}"

    if isinstance(exc, DiscogsConnectionError):
        return "✗ Connection error. Check your network and retry."

    if isinstance(exc, ValueError):
        return f"✗ {exc}"

    return f"✗ Unexpected error: {exc}"
