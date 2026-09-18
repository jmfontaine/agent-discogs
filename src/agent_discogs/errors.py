"""SDK exceptions → recovery-oriented errors, as text or a JSON envelope."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from typing import Any, NoReturn
from urllib.parse import urlsplit

from agent_discogs import trace


@dataclass(frozen=True)
class ErrorInfo:
    """A classified failure: a stable `code` for agents to branch on, a
    human message, and the recovery hint that the text output prints."""

    code: str
    message: str
    hint: str | None = None
    retry_after: int | None = None
    status: int | None = None
    limit: int | None = None
    remaining: int | None = None


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


def classify(exc: Exception, context: str | None = None) -> ErrorInfo:
    """Map an exception to an ErrorInfo. `context` names the thing that
    failed, e.g. "Release @r847868", and is used in not-found messages."""
    from discogs_sdk import (
        AuthenticationError,
        CacheMissError,
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
            return ErrorInfo(
                "seller_settings_required",
                f"Price data requires seller settings. Discogs said: {message}",
                "Fill them out at discogs.com/settings/seller, then retry. "
                "Other commands work without them; `get release` shows "
                "num_for_sale/lowest_price.",
                status=404,
            )
        return ErrorInfo(
            "not_found",
            f"{context or 'Resource'} not found.",
            'Try: agent-discogs search "<title>"',
            status=404,
        )

    if isinstance(exc, AuthenticationError):
        return ErrorInfo(
            "auth_required",
            "Authentication failed. Check your DISCOGS_TOKEN.",
            "Set token: export DISCOGS_TOKEN=<token> (discogs.com/settings/developers)",
            status=401,
        )

    if isinstance(exc, ForbiddenError):
        return ErrorInfo(
            "forbidden",
            "Access forbidden. This endpoint may require different permissions.",
            status=403,
        )

    if isinstance(exc, RateLimitError):
        raw = getattr(exc, "retry_after", None)
        retry_after = int(raw) if isinstance(raw, str) and raw.isdigit() else None
        wait = (
            f"Retry in {retry_after}s." if retry_after else "Wait a moment and retry."
        )
        rl = exc.ratelimit
        message = (
            "Rate limit exceeded."
            if rl is None
            else f"Rate limited: {rl.remaining}/{rl.limit} requests left this minute."
        )
        return ErrorInfo(
            "rate_limited",
            message,
            f"{wait} 60 req/min with DISCOGS_TOKEN, 25 without.",
            retry_after=retry_after,
            status=429,
            limit=None if rl is None else rl.limit,
            remaining=None if rl is None else rl.remaining,
        )

    if isinstance(exc, DiscogsAPIError):
        return ErrorInfo(
            "api_error", f"API error ({exc.status_code}): {exc}", status=exc.status_code
        )

    if isinstance(exc, DiscogsConnectionError):
        return ErrorInfo(
            "connection_error", "Connection error.", "Check your network and retry."
        )

    if isinstance(exc, CacheMissError):
        # The SDK emits no request event for a miss, so the footer learns
        # about it here: every miss an agent sees passes through classify().
        trace.note_cache_miss()
        split = urlsplit(exc.url)
        where = split.path + (f"?{split.query}" if split.query else "")
        return ErrorInfo(
            "cache_miss",
            f"Not cached: {exc.method} {where}",
            "Rerun without --cached to fetch it from the API.",
        )

    if isinstance(exc, ValueError):
        return ErrorInfo("invalid_argument", str(exc))

    return ErrorInfo("unexpected", f"Unexpected error: {exc}")


def format_error(exc: Exception, context: str | None = None) -> str:
    """Text rendering: `✗ message`, hint indented on the next line."""
    info = classify(exc, context)
    text = f"✗ {info.message}"
    if info.hint:
        text += f"\n  {info.hint}"
    return text


def error_document(exc: Exception, context: str | None = None) -> dict[str, Any]:
    """`{"code": ..., "message": ..., "hint": ...}` with empty fields omitted, so
    agents can test `error.code` and read `error.retry_after` without null
    checks on every field."""
    return {k: v for k, v in asdict(classify(exc, context)).items() if v is not None}


def format_error_json(exc: Exception, context: str | None = None) -> str:
    """JSON rendering: `{"error": {"code": ..., "message": ..., "hint": ...}}`."""
    return json.dumps({"error": error_document(exc, context)}, separators=(",", ":"))


def fail(exc: Exception, context: str | None = None, *, json_output: bool) -> NoReturn:
    """Report the error the way the caller asked for and exit 1.

    Text goes to stderr; the JSON envelope goes to stdout so a `--json` caller
    always gets one JSON document on stdout, success or failure.
    """
    if json_output:
        print(format_error_json(exc, context))
    else:
        print(format_error(exc, context), file=sys.stderr)
    sys.exit(1)
