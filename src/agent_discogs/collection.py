"""The authenticated user's collection: which releases they own, how many
copies, in which folders.

`--owned` reads the whole "All" folder once per invocation through the
filtered-scan machinery (`fetch_filtered_page`, keeping every row), so
`--max-requests` and a 429 stop it the way they stop any scan, and matches
list rows against it. `get owned` asks about one release at a time.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import click
from discogs_sdk import RateLimitError

from agent_discogs import account, trace
from agent_discogs.pagination import (
    MAX_LIMIT,
    PageResult,
    ScanStop,
    fetch_filtered_page,
    fetch_page,
    rate_limited_stop,
)

if TYPE_CHECKING:
    from discogs_sdk import Discogs

# The folder every collection has, holding every record of the others.
_ALL_FOLDER = 0

# ctx.meta keys: one collection read and one folder list per invocation, even
# for several refs under --fresh.
_OWNERSHIP = "agent_discogs.ownership"
_FOLDER_NAMES = "agent_discogs.folder_names"


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

    def owned(self, row: Any) -> int | None:
        """Copies of the release `row` is: 0 (not owned) only once the whole
        collection was read, None when the part read does not hold it. A
        copy found in a partial read counts, though more may be unread."""
        copies = self.counts[row.id]
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


def mark_rows(document: dict[str, Any], items: list[Any], ownership: Ownership) -> None:
    """Give each release row of a page document (`results`, projected or
    `--full`, in `items` order) its `owned` count, null when undecided, and
    the document its `collection` object."""
    for row, item in zip(document["results"], items, strict=True):
        if is_release_row(item):
            row["owned"] = ownership.owned(item)
    document["collection"] = ownership.document()


def _every(_item: Any) -> bool:
    return True


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
        user = client.users.get(account.username(client))
        result = fetch_filtered_page(
            user.collection.folders.get(_ALL_FOLDER).releases.list,
            limit=None,
            keep=_every,
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


def _folder_names(user: Any) -> dict[int, str]:
    meta = click.get_current_context().meta
    if _FOLDER_NAMES not in meta:
        meta[_FOLDER_NAMES] = {f.id: f.name for f in user.collection.folders.list()}
    return meta[_FOLDER_NAMES]


def copies(client: Discogs, release_id: int, *, folders: bool) -> Copies:
    """One request for the release's instances, plus, with `folders` and at
    least one copy, the folder list (once per invocation) to name them."""
    user = client.users.get(account.username(client))
    page = fetch_page(
        user.collection.releases.get(release_id).list, page=1, per_page=MAX_LIMIT
    )
    names: list[str] = []
    if folders and page.items:
        by_id = _folder_names(user)
        for item in page.items:
            if item.folder_id is None:
                continue
            name = by_id.get(item.folder_id, f"folder {item.folder_id}")
            if name not in names:
                names.append(name)
    # fetch_page always reports a total; it counts copies past the first 100.
    return Copies(page.total_items or len(page.items), page.items, names)
