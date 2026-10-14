"""The authenticated user's collection: its records, which releases they
own, how many copies, in which folders.

`--owned` reads the whole "All" folder once per invocation through the
filtered-scan machinery (`fetch_filtered_page`, keeping every row), so
`--max-requests` and a 429 stop it the way they stop any scan, and matches
list rows against it. `get owned` asks about one release at a time. `get
collection` lists the same folder (`records()`), a page at a time or, with
a filter (`record_filter()`), through a scan.
"""

from __future__ import annotations

import functools
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import click
from discogs_sdk import RateLimitError

from agent_discogs import account, trace
from agent_discogs.pagination import (
    MAX_LIMIT,
    Listing,
    PageResult,
    ScanStop,
    fetch_filtered_page,
    fetch_page,
    keep_all,
    rate_limited_stop,
    sort_params,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from discogs_sdk import Discogs

# The folder every collection has, holding every record of the others.
_ALL_FOLDER = 0

# ctx.meta keys: one collection read and one folder list per invocation, even
# for several refs under --fresh.
_OWNERSHIP = "agent_discogs.ownership"
_FOLDER_NAMES = "agent_discogs.folder_names"

# The collection value's amounts, in the order every view gives them.
_AMOUNTS = ("minimum", "median", "maximum")


@dataclass(frozen=True)
class Valuation:
    """The user's collection value: Discogs' record (the SDK's lazy
    `CollectionValue`, fetched at its first read) and the ISO code of the
    user's currency, which the record's formatted amounts are in."""

    currency: str
    record: Any

    def amounts(self) -> dict[str, float]:
        """Each amount as a number, parsed out of the string Discogs sent
        (`"€1.250,00"`); one it cannot read raises `UnknownFormatError`."""
        return {
            name: account.amount(
                getattr(self.record, name), f"the collection value's {name}"
            )
            for name in _AMOUNTS
        }


def valuation(client: Discogs) -> Valuation:
    """The user's collection value and currency: the username, the profile
    (`GET /users/{username}`, for its currency code), then the value (`GET
    .../collection/value`, sent when the record is first read)."""
    user = account.user(client)
    return Valuation(account.currency(user), user.collection.value.get())


def records(user: Any, *, sort: str | None = None, desc: bool = False) -> Listing:
    """Every record of the user's collection: the "All" folder, in `sort`
    order (Discogs' own without one). `get collection` and the `--owned`
    read both list it here, so without `--sort` they send the same requests
    and share their cache entries."""
    return functools.partial(
        user.collection.folders.get(_ALL_FOLDER).releases.list,
        **sort_params(sort, desc),
    )


def _record_values(record: Any) -> dict[str, set[str]]:
    """What each `get collection` filter matches on a record, casefolded: its
    format names and descriptions, genres, styles, label names and year."""
    release = getattr(record, "basic_information", None)
    formats = getattr(release, "formats", None) or []
    year = getattr(release, "year", None)
    values = {
        "format": [
            value for f in formats for value in (f.name, *(f.descriptions or []))
        ],
        "genre": getattr(release, "genres", None) or [],
        "style": getattr(release, "styles", None) or [],
        "label": [label.name for label in getattr(release, "labels", None) or []],
        "year": [str(year)] if year else [],
    }
    return {key: {v.casefold() for v in found if v} for key, found in values.items()}


def record_filter(filters: dict[str, str]) -> Callable[[Any], bool] | None:
    """The `keep` predicate of a filtered `get collection`, or None without a
    filter. `filters` maps `format`, `genre`, `style`, `label` or `year` to
    the value asked for; a record is kept when, for every filter, one of its
    values equals it, ignoring case."""
    wanted = {key: value.casefold() for key, value in filters.items()}
    if not wanted:
        return None

    def keep(record: Any) -> bool:
        values = _record_values(record)
        return all(value in values[key] for key, value in wanted.items())

    return keep


@dataclass(frozen=True)
class Ownership:
    """Copies per release id in the part of the collection read, and why the
    read stopped short (`stop`), if it did."""

    counts: Counter[int]
    records_read: int
    total_records: int | None
    stop: ScanStop | None

    @property
    def complete(self) -> bool:
        return self.stop is None

    def owned(self, release_id: int | None) -> int | None:
        """Copies of the release `release_id`: 0 (not owned) only once the
        whole collection was read, None when the part read does not hold it,
        or for no release id. A copy found in a partial read counts, though
        more may be unread."""
        if release_id is None:
            return None
        copies = self.counts[release_id]
        return copies if copies or self.complete else None

    def document(self) -> dict[str, Any]:
        """The JSON `collection` object: how much was read, and why not all."""
        doc: dict[str, Any] = {
            "complete": self.complete,
            "records_read": self.records_read,
            "total_records": self.total_records,
        }
        if self.stop is not None:
            doc["stop_reason"] = self.stop.reason
            if self.stop.retry_after is not None:
                doc["retry_after"] = self.stop.retry_after
        return doc


def is_release_row(row: Any) -> bool:
    """Whether a list row is a release, the only thing a collection holds.
    Search and artist discography rows carry a `type`; version and label
    catalogue rows have none and are all releases."""
    return getattr(row, "type", "release") == "release"


def _row_id(row: Any) -> int | None:
    """The release a list row is: the row itself, so its own `id`. An
    inventory row is a listing, whose release is `inventory.release_id()`."""
    return row.id


def mark_rows(
    document: dict[str, Any],
    items: list[Any],
    ownership: Ownership,
    *,
    release_id: Callable[[Any], int | None] = _row_id,
) -> None:
    """Give each release row of a page document (`results`, projected or
    `--full`, in `items` order) the `owned` count of its release (by
    `release_id`), null when undecided, and the document its `collection`
    object."""
    for row, item in zip(document["results"], items, strict=True):
        if is_release_row(item):
            row["owned"] = ownership.owned(release_id(item))
    document["collection"] = ownership.document()


def ownership_of(
    client: Discogs, listing: PageResult, *, max_requests: int | None
) -> Ownership | None:
    """The ownership to mark the `listing` page with, read once per
    invocation, or None, without a request, when the page has no release
    row: there is nothing to mark."""
    if not any(is_release_row(item) for item in listing.items):
        return None
    meta = click.get_current_context().meta
    if _OWNERSHIP not in meta:
        meta[_OWNERSHIP] = _read_ownership(
            client, listing.stop, max_requests=max_requests
        )
    return meta[_OWNERSHIP]


def _unread(stop: ScanStop) -> Ownership:
    return Ownership(Counter(), 0, None, stop)


def _read_ownership(
    client: Discogs, listing_stop: ScanStop | None, *, max_requests: int | None
) -> Ownership:
    """Read the user's collection: the username, then the "All" folder at
    100 records per request.

    `max_requests` caps the sends of the whole command, as for every scan:
    with nothing left of it, not even the username is asked for. A 429 keeps
    the records read so far, none if it answers the username or the first
    page; after a listing scan stopped by one, nothing is asked at all.
    """
    if listing_stop is not None and listing_stop.reason == "rate_limited":
        return _unread(listing_stop)
    if max_requests is not None and trace.sends() >= max_requests:
        return _unread(ScanStop("max_requests", max_requests=max_requests))
    try:
        user = account.user(client)
        result = fetch_filtered_page(
            records(user),
            limit=None,
            keep=keep_all,
            max_requests=max_requests,
        )
    except RateLimitError as exc:
        return _unread(rate_limited_stop(exc))
    return Ownership(
        Counter(item.id for item in result.items),
        len(result.items),
        result.total_items,
        result.stop,
    )


@dataclass(frozen=True)
class Copies:
    """The user's copies of one release: `count` across the collection, the
    `instances` read (Discogs' first 100), and their folders by name, in the
    order the instances list them."""

    count: int
    instances: list[Any]
    folders: list[str]


def folder_names(user: Any) -> dict[int, str]:
    """The user's folder names by id: one request (`GET .../collection/
    folders`), at most once per invocation."""
    meta = click.get_current_context().meta
    if _FOLDER_NAMES not in meta:
        meta[_FOLDER_NAMES] = {f.id: f.name for f in user.collection.folders.list()}
    return meta[_FOLDER_NAMES]


def folder_name(names: dict[int, str], folder_id: int | None) -> str | None:
    """The name of the folder a copy is in, `folder 123` for one the folder
    list does not name, or None when the copy names no folder."""
    if folder_id is None:
        return None
    return names.get(folder_id, f"folder {folder_id}")


def copies(client: Discogs, release_id: int, *, folders: bool) -> Copies:
    """One request for the release's instances, plus, with `folders` and at
    least one copy, the folder list (once per invocation) to name them."""
    user = account.user(client)
    page = fetch_page(
        user.collection.releases.get(release_id).list, page=1, per_page=MAX_LIMIT
    )
    names: list[str] = []
    if folders and page.items:
        by_id = folder_names(user)
        for item in page.items:
            name = folder_name(by_id, item.folder_id)
            if name is not None and name not in names:
                names.append(name)
    # fetch_page always reports a total; it counts copies past the first 100.
    return Copies(page.total_items or len(page.items), page.items, names)
