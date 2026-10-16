"""Which of the user's wants are for sale: the `get wantlist --for-sale`
filter, one Marketplace stats lookup per want. `Market` and `market_of()`
are a release's Marketplace numbers, as `get stats` shows them too, and
`read_markets()` looks them up for each row of a `get versions --market` page.

The Discogs API lists no copies for sale per release, only these stats: how
many copies are for sale and the lowest asking price across every condition
and location. So `--for-sale` can filter on count and price, nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from discogs_sdk import CacheMissError, RateLimitError

from agent_discogs import trace
from agent_discogs.pagination import ScanStop, rate_limited_stop

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


@dataclass
class MarketRead:
    """The `--market` lookups of one page of `rows` release rows: each row's
    `Market` by release id, the ids blocked from sale, the `--cached` misses
    (a row read, its error reported in place of its numbers) and, when the
    lookups stopped short, why. The rows after the stop are not checked;
    `unsent` is a read that never started, the listing having been rate
    limited first."""

    rows: int
    markets: dict[int, Market] = field(default_factory=dict)
    blocked: set[int] = field(default_factory=set)
    misses: dict[int, CacheMissError] = field(default_factory=dict)
    stop: ScanStop | None = None
    unsent: bool = False

    @property
    def rows_read(self) -> int:
        return len(self.markets) + len(self.misses)

    def document(self) -> dict[str, Any]:
        """The JSON `market_read` object: how many rows were checked, and
        why not all."""
        doc: dict[str, Any] = {
            "complete": self.stop is None,
            "rows_read": self.rows_read,
            "rows": self.rows,
        }
        if self.stop is not None:
            doc["stop_reason"] = self.stop.reason
            if self.stop.retry_after is not None:
                doc["retry_after"] = self.stop.retry_after
        return doc


def read_markets(
    client: Discogs,
    rows: list[Any],
    *,
    currency: CurrencyCode | None,
    max_requests: int | None,
    listing_stop: ScanStop | None,
) -> MarketRead:
    """One `/marketplace/stats/{id}` lookup per row, in row order, cached
    like any request. A lookup is sent only while the command's sends are
    below `max_requests` (cache hits are free), and a 429 stops the lookups
    with the rows read so far; any other failure propagates. After a
    listing scan stopped by a 429 (`listing_stop`) nothing is asked."""
    if listing_stop is not None and listing_stop.reason == "rate_limited":
        return MarketRead(len(rows), stop=listing_stop, unsent=True)
    read = MarketRead(len(rows))
    for row in rows:
        if max_requests is not None and trace.sends() >= max_requests:
            read.stop = ScanStop("max_requests", max_requests=max_requests)
            break
        stats = client.releases.get(row.id).marketplace_stats.get(curr_abbr=currency)
        try:
            market = market_of(stats)
        except CacheMissError as exc:
            read.misses[row.id] = exc
            continue
        except RateLimitError as exc:
            read.stop = rate_limited_stop(exc)
            break
        read.markets[row.id] = market
        if stats.blocked_from_sale:
            read.blocked.add(row.id)
    return read


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
