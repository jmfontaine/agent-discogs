"""The Discogs user behind the credentials: the check that there is one, and
their username. Every command that reads the user's own data (collection,
wantlist) goes through here."""

from __future__ import annotations

from typing import TYPE_CHECKING

import click

from agent_discogs.client import auth_mode

if TYPE_CHECKING:
    from discogs_sdk import AuthMode, Discogs

# ctx.meta key (shared by every context of one invocation) for the username,
# looked up once even when `--fresh` sends every request again.
_USERNAME = "agent_discogs.username"


class UserRequiredError(Exception):
    """`feature` reads the user's own data, and the credentials in use
    (`mode`, `consumer` or `none`) name no user. Raised before any request;
    classified as `auth_required`."""

    def __init__(self, feature: str, mode: AuthMode) -> None:
        super().__init__(f"{feature} needs a Discogs user ({mode} credentials)")
        self.feature = feature
        self.mode = mode


def require_user(feature: str) -> None:
    """Raise `UserRequiredError` unless the credentials belong to a user: a
    token or OAuth. Sends nothing, so it runs before any request."""
    mode = auth_mode()
    if mode not in ("token", "oauth"):
        raise UserRequiredError(feature, mode)


def username(client: Discogs) -> str:
    """The username behind the credentials (`GET /oauth/identity`, cached
    like any request), looked up at most once per invocation. A lookup that
    failed fails every later call the same way, without asking again: with
    several refs, one 429 on it answers for all of them."""
    meta = click.get_current_context().meta
    if _USERNAME not in meta:
        try:
            meta[_USERNAME] = client.user.identity().username
        except Exception as exc:
            meta[_USERNAME] = exc
            raise
    found = meta[_USERNAME]
    if isinstance(found, Exception):
        raise found
    return found
