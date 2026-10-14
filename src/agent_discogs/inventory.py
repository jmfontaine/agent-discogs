"""A seller's inventory: the Discogs grades, and the `get inventory` filters.

The inventory endpoint sends every listing's grades, price and origin, which
Discogs cannot filter server-side: `Filter` is the `keep` predicate of a
client-side scan. `--in-wantlist` needs the user's whole wantlist first
(`read_wantlist()`), so a listing is never reported as not wanted on a
partial read.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from discogs_sdk import RateLimitError

from agent_discogs import account
from agent_discogs.pagination import (
    ScanStop,
    fetch_filtered_page,
    keep_all,
    rate_limited_stop,
)

if TYPE_CHECKING:
    from discogs_sdk import CurrencyCode, Discogs

# The Discogs grades, best first: the name Discogs sends, then its short code.
# Ours, not the order of discogs-sdk's `Condition` literal, which is no
# documented contract. A sleeve can also be "Generic", "Not Graded" or "No
# Cover", which are no grade.
GRADES: dict[str, str] = {
    "Mint (M)": "M",
    "Near Mint (NM or M-)": "NM",
    "Very Good Plus (VG+)": "VG+",
    "Very Good (VG)": "VG",
    "Good Plus (G+)": "G+",
    "Good (G)": "G",
    "Fair (F)": "F",
    "Poor (P)": "P",
}

_RANKS = {name: rank for rank, name in enumerate(GRADES)}


def grade(text: str) -> str | None:
    """The grade `text` names, by its short code or its full name in any
    case, as the name Discogs sends; None when it names none."""
    wanted = text.casefold()
    return next(
        (
            name
            for name, code in GRADES.items()
            if wanted in (name.casefold(), code.casefold())
        ),
        None,
    )


def short_grade(name: str | None) -> str | None:
    """`VG+` for `Very Good Plus (VG+)`; a value that is no grade (`Generic`)
    as sent."""
    return GRADES.get(name, name) if name else None


def _at_least(name: str | None, minimum: str) -> bool:
    """Whether `name` is a grade, `minimum` or better."""
    rank = _RANKS.get(name) if name else None
    return rank is not None and rank <= _RANKS[minimum]


def release_id(listing: Any) -> int | None:
    """The id of the release a listing is a copy of, which `--owned` and
    `--in-wantlist` look up: not the listing's own `id`."""
    return getattr(listing.release, "id", None)


@dataclass(frozen=True)
class Wants:
    """The release ids on the part of the wantlist read, and why the read
    stopped short (`stop`), if it did."""

    ids: frozenset[int]
    total: int | None
    stop: ScanStop | None
    read: int

    def document(self) -> dict[str, Any]:
        """The JSON `wantlist` object: how much was read, and why not all."""
        doc: dict[str, Any] = {
            "complete": self.stop is None,
            "wants_read": self.read,
            "total_wants": self.total,
        }
        if self.stop is not None:
            doc["stop_reason"] = self.stop.reason
            if self.stop.retry_after is not None:
                doc["retry_after"] = self.stop.retry_after
        return doc


def read_wantlist(client: Discogs, *, max_requests: int | None) -> Wants:
    """The user's whole wantlist: the username, then 100 wants per request.

    `max_requests` caps the sends of the whole command, as for every scan. A
    429 keeps the wants read so far, none if it answers the username or the
    first page; either way the read is incomplete, and nothing may be judged
    against it.
    """
    try:
        user = account.user(client)
        result = fetch_filtered_page(
            user.wantlist.list, limit=None, keep=keep_all, max_requests=max_requests
        )
    except RateLimitError as exc:
        return Wants(frozenset(), None, rate_limited_stop(exc), 0)
    return Wants(
        frozenset(want.id for want in result.items),
        result.total_items,
        result.stop,
        len(result.items),
    )


class Filter:
    """The `keep` predicate of the `get inventory` scan. It sends nothing.

    A listing is kept when its media grade is `condition` or better, its
    sleeve grade `sleeve` or better (a Generic, Not Graded or No Cover sleeve
    is no grade), it ships from `ships_from` (any case), its release is in
    `wanted`, and, with `max_price` (an amount and its currency), it is
    priced in that currency at most that amount, exactly it included. A
    listing that passes every other filter but is priced in another currency
    is counted in `skipped_currency`.
    """

    def __init__(
        self,
        *,
        condition: str | None,
        sleeve: str | None,
        ships_from: str | None,
        max_price: tuple[float, CurrencyCode] | None,
        wanted: frozenset[int] | None,
    ) -> None:
        self._condition = condition
        self._sleeve = sleeve
        self._ships_from = None if ships_from is None else ships_from.casefold()
        self._max_price = max_price
        self._wanted = wanted
        self.skipped_currency: int = 0

    @property
    def active(self) -> bool:
        """Whether any filter is set, so the listing is scanned client-side."""
        return any(
            value is not None
            for value in (
                self._condition,
                self._sleeve,
                self._ships_from,
                self._max_price,
                self._wanted,
            )
        )

    def __call__(self, listing: Any) -> bool:
        if self._condition is not None and not _at_least(
            listing.condition, self._condition
        ):
            return False
        if self._sleeve is not None and not _at_least(
            listing.sleeve_condition, self._sleeve
        ):
            return False
        if (
            self._ships_from is not None
            and (listing.ships_from or "").casefold() != self._ships_from
        ):
            return False
        if self._wanted is not None and release_id(listing) not in self._wanted:
            return False
        if self._max_price is None:
            return True
        amount, currency = self._max_price
        price = listing.price
        if price is None:
            return False
        if price.currency != currency:
            self.skipped_currency += 1
            return False
        return price.value <= amount
