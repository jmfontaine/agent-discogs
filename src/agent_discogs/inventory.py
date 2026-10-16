"""A seller's inventory: the Discogs grades, and the `get inventory` filters.

The inventory endpoint sends every listing's grades, price and origin, which
Discogs cannot filter server-side: `Filter` is the `keep` predicate of a
client-side scan. `--in-wantlist` needs the user's whole wantlist first
(`read_wantlist()`), so a listing is never reported as not wanted on a
partial read; `search --wanted` marks its rows against the same read
(`wants_of()`), leaving a row the part read lacks undecided.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from discogs_sdk import RateLimitError

from agent_discogs import account, trace
from agent_discogs.collection import is_release_row
from agent_discogs.pagination import (
    PageResult,
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

    def wanted(self, release_id: int) -> bool | None:
        """Whether `release_id` is on the wantlist (`--wanted`): True when
        the part read holds it, False only after a complete read, else None
        (undecided)."""
        if release_id in self.ids:
            return True
        return False if self.stop is None else None


def _unread(stop: ScanStop) -> Wants:
    return Wants(frozenset(), None, stop, 0)


def read_wantlist(client: Discogs, *, max_requests: int | None) -> Wants:
    """The user's whole wantlist: the username, then 100 wants per request.

    `max_requests` caps the sends of the whole command, as for every scan. A
    429 keeps the wants read so far, none if it answers the username or the
    first page; either way the read is incomplete, and nothing may be judged
    absent from it.
    """
    try:
        user = account.user(client)
        result = fetch_filtered_page(
            user.wantlist.list, limit=None, keep=keep_all, max_requests=max_requests
        )
    except RateLimitError as exc:
        return _unread(rate_limited_stop(exc))
    return Wants(
        frozenset(want.id for want in result.items),
        result.total_items,
        result.stop,
        len(result.items),
    )


def wants_of(
    client: Discogs,
    listing: PageResult,
    *,
    earlier: tuple[ScanStop | None, ...],
    max_requests: int | None,
) -> Wants | None:
    """The wantlist to mark the `listing` page's release rows with
    (`search --wanted`), or None, without a request, when the page has no
    release row.

    `earlier` holds the stops of the reads this command made before (the
    listing, the `--owned` collection read): after a 429 nothing is asked,
    and with nothing left of the cap not even the username, which
    `read_wantlist()` alone would send before checking it.
    """
    if not any(is_release_row(item) for item in listing.items):
        return None
    for stop in earlier:
        if stop is not None and stop.reason == "rate_limited":
            return _unread(stop)
    if max_requests is not None and trace.sends() >= max_requests:
        return _unread(ScanStop("max_requests", max_requests=max_requests))
    return read_wantlist(client, max_requests=max_requests)


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
