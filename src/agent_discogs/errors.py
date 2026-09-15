"""SDK exceptions → recovery-oriented error text."""

from __future__ import annotations


def _api_message(exc: Exception) -> str:
    """Pull the Discogs-supplied message out of an API error's response body.

    The SDK types `response_body` as `dict | str` and means it: JSON error
    payloads arrive as a dict, while its HTTP-error boundary passes
    `response.text` verbatim for anything that is not a JSON object — gateway
    HTML, plain text, or an empty body.
    """
    body = getattr(exc, "response_body", None)
    if isinstance(body, str):
        return body
    message = body.get("message") if isinstance(body, dict) else None
    return message if isinstance(message, str) else ""


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
        # Discogs overloads 404 for the marketplace endpoints: a release that
        # does not exist and a release whose price data the caller may not read
        # both come back as 404, and only the body tells them apart. Without
        # this branch, `price` tells an agent to go search for a release it
        # just looked up successfully.
        message = _api_message(exc)
        if "seller settings" in message.lower():
            return (
                f"✗ Price data requires seller settings. Discogs said: {message}\n"
                "  Fill them out at discogs.com/settings/seller, then retry.\n"
                "  Other commands work without them."
            )
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
