"""SDK exceptions → recovery-oriented errors, as text or a JSON envelope."""

from __future__ import annotations

import json
import math
import sys
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any, NoReturn
from urllib.parse import urlsplit

import pydantic
from discogs_sdk import (
    AuthenticationError,
    CacheError,
    CacheMissError,
    DiscogsAPIError,
    DiscogsConnectionError,
    DiscogsError,
    ForbiddenError,
    NotFoundError,
    RateLimitError,
)

from agent_discogs import sanitize, style
from agent_discogs.account import UserRequiredError
from agent_discogs.client import auth_mode
from agent_discogs.json_output import dumps

if TYPE_CHECKING:
    from discogs_sdk import AuthMode, RateLimit


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


def _is_bad_response(exc: Exception) -> bool:
    """A 2xx response the SDK could not read: a body that is not JSON (or not
    text at all), JSON its models reject, or a malformed page envelope, which
    the SDK reports as a bare `DiscogsError`. Its subclasses mean other things."""
    return type(exc) is DiscogsError or isinstance(
        exc, (json.JSONDecodeError, UnicodeDecodeError, pydantic.ValidationError)
    )


def _bad_response_detail(exc: Exception) -> str:
    """What was wrong with the response. A validation error names its first
    failing field, never the value Discogs sent, and counts the rest."""
    if not isinstance(exc, pydantic.ValidationError):
        return str(exc)
    first, *rest = exc.errors(include_url=False, include_input=False)
    where = ".".join([exc.title, *map(str, first["loc"])])
    more = f" (and {len(rest)} more)" if rest else ""
    return f"{where}: {first['msg']}{more}"


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
    """A cache the SDK could not create, vet, open or purge. The CLI deletes
    nothing: the hint tells the user what to fix, or which file to remove by
    hand."""
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
    sidecars = f"any {path.name}-wal or {path.name}-shm beside it"
    if exc.operation == "clear the cache":
        # The purge ran on an open database: it is locked, read-only or damaged,
        # and --fresh would not purge anything.
        return ErrorInfo(
            "cache_error",
            f"Cache error at {path}: {action}{reason}",
            "Retry once no other process is using the cache. If it keeps "
            f"failing, fix the permissions of {path.parent} and the files in it, "
            f"or delete {path} and {sidecars} by hand.",
            path=str(path),
        )
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
            f"it, or delete {path} and {sidecars} by hand."
        )
    return ErrorInfo(
        "cache_error",
        f"Cache error at {path}: {action}{reason}",
        f"{hint} --fresh skips the cache.",
        path=str(path),
    )


_SEE_STATUS = "agent-discogs status shows the credentials in use."

# What a 401 says about each mode the SDK can authenticate with: the
# credentials it sent, and what to check about them.
_REJECTED: dict[AuthMode, tuple[str, str]] = {
    "token": (
        "DISCOGS_TOKEN",
        "Check the token, or generate a new one at discogs.com/settings/developers.",
    ),
    "consumer": (
        "the consumer key/secret (DISCOGS_CONSUMER_KEY, DISCOGS_CONSUMER_SECRET)",
        (
            "Check both at discogs.com/settings/developers. They identify an app, "
            "not a user: price suggestions need DISCOGS_TOKEN or OAuth."
        ),
    ),
    "oauth": (
        (
            "OAuth (DISCOGS_CONSUMER_KEY, DISCOGS_CONSUMER_SECRET, "
            "DISCOGS_ACCESS_TOKEN, DISCOGS_ACCESS_TOKEN_SECRET)"
        ),
        "Check all four; the access token may have been revoked.",
    ),
}


def _auth_failed() -> ErrorInfo:
    """A 401, explained in terms of the credentials the client sent. The mode
    comes from the same environment the client resolved, so it is the one
    `status` reports."""
    mode = auth_mode()
    if mode == "none":
        return ErrorInfo(
            "auth_required",
            "Authentication failed: no credentials are set.",
            "Set token: export DISCOGS_TOKEN=<token> "
            f"(discogs.com/settings/developers). {_SEE_STATUS}",
            status=401,
        )
    sent, check = _REJECTED[mode]
    return ErrorInfo(
        "auth_required",
        f"Authentication failed with {sent}.",
        f"{check} {_SEE_STATUS}",
        status=401,
    )


def _rate_budget(rl: RateLimit | None) -> str:
    """The per-minute limit that applies. Unauthenticated, setting a token is
    the fix; with credentials, it would change nothing, so the hint states
    the limit Discogs reported (or the authenticated 60 when it sent none)."""
    mode = auth_mode()
    if mode == "none":
        return "60 req/min with DISCOGS_TOKEN, 25 without."
    if rl is None:
        return f"Discogs allows 60 req/min for the credentials in use ({mode})."
    return f"Discogs reported {rl.limit} req/min for the credentials in use ({mode})."


def _user_required(exc: UserRequiredError) -> ErrorInfo:
    """A command that reads the user's own data, run with credentials that
    name no user. Caught before any request, so there is no status."""
    sent = (
        "no credentials are set"
        if exc.mode == "none"
        else "a consumer key/secret identifies an app, not a user"
    )
    return ErrorInfo(
        "auth_required",
        f"{exc.feature} reads your own Discogs account, but {sent}.",
        "Set a token: export DISCOGS_TOKEN=<token> "
        "(discogs.com/settings/developers), or use OAuth (DISCOGS_CONSUMER_KEY, "
        "DISCOGS_CONSUMER_SECRET, DISCOGS_ACCESS_TOKEN, "
        f"DISCOGS_ACCESS_TOKEN_SECRET). {_SEE_STATUS}",
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
        return _auth_failed()

    if isinstance(exc, ForbiddenError):
        return ErrorInfo(
            "forbidden",
            "Access forbidden. This endpoint may require different permissions.",
            status=403,
        )

    if isinstance(exc, RateLimitError):
        # The SDK parses Retry-After (seconds or an HTTP date). Round a fraction
        # up, so the hint never suggests retrying before the server asked; 0 is
        # a real answer, not a missing one.
        seconds = exc.retry_after_seconds
        retry_after = None if seconds is None else math.ceil(seconds)
        wait = (
            "Wait a moment and retry."
            if retry_after is None
            else f"Retry in {retry_after}s."
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
            f"{wait} {_rate_budget(rl)}",
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
        # The SDK's message is the transport error's; `transient` says whether
        # its own retry policy would try again, so advice needs no httpx2.
        detail = sanitize.summary(str(exc.args[0])) if exc.args else ""
        return ErrorInfo(
            "connection_error",
            f"Connection error: {detail}" if detail else "Connection error.",
            "Check your network and retry."
            if exc.transient
            else "Retrying will not help: check your proxy, protocol, or network "
            "configuration.",
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

    if _is_bad_response(exc):
        # Never the caller's input. The SDK stores no response that fails to
        # parse, so a retry normally asks Discogs again. A price guide is the
        # exception: its conditions are validated only when read, after the
        # body is cached, so only --fresh gets past a bad one.
        detail = sanitize.summary(_bad_response_detail(exc))
        return ErrorInfo(
            "bad_response",
            f"Discogs sent a response that cannot be read: {detail}",
            "Not caused by your arguments. Retry in a moment, or with --fresh "
            "if the same error repeats.",
        )

    if isinstance(exc, UserRequiredError):
        return _user_required(exc)

    # UnicodeError is a ValueError too, but an encoding failure says nothing
    # about the caller's arguments.
    if isinstance(exc, ValueError) and not isinstance(exc, UnicodeError):
        return ErrorInfo("invalid_argument", str(exc))

    return ErrorInfo("unexpected", f"Unexpected error: {_describe(exc)}")


def format_error(
    exc: Exception, context: str | None = None, *, color: bool = False
) -> str:
    """Text rendering: `✗ message`, hint indented on the next line. Both are
    neutralized: a message can quote a ref, a URL, or other outside text.
    `color` paints the `✗` only; the message can quote Discogs text."""
    info = classify(exc, context)
    mark = style.paint("✗", "error", color=color)
    text = f"{mark} {sanitize.line(info.message)}"
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
        print(
            format_error(exc, context, color=style.enabled(sys.stderr)), file=sys.stderr
        )
    sys.exit(1)
