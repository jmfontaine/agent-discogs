"""SDK client initialization and the per-invocation cache scope."""

from __future__ import annotations

from typing import Literal

import click
from discogs_sdk import AuthMode, Discogs

from agent_discogs import trace
from agent_discogs.config import CACHE_TTL, cache_dir

_client: Discogs | None = None

CacheScope = Literal["cache_only", "no_cache"]

# ctx.meta key (shared by every context of one invocation) for the scope that
# --cached / --fresh asked for and the first get_client() call enters.
_PENDING_SCOPE = "agent_discogs.pending_cache_scope"


def request_cache_scope(ctx: click.Context, scope: CacheScope) -> None:
    """Wrap the rest of this invocation in `scope` once a client exists.

    The group callback runs before the subcommand validates its flags, so it
    must not build the client itself: a rejected flag would build one anyway.
    """
    ctx.meta[_PENDING_SCOPE] = scope


def _enter_pending_scope(discogs: Discogs) -> None:
    ctx = click.get_current_context(silent=True)
    if ctx is None:
        return
    scope: CacheScope | None = ctx.meta.pop(_PENDING_SCOPE, None)
    if scope == "cache_only":
        ctx.find_root().with_resource(discogs.cache_only())
    elif scope == "no_cache":
        ctx.find_root().with_resource(discogs.no_cache())


def auth_mode() -> AuthMode:
    """The auth mode the SDK selects from the `DISCOGS_*` environment
    variables, the same one `get_client()` would use.

    Read from a throwaway client built without a cache, so it sends nothing
    and never opens or creates the cache dir.
    """
    with Discogs() as client:
        return client.auth_mode


def get_client() -> Discogs:
    """Get or create the Discogs SDK client, entering any pending cache scope.

    Neither step touches the disk: the SDK opens its SQLite cache at the first
    request that needs it, so a cache it cannot open raises `CacheError` inside
    the command's error boundary, and a `--fresh` command never opens it.
    """
    global _client  # noqa: PLW0603  # module-level singleton is the point
    if _client is None:
        # The CLI never retries or sleeps on the caller's behalf: a 429, a
        # 5xx or a transport failure is reported at once, so one request is
        # one send and the footer counts what was spent. Credentials come
        # from the environment, resolved by the SDK as in auth_mode().
        _client = Discogs(
            cache=True,
            cache_dir=cache_dir(),
            cache_ttl=CACHE_TTL,
            max_retries=0,
            on_request=trace.record,
        )
    _enter_pending_scope(_client)
    return _client
