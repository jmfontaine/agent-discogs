"""Reading fields off Discogs rows: the pure helpers the text views
(`formatting`), the JSON projectors (`projections`), `collection` and
`inventory` share.

Nothing here sends a request, touches click or styles text, and the module
imports nothing from the package but `refs`, so every module that reads rows
sits above it without an import cycle.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, TypedDict

from agent_discogs.refs import TYPE_TO_PREFIX


def field(obj: Any, name: str) -> Any:
    """Attribute or dict key: the SDK leaves untyped API fields as raw dicts."""
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def count(value: object) -> int | None:
    """A count Discogs sent as an integer; anything else (a raw dict may hold
    any JSON, `true` included) is not shown."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


class _Community(TypedDict):
    """A row's community counts, as `_release_row` and the projectors take them."""

    have: int | None
    want: int | None


def community_counts(row: Any) -> _Community:
    """Discogs' community counts on a list row, `{"have": N, "want": N}`, each
    None when not sent: a search result's `community.have`/`want`, or the
    `stats.community.in_collection`/`in_wantlist` of a version or of an
    artist or label release row (modeled on versions, a raw dict on the
    others, whose stats the SDK does not model). No request is sent."""
    community = field(row, "community")
    if community is not None:
        have, want = field(community, "have"), field(community, "want")
    else:
        community = field(field(row, "stats"), "community")
        have = field(community, "in_collection")
        want = field(community, "in_wantlist")
    return {"have": count(have), "want": count(want)}


def is_release_row(row: Any) -> bool:
    """Whether a list row is a release, the only thing a collection or a
    wantlist holds. Search and artist discography rows carry a `type`; version
    and label catalogue rows have none and are all releases."""
    return getattr(row, "type", "release") == "release"


def ref_type(entity: Any) -> str | None:
    """The entity type of a search result or list item if it has a ref
    prefix, else None.

    Discogs may send any type string, or none. Only a known type says which
    kind of ID `entity.id` is, so anything else gets no ref.
    """
    return entity.type if entity.type in TYPE_TO_PREFIX else None


def known_year(year: int | str | None) -> int | str | None:
    """The year, or None for Discogs' `0` / `"0"` meaning unknown."""
    return year if year and str(year).strip("0") else None


_UNKNOWN_DATE_PARTS = re.compile(r"(?:-00)+$")


def release_date(released: str | None) -> str | None:
    """`1994-05-00` → `1994-05`, `1994-00-00` → `1994`; None for an unknown year.

    Discogs pads unknown month and day with `-00`; shown as-is they read as a
    date, and a bare year would not match the release year it repeats.
    """
    if not released:
        return None
    date = _UNKNOWN_DATE_PARTS.sub("", released)
    return date if known_year(date.split("-", 1)[0]) else None


def format_string(formats: list[Any] | None) -> str:
    """One entry per format, `;`-separated: `2xVinyl (LP, Album, 180g); CD (Album)`.

    A quantity above 1 prefixes the name, joined by a multiplication sign (`x`
    above). Descriptions and the free text (`180g, Gatefold`) go in
    parentheses, so entries never blur together.
    """
    entries = []
    for f in formats or []:
        name = getattr(f, "name", None) or ""
        quantity = str(getattr(f, "quantity", None) or "")
        if name and quantity not in ("", "0", "1"):
            name = f"{quantity}\u00d7{name}"  # multiplication sign
        details = list(getattr(f, "descriptions", None) or [])
        text = getattr(f, "text", None)
        if text:
            details.append(text)
        detail = ", ".join(details)
        if name and detail:
            entries.append(f"{name} ({detail})")
        elif name or detail:
            entries.append(name or detail)
    return "; ".join(entries)


def urls_short(urls: list[str] | None) -> str:
    """Extract domain names from URLs for compact display."""
    if not urls:
        return ""
    domains = []
    for url in urls:
        parts = url.split("//", 1)
        domain = parts[1].split("/", 1)[0] if len(parts) > 1 else parts[0]
        domain = domain.removeprefix("www.")
        if domain:  # an empty URL (or a bare "https://") names no site
            domains.append(domain)
    return ", ".join(domains)


def split_members(artist: Any) -> tuple[list[Any], list[Any]]:
    """(members, former): only `active is False` is former; unknown (None) is not."""
    members = getattr(artist, "members", None) or []
    former = [m for m in members if getattr(m, "active", None) is False]
    current = [m for m in members if getattr(m, "active", None) is not False]
    return current, former


def artist_names(release: Any) -> str:
    """A release's artist names as credited (`A, B & C`), or `""` with none.
    A comma join attaches to the name before it (`A, B`); others stand apart
    (`A & B`)."""
    names = ""
    for artist in getattr(release, "artists", None) or []:
        names += getattr(artist, "name", None) or "Unknown"
        join = (getattr(artist, "join", None) or "").strip()
        if join:
            names += join if join == "," else f" {join}"
        names += " "
    return names.rstrip()


def artist_title(release: Any) -> str | None:
    """`Artist - Title`, the way a search result titles a release, or
    whichever of the two Discogs sent."""
    names = [artist_names(release), getattr(release, "title", None)]
    return " - ".join(name for name in names if name) or None


def listing_title(release: Any) -> str | None:
    """`Artist - Title` of a listing's release, or whichever of the two
    Discogs sent."""
    names = [getattr(release, "artist", None), getattr(release, "title", None)]
    return " - ".join(name for name in names if name) or None


_ROLE_SPLIT = re.compile(r",\s*(?![^\[]*\])")  # commas outside [bracketed] notes


def iter_tracks(tracklist: list[Any] | None) -> Iterator[Any]:
    """Every track in order, each index track followed by its sub-tracks."""
    for track in tracklist or []:
        yield track
        yield from iter_tracks(getattr(track, "sub_tracks", None))


@dataclass
class Credit:
    """One person under one role; empty `tracks` means the whole release."""

    id: int | None
    name: str | None
    tracks: list[str]

    @property
    def person(self) -> int | str | None:
        """Identity for merging: the artist ID, or the name when there is none."""
        return self.id or self.name


def _scoped_credits(release: Any) -> Iterator[tuple[Any, list[str]]]:
    """Each credit with the tracks it covers: release credits first, then tracks.

    A release credit's scope is its own `tracks` string, split on commas; a
    track or sub-track credit covers that track, named by position or title.
    """
    for credit in getattr(release, "extra_artists", None) or []:
        tracks = (getattr(credit, "tracks", None) or "").split(",")
        yield credit, [t.strip() for t in tracks if t.strip()]
    for track in iter_tracks(getattr(release, "tracklist", None)):
        scope = getattr(track, "position", None) or getattr(track, "title", None)
        for credit in getattr(track, "extra_artists", None) or []:
            yield credit, [scope or "?"]


def credits_by_role(release: Any) -> dict[str, list[Credit]]:
    """Invert Discogs credits from per-person to per-role, across all tracks.

    Discogs stores one entry per person with a comma-joined role string such
    as `Producer [Production], Written-By`, on the release and on each track
    and sub-track; both the text and JSON views group by role so "who
    produced this?" is one line or one key. A person appears once per role,
    with the union of the tracks credited; a release-wide credit absorbs the
    same credit on a track. Roles sort alphabetically; people keep first-seen
    order within a role.
    """
    by_role: dict[str, dict[int | str | None, Credit]] = {}
    for source, scope in _scoped_credits(release):
        credit_id = getattr(source, "id", None)
        name = getattr(source, "name", None)
        for role in _ROLE_SPLIT.split(getattr(source, "role", None) or ""):
            people = by_role.setdefault(role.strip() or "Other", {})
            fresh = Credit(credit_id, name, list(scope))
            known = people.setdefault(fresh.person, fresh)
            if known is fresh:
                continue
            if not scope:
                known.tracks = []
            elif known.tracks:
                known.tracks += [t for t in scope if t not in known.tracks]
    return {role: list(people.values()) for role, people in sorted(by_role.items())}


def _duration_seconds(duration: str | None) -> int | None:
    """`4:30` or `1:02:03` in seconds; None for a blank or malformed duration.

    `isdigit()` also accepts digits `int()` refuses (`²`) and runs too long
    for it to convert; such a duration is malformed too.
    """
    parts = (duration or "").split(":")
    if not all(p.isdigit() for p in parts):
        return None
    seconds = 0
    try:
        for part in parts:
            seconds = seconds * 60 + int(part)
    except ValueError:
        return None
    return seconds


def _leaf_timing(tracklist: list[Any]) -> tuple[int, int | None]:
    """`(leaf track count, summed seconds)`; seconds is None if any leaf is untimed.

    Headings are skipped. An index track counts as its sub-tracks and sums
    their durations; when a sub-track is untimed, the index's own duration
    stands in for the group, since Discogs often times only the whole work.
    """
    count = 0
    total: int | None = 0
    for track in tracklist:
        if (getattr(track, "type", None) or "track") == "heading":
            continue
        own = _duration_seconds(getattr(track, "duration", None))
        sub_tracks = getattr(track, "sub_tracks", None) or []
        leaves, seconds = _leaf_timing(sub_tracks) if sub_tracks else (0, None)
        if not leaves:
            leaves, seconds = 1, own
        elif seconds is None:
            seconds = own
        count += leaves
        total = None if total is None or seconds is None else total + seconds
    return count, total


def track_summary(tracklist: list[Any]) -> str:
    """`14 (65:01)`: track count and summed duration, for compact views.

    Headings are not counted, and an index track counts as its sub-tracks
    (see `_leaf_timing`). The total is shown only when every counted track has
    a valid duration; a partial sum would read as the whole release's running
    time, so a single blank duration drops it to the bare count.
    """
    count, seconds = _leaf_timing(tracklist)
    if not count:
        return "0"
    if seconds is None:
        return str(count)
    minutes, secs = divmod(seconds, 60)
    return f"{count} ({minutes}:{secs:02d})"
