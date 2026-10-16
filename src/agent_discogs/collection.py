"""The authenticated user's collection: its records, which releases they
own, how many copies, in which folders.

`--owned` on `get versions` and `get releases` reads each row's own count
(`user_stat()`, as `--wanted` reads its want count): Discogs sends it on
those listings. On `search` and `get inventory`, whose rows carry no
reliable one, it reads the whole "All"
folder once per invocation through the filtered-scan machinery
(`fetch_filtered_page`, keeping every row), so `--max-requests` and a 429
stop it the way they stop any scan, and matches list rows against it. `get
owned` asks about one release at a time, and answers a master from that
same read, matching each record's master id. `get collection` lists the
same folder, or with `--folder` another one (`records()`), a page at a time
or, with a filter (`record_filter()`), through a scan; with `--fields` it
names each copy's notes and custom fields (`custom_fields()`,
`field_values()`).
"""

from __future__ import annotations

import functools
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import click
from discogs_sdk import RateLimitError

from agent_discogs import account, trace
from agent_discogs.inventory import is_release_row, short_grade
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
ALL_FOLDER = 0

# ctx.meta keys: one collection read and one folder list per invocation, even
# for several refs under --fresh.
_OWNERSHIP = "agent_discogs.ownership"
_FOLDER_NAMES = "agent_discogs.folder_names"
_CUSTOM_FIELDS = "agent_discogs.custom_fields"

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


def records(
    user: Any,
    *,
    folder: int = ALL_FOLDER,
    sort: str | None = None,
    desc: bool = False,
) -> Listing:
    """The records of one of the user's folders, by default the "All"
    folder, every record, in `sort` order (Discogs' own without one). `get
    collection` and the `--owned` read both list the "All" folder here, so
    without `--sort` they send the same requests and share their cache
    entries."""
    return functools.partial(
        user.collection.folders.get(folder).releases.list,
        **sort_params(sort, desc),
    )


def folder_id(names: dict[int, str], name: str) -> int:
    """The id of the folder `name` names in `names` (the folder list), any
    case. No folder, or more than one, by that name raises `ValueError`
    naming the folders."""
    wanted = name.casefold()
    found = [(i, folder) for i, folder in names.items() if folder.casefold() == wanted]
    if not found:
        listed = ", ".join(names.values())
        raise ValueError(
            f'--folder "{name}" matches no folder. Your folders: {listed}.'
        )
    if len(found) > 1:
        both = ", ".join(f"{folder} (id {i})" for i, folder in found)
        raise ValueError(f'--folder "{name}" matches more than one folder: {both}.')
    return found[0][0]


def matcher(pattern: str) -> Callable[[str], bool]:
    """Whether a value matches `pattern`, as every `get collection` filter
    matches: the whole value, ignoring case (`str.casefold`), with `*` for
    any run of characters (none included). `\\` makes the next character
    literal (`\\*`, `\\\\`); a trailing lone `\\` is a backslash. Nothing
    else is special: `?`, `[` and regular-expression characters are
    literal."""
    pieces: list[str] = []
    literal: list[str] = []
    chars = iter(pattern)
    for char in chars:
        if char == "\\":
            literal.append(next(chars, "\\"))
        elif char == "*":
            pieces.append("".join(literal))
            literal = []
        else:
            literal.append(char)
    pieces.append("".join(literal))
    compiled = re.compile(
        ".*".join(re.escape(piece.casefold()) for piece in pieces), re.DOTALL
    )
    return lambda value: compiled.fullmatch(value.casefold()) is not None


def _record_values(record: Any) -> dict[str, list[str]]:
    """What each `get collection` filter matches on a record: its format
    names and descriptions, genres, styles, label names, year (as decimal
    text), credited artist names and title."""
    release = getattr(record, "basic_information", None)
    formats = getattr(release, "formats", None) or []
    year = getattr(release, "year", None)
    title = getattr(release, "title", None)
    values = {
        "format": [
            value for f in formats for value in (f.name, *(f.descriptions or []))
        ],
        "genre": getattr(release, "genres", None) or [],
        "style": getattr(release, "styles", None) or [],
        "label": [label.name for label in getattr(release, "labels", None) or []],
        "year": [str(year)] if year else [],
        "artist": [a.name for a in getattr(release, "artists", None) or []],
        "title": [title] if title else [],
    }
    return {key: [v for v in found if v] for key, found in values.items()}


def record_filter(filters: dict[str, str]) -> Callable[[Any], bool] | None:
    """The `keep` predicate of a filtered `get collection`, or None without a
    filter. `filters` maps `format`, `genre`, `style`, `label`, `year`,
    `artist` or `title` to the pattern asked for; a record is kept when, for
    every filter, one of its values matches it (`matcher()`)."""
    wanted = {key: matcher(pattern) for key, pattern in filters.items()}
    if not wanted:
        return None

    def keep(record: Any) -> bool:
        values = _record_values(record)
        return all(any(map(match, values[key])) for key, match in wanted.items())

    return keep


@dataclass(frozen=True)
class Ownership:
    """Copies per release id in the part of the collection read, the records
    read (in collection order), and why the read stopped short (`stop`), if
    it did."""

    counts: Counter[int]
    records_read: int
    total_records: int | None
    stop: ScanStop | None
    records: tuple[Any, ...] = ()

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


def _member(value: Any, name: str) -> Any:
    """`value.name`, or `value[name]` for a dict: the SDK models a version
    row's `stats`, but an artist or label row keeps the dict Discogs sent."""
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def user_stat(row: Any, name: str) -> int | None:
    """The authenticated user's own `stats.user.<name>` on a list row
    (`in_collection`, `in_wantlist`), which `get versions` and `get releases`
    rows carry; None when the row has no such integer (no user, or Discogs
    sent none)."""
    value = _member(_member(_member(row, "stats"), "user"), name)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _row_id(row: Any) -> int | None:
    """The release a list row is: the row itself, so its own `id`. An
    inventory row is a listing, whose release is `inventory.release_id()`."""
    return row.id


def mark_rows_with(
    document: dict[str, Any], items: list[Any], key: str, value: Callable[[Any], Any]
) -> None:
    """Give each release row of a page document (`results`, projected or
    `--full`, in `items` order) `key`: `value` of its item."""
    for row, item in zip(document["results"], items, strict=True):
        if is_release_row(item):
            row[key] = value(item)


def mark_rows(
    document: dict[str, Any],
    items: list[Any],
    ownership: Ownership,
    *,
    release_id: Callable[[Any], int | None] = _row_id,
) -> None:
    """Give each release row of a page document the `owned` count of its
    release (by `release_id`) in the collection read, null when undecided,
    and the document its `collection` object."""
    mark_rows_with(
        document, items, "owned", lambda item: ownership.owned(release_id(item))
    )
    document["collection"] = ownership.document()


def stat_copies(row: Any) -> int | None:
    """The user's copies of a `get versions` or `get releases` row's release,
    as the row's own stats count them; None when it carries no count."""
    return user_stat(row, "in_collection")


def stat_wanted(row: Any) -> bool | None:
    """Whether a `get versions` or `get releases` row's release is on the
    user's wantlist, as the row's own stats count it (`--wanted`); None when
    it carries no count."""
    count = user_stat(row, "in_wantlist")
    return None if count is None else count > 0


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


def _collection_read(client: Discogs) -> Ownership:
    """The collection `get owned @m...` answers from: the same read as
    `--owned`, once per invocation, uncapped (`get owned` takes no
    `--max-requests`)."""
    meta = click.get_current_context().meta
    if _OWNERSHIP not in meta:
        meta[_OWNERSHIP] = _read_ownership(client, None, max_requests=None)
    return meta[_OWNERSHIP]


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
        tuple(result.items),
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


def _folders_of(names: dict[int, str], items: list[Any]) -> list[str]:
    """The names of the folders the copies `items` are in, each once, in the
    order the copies list them."""
    found: list[str] = []
    for item in items:
        name = folder_name(names, item.folder_id)
        if name is not None and name not in found:
            found.append(name)
    return found


@dataclass(frozen=True)
class CustomField:
    """One of the user's collection fields: the `name` a row prints it under
    and whether it is a dropdown, whose grade values print as short codes."""

    name: str
    dropdown: bool


def custom_fields(user: Any) -> dict[int, CustomField]:
    """The user's notes and custom fields by id, in the order Discogs shows
    them (`position`, then id; a field with no position last): one request
    (`GET .../collection/fields`), at most once per invocation. A name
    another field also prints under becomes `Name (field N)`, so every key
    of a row is unique."""
    meta = click.get_current_context().meta
    if _CUSTOM_FIELDS not in meta:
        listed = sorted(
            user.collection.fields.list(),
            key=lambda f: (f.position is None, f.position or 0, f.id),
        )
        keys = {f.id: f.name for f in listed}
        # Suffixing a shared name can produce another field's own name
        # (`Box (field 7)`), so repeat until no two keys clash. No two
        # suffixed keys can clash (each ends in its own id), so every pass
        # suffixes a plain name, and it ends.
        while True:
            counts = Counter(keys.values())
            clashing = [f for f in listed if counts[keys[f.id]] > 1]
            if not clashing:
                break
            for f in clashing:
                keys[f.id] = f"{f.name} (field {f.id})"
        meta[_CUSTOM_FIELDS] = {
            f.id: CustomField(keys[f.id], f.type == "dropdown") for f in listed
        }
    return meta[_CUSTOM_FIELDS]


def field_values(record: Any, fields: dict[int, CustomField]) -> dict[str, str]:
    """A copy's non-empty notes by field name, in the fields' order: a grade
    in a dropdown as its short code (`VG+`), anything else as sent. A note
    whose field the list lacks comes last, as `field N` (`field N (field N)`
    when a field of the list is named `field N`); one with no field id is
    dropped."""
    sent: dict[int, str] = {}
    for note in getattr(record, "notes", None) or []:
        if note.field_id is not None and note.value:
            sent.setdefault(note.field_id, note.value)
    values: dict[str, str] = {}
    for field_id, field in fields.items():
        if field_id in sent:
            value = sent[field_id]
            values[field.name] = (
                (short_grade(value) or value) if field.dropdown else value
            )
    names = {field.name for field in fields.values()}
    for field_id in sorted(sent.keys() - fields.keys()):
        key = f"field {field_id}"
        while key in names:
            key = f"{key} (field {field_id})"
        values[key] = sent[field_id]
    return values


def copies(client: Discogs, release_id: int, *, folders: bool) -> Copies:
    """One request for the release's instances, plus, with `folders` and at
    least one copy, the folder list (once per invocation) to name them."""
    user = account.user(client)
    page = fetch_page(
        user.collection.releases.get(release_id).list, page=1, per_page=MAX_LIMIT
    )
    names: list[str] = []
    if folders and page.items:
        names = _folders_of(folder_names(user), page.items)
    # fetch_page always reports a total; it counts copies past the first 100.
    return Copies(page.total_items or len(page.items), page.items, names)


@dataclass(frozen=True)
class MasterCopies:
    """The user's copies of a master's releases in the part of the collection
    read: the matching `records` in collection order, `releases` as (release
    id, copies, folder names), ordered by each release's first copy, and the
    `ownership` read they come from."""

    records: list[Any]
    releases: list[tuple[int, int, list[str]]]
    ownership: Ownership

    @property
    def owned(self) -> int | None:
        """The copies found: 0 (not owned) only once the whole collection was
        read, None when the part read holds none of them."""
        if self.records or self.ownership.complete:
            return len(self.records)
        return None


def _master_id(record: Any) -> int | None:
    release = getattr(record, "basic_information", None)
    return getattr(release, "master_id", None)


def master_copies(client: Discogs, master_id: int, *, folders: bool) -> MasterCopies:
    """The user's copies of any release of master `master_id`, from the
    collection read once per invocation (a record with no master, `None` or
    0, matches none), plus, with `folders` and at least one copy, the folder
    list (once per invocation) to name their folders. A 429 on the folder
    list keeps the answer, its folders named by id (`folder 5`): like a 429
    in the read, it never fails a master ref, and the list is not asked for
    again in this invocation (every later ref names folders by id too)."""
    ownership = _collection_read(client)
    found = [r for r in ownership.records if master_id and _master_id(r) == master_id]
    by_release: dict[int, list[Any]] = {}
    for record in found:
        by_release.setdefault(record.id, []).append(record)
    names: dict[int, str] = {}
    if folders and found:
        try:
            names = folder_names(account.user(client))
        except RateLimitError:
            click.get_current_context().meta[_FOLDER_NAMES] = names
    releases = [
        (release_id, len(items), _folders_of(names, items) if folders else [])
        for release_id, items in by_release.items()
    ]
    return MasterCopies(found, releases, ownership)
