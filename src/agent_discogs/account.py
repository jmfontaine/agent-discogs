"""The Discogs user behind the credentials: the check that there is one,
their username, and their profile's currency. Every command that reads the
user's own data (collection, lists, wantlist) goes through here.

Discogs writes the collection value as formatted strings (`"$250.00"`), so
`amount()` reads the number out of one, and `currency()` names the ISO code
it is in: the CLI never prints a currency symbol, which cannot tell USD from
CAD or AUD."""

from __future__ import annotations

import math
import re
from typing import TYPE_CHECKING, Any

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


class UnknownFormatError(Exception):
    """Discogs sent `field` in a shape this CLI cannot read: an amount it
    cannot parse, or a profile without a currency code. Never the caller's
    input, so it is no `ValueError` (`invalid_argument`); `errors.classify()`
    maps it to `bad_response`. The message names the field, never the value."""

    def __init__(self, field: str, problem: str) -> None:
        super().__init__(f"{field} {problem}")
        self.field = field


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


def user(client: Discogs) -> Any:
    """The SDK's handle on the user behind the credentials, for their
    collection, lists and wantlist: the username lookup, nothing more."""
    return client.users.get(username(client))


_CURRENCY_CODE = re.compile(r"[A-Z]{3}")
# The field an unreadable currency code is reported under.
CURRENCY_FIELD = "currency_code"


def currency(user: Any) -> str:
    """The ISO code of the user's currency, `curr_abbr` on their profile:
    reading it sends the profile request (`GET /users/{username}`)."""
    code = user.currency_code
    if not isinstance(code, str) or not _CURRENCY_CODE.fullmatch(code):
        raise UnknownFormatError(
            CURRENCY_FIELD, "on the profile is not a currency code (curr_abbr)"
        )
    return code


# Thousands separators: dot, comma, apostrophe (also typographic) and the
# spaces, no-break and narrow no-break included. A dot or comma may be the
# decimal separator.
_SEPARATORS = ".,'\u2019 \u00a0\u202f"
_DIGITS_TO_DIGITS = re.compile(r"[0-9](?:.*[0-9])?", re.DOTALL)
_SEPARATOR = re.compile(f"[{re.escape(_SEPARATORS)}]")
_DIGITS = re.compile(r"[0-9]+")
# Around the number: a sign or accounting parentheses would be dropped with
# the symbol, so a negative amount would read as positive.
_SIGNS = re.compile("[-\u2212()]")
_GROUP = 3
_DECIMALS = 2


def amount(text: str | None, field: str) -> float:
    """The number in a formatted amount: `$250.00` → 250.0, `€1.250,00` →
    1250.0, `1 250,00 kr` → 1250.0, `CHF 1'250.00` → 1250.0, `¥1,250` →
    1250.0.

    What comes before the first digit and after the last is the currency
    symbol, and is dropped. Between them, the decimal separator is the last
    `.` or `,` when one or two digits follow it to the end; every other `.`,
    `,`, `'`, space or no-break space separates thousands, so it must cut
    the whole part into groups of three after a first group of one to three
    digits that is not 0. Anything else raises `UnknownFormatError` naming
    `field`: a sign or parentheses around the number, a `.` or `,` right
    before its first digit (`.50` is not 50), or more digits than a float
    holds.
    """
    found = _DIGITS_TO_DIGITS.search(text) if isinstance(text, str) else None
    if found is None:
        raise UnknownFormatError(field, "holds no amount")
    unknown = UnknownFormatError(field, "is not an amount in a known format")
    symbol = found.string[: found.start()] + found.string[found.end() :]
    if _SIGNS.search(symbol) or found.string[: found.start()].endswith((".", ",")):
        raise unknown
    number = found.group()
    whole, fraction = number, ""
    point = max(number.rfind("."), number.rfind(","))
    decimals = number[point + 1 :]
    if point >= 0 and _DIGITS.fullmatch(decimals) and len(decimals) <= _DECIMALS:
        whole, fraction = number[:point], decimals
    groups = _SEPARATOR.split(whole)
    if not all(_DIGITS.fullmatch(group) for group in groups) or (
        len(groups) > 1
        and (
            len(groups[0]) > _GROUP
            or groups[0].startswith("0")
            or any(len(g) != _GROUP for g in groups[1:])
        )
    ):
        raise unknown
    value = float("".join(groups) + (f".{fraction}" if fraction else ""))
    if not math.isfinite(value):
        raise unknown
    return value
