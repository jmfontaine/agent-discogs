"""SDK exceptions → recovery-oriented errors, as text or a JSON envelope."""

from __future__ import annotations

import sys
from dataclasses import asdict, dataclass
from typing import Any, NoReturn
from urllib.parse import urlsplit

from discogs_sdk import (
    AuthenticationError,
    CacheError,
    CacheMissError,
    DiscogsAPIError,
    DiscogsConnectionError,
    ForbiddenError,
    NotFoundError,
    RateLimitError,
)

from agent_discogs import sanitize
from agent_discogs.json_output import dumps


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
    path: str | None = None  # the cache database, for `cache_error`


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


def _describe(exc: BaseException) -> str:
    """`Class: message`, with the module for a class outside builtins
    (`sqlite3.OperationalError: ...`), so the kind of failure survives."""
    cls = type(exc)
    name = (
        cls.__qualname__
        if cls.__module__ == "builtins"
        else f"{cls.__module__}.{cls.__qualname__}"
    )
    text = str(exc)
    return f"{name}: {text}" if text else name


def _cache_error(exc: CacheError) -> ErrorInfo:
    """A cache the SDK could not create, vet or open. The CLI deletes nothing:
    the hint tells the user what to fix, or which file to remove by hand."""
    cause = exc.__cause__
    reason = _describe(cause) if cause is not None else str(exc)
    action = f"could not {exc.operation}: " if exc.operation else ""
    if exc.path is None:
        return ErrorInfo(
            "cache_error",
            f"Cache error: {action}{reason}",
            "Check the cache's permissions and ownership. --fresh skips the cache.",
        )
    path = exc.path
    if exc.operation == "create the cache directory":
        # Nothing exists yet to delete: the path leading to it is the problem.
        hint = (
            f"Make {path.parent} a directory you own and can write to: fix the "
            "permissions or ownership on the way to it, or point XDG_CACHE_HOME "
            "elsewhere."
        )
    else:
        # Vetting covers cache.db and its -wal/-shm files; any of them can be
        # the one refused, and the cause names which.
        hint = (
            f"Fix the permissions or ownership of {path.parent} and the files in "
            f"it, or delete {path} and any {path.name}-wal or {path.name}-shm "
            "beside it by hand."
        )
    return ErrorInfo(
        "cache_error",
        f"Cache error at {path}: {action}{reason}",
        f"{hint} --fresh skips the cache.",
        path=str(path),
    )


def classify(exc: Exception, context: str | None = None) -> ErrorInfo:
    """Map an exception to an ErrorInfo. `context` names the thing that
    failed, e.g. "Release @r847868", and is used in not-found messages."""
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
                "Price data requires seller settings. "
                f"Discogs said: {sanitize.summary(message)}",
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
        # str(exc) is "<status>: <message>"; show the status once, and the
        # body-derived message only as a bounded, control-safe summary.
        detail = sanitize.summary(str(exc.args[0])) if exc.args else ""
        status = exc.status_code
        return ErrorInfo(
            "api_error",
            f"API error ({status}): {detail}" if detail else f"API error ({status}).",
            status=status,
        )

    if isinstance(exc, DiscogsConnectionError):
        return ErrorInfo(
            "connection_error", "Connection error.", "Check your network and retry."
        )

    if isinstance(exc, CacheMissError):
        split = urlsplit(exc.url)
        where = split.path + (f"?{split.query}" if split.query else "")
        return ErrorInfo(
            "cache_miss",
            f"Not cached: {exc.method} {where}",
            "Rerun without --cached to fetch it from the API.",
        )

    if isinstance(exc, CacheError):
        return _cache_error(exc)

    # UnicodeError is a ValueError too, but an encoding failure says nothing
    # about the caller's arguments.
    if isinstance(exc, ValueError) and not isinstance(exc, UnicodeError):
        return ErrorInfo("invalid_argument", str(exc))

    return ErrorInfo("unexpected", f"Unexpected error: {_describe(exc)}")


def format_error(exc: Exception, context: str | None = None) -> str:
    """Text rendering: `✗ message`, hint indented on the next line. Both are
    neutralized: a message can quote a ref, a URL, or other outside text."""
    info = classify(exc, context)
    text = f"✗ {sanitize.line(info.message)}"
    if info.hint:
        text += f"\n  {sanitize.line(info.hint)}"
    return text


def error_document(exc: Exception, context: str | None = None) -> dict[str, Any]:
    """`{"code": ..., "message": ..., "hint": ...}` with empty fields omitted, so
    agents can test `error.code` and read `error.retry_after` without null
    checks on every field."""
    return {k: v for k, v in asdict(classify(exc, context)).items() if v is not None}


def format_error_json(exc: Exception, context: str | None = None) -> str:
    """JSON rendering: `{"error": {"code": ..., "message": ..., "hint": ...}}`."""
    return dumps({"error": error_document(exc, context)})


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
