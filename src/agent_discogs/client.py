"""The SDK client of one invocation and the invocation's cache scope."""

from __future__ import annotations

import contextlib
from typing import Literal

import click
from discogs_sdk import AuthMode, Discogs

from agent_discogs import trace
from agent_discogs.config import CACHE_TTL, cache_dir

CacheScope = Literal["cache_only", "no_cache"]

# ctx.meta keys (shared by every context of one invocation): the scope that
# --cached / --fresh asked for and the first get_client() call enters, and the
# client that call builds.
_PENDING_SCOPE = "agent_discogs.pending_cache_scope"
_CLIENT = "agent_discogs.client"


def request_cache_scope(ctx: click.Context, scope: CacheScope) -> None:
    """Wrap the rest of this invocation in `scope` once a client exists.

    The group callback runs before the subcommand validates its flags, so it
    must not build the client itself: a rejected flag would build one anyway.
    """
    ctx.meta[_PENDING_SCOPE] = scope


def _enter_pending_scope(ctx: click.Context, discogs: Discogs) -> None:
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


def _build() -> Discogs:
    # The CLI never retries or sleeps on the caller's behalf: a 429, a 5xx or
    # a transport failure is reported at once, so one request is one send and
    # the footer counts what was spent. Credentials come from the
    # environment, resolved by the SDK as in auth_mode().
    return Discogs(
        cache=True,
        cache_dir=cache_dir(),
        cache_ttl=CACHE_TTL,
        max_retries=0,
        on_request=trace.record,
    )


def get_client() -> Discogs:
    """The invocation's Discogs SDK client, built on the first call, entering
    any pending cache scope.

    The client lives in `ctx.meta`, so the next invocation in the same process
    builds its own from its own environment; `close_client()` closes it. Needs
    a click context: outside one, click raises `RuntimeError`. Neither step
    touches the disk: the SDK opens its SQLite cache at the first request that
    needs it, so a cache it cannot open raises `CacheError` inside the
    command's error boundary, and a `--fresh` command never opens it.
    """
    ctx = click.get_current_context()
    discogs: Discogs | None = ctx.meta.get(_CLIENT)
    if discogs is None:
        discogs = ctx.meta[_CLIENT] = _build()
    _enter_pending_scope(ctx, discogs)
    return discogs


def close_client(ctx: click.Context) -> None:
    """Close the client `get_client()` built in `ctx`'s invocation, if any.

    The group callback registers it with `ctx.call_on_close()` before
    `trace.begin()` registers `finish()`, so it runs after the footer and the
    `--debug` panel: closing checkpoints the SQLite WAL and removes the
    `-wal`/`-shm` files, which would change the cache size the panel prints.
    A failure to close is ignored, so it never changes the invocation's exit
    code or output.
    """
    discogs: Discogs | None = ctx.meta.pop(_CLIENT, None)
    if discogs is not None:
        with contextlib.suppress(Exception):
            discogs.close()
