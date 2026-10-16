"""Which of the user's wants are for sale: the `get wantlist --for-sale`
filter, one Marketplace stats lookup per want. `Market` and `market_of()`
are a release's Marketplace numbers, as `get stats` shows them too.

The Discogs API lists no copies for sale per release, only these stats: how
many copies are for sale and the lowest asking price across every condition
and location. So `--for-sale` can filter on count and price, nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from discogs_sdk import CacheMissError

if TYPE_CHECKING:
    from discogs_sdk import CurrencyCode, Discogs, Price


@dataclass(frozen=True)
class Market:
    """A release's Marketplace stats: copies for sale and the lowest asking
    price, in the `--currency` asked for (else the account's)."""

    for_sale: int
    lowest: Price | None


def market_of(stats: Any) -> Market:
    """The Marketplace numbers of a release's stats record, which Discogs
    sends as `null` when nothing is for sale: a `null` count is 0, and a
    release blocked from sale (`blocked_from_sale`, with `null` counts too)
    reads as no copies. Reading a field of the lazy proxy sends its one
    request."""
    if stats.blocked_from_sale:
        return Market(0, None)
    return Market(stats.num_for_sale or 0, stats.lowest_price)


class ForSale:
    """The `keep` predicate of the `--for-sale` scan: one stats request per
    want, cached like any request.

    A want is kept when it has copies for sale, is not blocked from sale,
    and, with `max_price`, its lowest price is known and at most that. Its
    stats are kept in `markets`. A `--cached` miss keeps the want too, as
    undecided, with its error in `misses`: it is reported, never dropped as
    not for sale. Any other failure propagates; a 429 stops the scan.
    """

    def __init__(
        self,
        client: Discogs,
        *,
        max_price: float | None,
        currency: CurrencyCode | None,
    ) -> None:
        self._client = client
        self._max_price = max_price
        self._currency = currency
        self.markets: dict[int, Market] = {}
        self.misses: dict[int, CacheMissError] = {}

    def __call__(self, want: Any) -> bool:
        stats = self._client.releases.get(want.id).marketplace_stats.get(
            curr_abbr=self._currency
        )
        try:
            # A lazy proxy: reading a field sends the one request.
            for_sale = stats.num_for_sale or 0
        except CacheMissError as exc:
            self.misses[want.id] = exc
            return True
        if stats.blocked_from_sale or not for_sale:
            return False
        lowest = stats.lowest_price
        if self._max_price is not None and (
            lowest is None or lowest.value > self._max_price
        ):
            return False
        self.markets[want.id] = Market(for_sale, lowest)
        return True
