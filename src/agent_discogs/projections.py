"""SDK models → the JSON an agent actually needs.

Each text view in `formatting.py` has a sibling projector here that returns
exactly the fields that view shows, plus refs and, for an entity Discogs
gives a page (`uri`), its web link as `url`. `--json` serializes these;
`--json --full` bypasses them for the SDK's complete, normalized record
(`json_output.full_dump()`). A full release is ~33 KB of image URLs and
bookkeeping; its projection is ~1.5 KB.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from pydantic import TypeAdapter

from agent_discogs import markup
from agent_discogs.collection import (
    CustomField,
    Duplicate,
    field_values,
    folder_name,
    folders_of,
)
from agent_discogs.formatting import (
    _community_counts,
    _format_string,
    _ref_type,
    _urls_short,
    artist_title,
    credits_by_role,
    field,
    known_year,
    listing_title,
    release_date,
    split_members,
    track_summary,
)
from agent_discogs.inventory import release_id, short_grade
from agent_discogs.refs import make_ref

if TYPE_CHECKING:
    from agent_discogs.wantlist import Market


def drop_empty(value: Any) -> Any:
    """Recursively drop None/""/[]/{} so consumers never null-check; 0/False stay."""
    if isinstance(value, dict):
        cleaned = {k: drop_empty(v) for k, v in value.items()}
        return {k: v for k, v in cleaned.items() if v not in (None, "", [], {})}
    if isinstance(value, list):
        return [drop_empty(v) for v in value]
    return value


_DATETIME: TypeAdapter[datetime] = TypeAdapter(datetime)


def _timestamp(value: datetime | None) -> str | None:
    """RFC 3339, exactly as `--full` (`model_dump(mode="json")`) serializes it."""
    return _DATETIME.dump_python(value, mode="json") if value else None


_SITE_URL = "https://www.discogs.com"


def _url(entity: Any, attr: str = "uri") -> str | None:
    """The entity's Discogs web page: its `uri` (or the field `attr` names),
    always absolute.

    The SDK makes a search result's site-relative `uri` absolute; a list row
    keeps an unmodeled `uri` as sent, so a relative one is completed the same
    way here.
    """
    uri = getattr(entity, attr, None)
    if isinstance(uri, str) and uri.startswith("/"):
        return _SITE_URL + uri
    return uri


def _artist_refs(artists: list[Any] | None) -> list[dict[str, Any]]:
    return [
        {
            "ref": make_ref("artist", a.id) if getattr(a, "id", None) else None,
            "name": getattr(a, "name", None),
            "join": getattr(a, "join", None),
        }
        for a in artists or []
    ]


def _tracks(tracklist: list[Any] | None) -> list[dict[str, Any]]:
    """Tracklist rows; an index track (suite, medley) nests its `sub_tracks`."""
    return [
        {
            "pos": getattr(t, "position", None),
            "title": getattr(t, "title", None),
            "duration": getattr(t, "duration", None),
            "type": getattr(t, "type", None)
            if getattr(t, "type", None) in ("heading", "index")
            else None,
            "artists": _artist_refs(getattr(t, "artists", None)),
            "sub_tracks": _tracks(getattr(t, "sub_tracks", None)),
        }
        for t in tracklist or []
    ]


def project_search_result(r: Any) -> dict[str, Any]:
    entity_type = _ref_type(r)
    if entity_type is None:
        # What the text row shows: no ref, since nothing says what the ID is.
        return drop_empty(
            {"id": r.id, "type": r.type, "title": r.title, "url": _url(r)}
        )
    label = getattr(r, "label", None)
    master_id = getattr(r, "master_id", None)
    return drop_empty(
        {
            "ref": make_ref(entity_type, r.id),
            "url": _url(r),
            "type": entity_type,
            "title": r.title,
            "year": known_year(getattr(r, "year", None)),
            "country": getattr(r, "country", None),
            "label": label[0] if label else None,
            "catno": getattr(r, "catalog_number", None),
            "format": getattr(r, "format", None),
            **_community_counts(r),
            "master": make_ref("master", master_id)
            if entity_type == "release" and master_id
            else None,
        }
    )


def project_added_release(rel: Any) -> dict[str, Any]:
    """A `search --added-since` row: a looked-up release, titled like a search
    result (`Artist - Title`), with `formats` as `get release` gives them and
    `added`, its full RFC 3339 timestamp."""
    labels = getattr(rel, "labels", None) or []
    first = labels[0] if labels else None
    master_id = getattr(rel, "master_id", None)
    return drop_empty(
        {
            "ref": make_ref("release", rel.id),
            "url": _url(rel),
            "title": artist_title(rel),
            "year": known_year(getattr(rel, "year", None)),
            "country": getattr(rel, "country", None),
            "label": getattr(first, "name", None),
            "catno": getattr(first, "catalog_number", None),
            "formats": _format_string(getattr(rel, "formats", None)),
            "added": _timestamp(getattr(rel, "date_added", None)),
            "master": make_ref("master", master_id) if master_id else None,
        }
    )


def project_release(
    rel: Any,
    *,
    verbose: bool = False,
    compact: bool = False,
    master_year: int | str | None = None,
    master_year_error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The `get release` view. Under `--master-year`, `master_year` (the
    master's year) or `master_year_error` (why it was not read) follow `master`."""
    community = getattr(rel, "community", None)
    master_id = getattr(rel, "master_id", None)
    tracklist = getattr(rel, "tracklist", None) or []
    data = {
        "ref": make_ref("release", rel.id),
        "url": _url(rel),
        "title": rel.title,
        "artists": _artist_refs(getattr(rel, "artists", None)),
        "year": known_year(getattr(rel, "year", None)),
        "released": release_date(getattr(rel, "released", None)),
        "added": _timestamp(getattr(rel, "date_added", None)),
        "country": getattr(rel, "country", None),
        "labels": [
            {
                "ref": make_ref("label", lb.id) if getattr(lb, "id", None) else None,
                "name": getattr(lb, "name", None),
                "catno": getattr(lb, "catalog_number", None),
            }
            for lb in getattr(rel, "labels", None) or []
        ],
        "formats": _format_string(getattr(rel, "formats", None)),
        "genres": getattr(rel, "genres", None),
        "styles": getattr(rel, "styles", None),
        "master": make_ref("master", master_id) if master_id else None,
        "master_year": master_year,
        "master_year_error": master_year_error,
        "community": _community(community),
        "market": {
            "for_sale": getattr(rel, "num_for_sale", None),
            "lowest": getattr(rel, "lowest_price", None),
        },
    }
    if compact and tracklist:
        # Same one-line summary the text view prints as `Tracks:`.
        data["tracks"] = track_summary(tracklist)
    elif not compact:
        data["tracklist"] = _tracks(tracklist)
    if verbose:
        # Same facets the text view adds under -v: notes, credits, identifiers.
        data["notes"] = markup.plain(markup.parse(getattr(rel, "notes", None) or ""))
        data["credits"] = project_credits(rel)
        data["identifiers"] = project_identifiers(rel)
    return drop_empty(data)


def _community(community: Any) -> dict[str, Any]:
    """A release's `community` object: `have`, `want`, the rating and its
    vote count."""
    rating = getattr(community, "rating", None)
    return {
        "have": getattr(community, "have", None),
        "want": getattr(community, "want", None),
        "rating": getattr(rating, "average", None),
        "votes": getattr(rating, "count", None),
    }


def project_market(market: Market, *, blocked: bool = False) -> dict[str, Any]:
    """The `market` object of a release's Marketplace numbers: `for_sale`
    (0 included), then `lowest` and its `currency` when Discogs priced it,
    and `"blocked": true` only for a release blocked from sale."""
    lowest = market.lowest
    return drop_empty(
        {
            "for_sale": market.for_sale,
            "lowest": getattr(lowest, "value", None),
            "currency": getattr(lowest, "currency", None),
            "blocked": True if blocked else None,
        }
    )


def project_stats(rel: Any, market: Market, *, blocked: bool) -> dict[str, Any]:
    """The `get stats` view: the release, named as `get release` names it,
    its `community` object (no `rating` when nobody voted, as the text shows
    none) and its `market` object."""
    community = _community(getattr(rel, "community", None))
    if not community["votes"]:
        community["rating"] = None
    return drop_empty(
        {
            "ref": make_ref("release", rel.id),
            "title": rel.title,
            "artists": _artist_refs(getattr(rel, "artists", None)),
            "year": known_year(getattr(rel, "year", None)),
            "community": community,
            "market": project_market(market, blocked=blocked),
        }
    )


def project_credits(rel: Any) -> dict[str, list[dict[str, Any]]]:
    """`{role: [{ref, name, tracks}]}`, grouped exactly like the text view."""
    return drop_empty(
        {
            role: [
                {
                    "ref": make_ref("artist", c.id) if c.id else None,
                    "name": c.name,
                    "tracks": ", ".join(c.tracks),
                }
                for c in entries
            ]
            for role, entries in credits_by_role(rel).items()
        }
    )


def project_identifiers(rel: Any) -> list[dict[str, Any]]:
    return drop_empty(
        [
            {
                "type": getattr(i, "type", None),
                "value": getattr(i, "value", None),
                "description": getattr(i, "description", None),
            }
            for i in getattr(rel, "identifiers", None) or []
        ]
    )


def project_tracklist(rel: Any) -> dict[str, Any]:
    return drop_empty(
        {
            "ref": make_ref("release", rel.id),
            "title": getattr(rel, "title", None),
            "tracklist": _tracks(getattr(rel, "tracklist", None)),
        }
    )


def project_price(release: Any, suggestions: Any, stats: Any) -> dict[str, Any]:
    conditions: Any = getattr(suggestions, "conditions", None) or {}
    lowest = getattr(stats, "lowest_price", None)
    return drop_empty(
        {
            "ref": make_ref("release", release.id),
            "title": getattr(release, "title", None),
            # Each suggestion carries its own currency, like Discogs's Price:
            # the API takes no currency parameter for suggestions.
            "suggestions": {
                cond: {
                    "value": getattr(price, "value", None),
                    "currency": getattr(price, "currency", None),
                }
                for cond, price in conditions.items()
                if getattr(price, "value", None) is not None
            },
            "market": {
                "for_sale": getattr(stats, "num_for_sale", None),
                "lowest": getattr(lowest, "value", None),
                "currency": getattr(lowest, "currency", None),
            },
        }
    )


def _refs_of(items: list[Any], entity_type: str) -> list[dict[str, Any]]:
    """`[{ref, name}]` for members, sub-labels, and a parent label."""
    return [
        {
            "ref": make_ref(entity_type, field(i, "id")) if field(i, "id") else None,
            "name": field(i, "name"),
        }
        for i in items
    ]


def project_artist(artist: Any) -> dict[str, Any]:
    """Text parity: `members` (current or unknown) and `former`, each with refs."""
    current, former = split_members(artist)
    return drop_empty(
        {
            "ref": make_ref("artist", artist.id),
            "url": _url(artist),
            "name": artist.name,
            "profile": markup.plain(markup.profile(getattr(artist, "profile", None))),
            "urls": _urls_short(getattr(artist, "urls", None)),
            "members": _refs_of(current, "artist"),
            "former": _refs_of(former, "artist"),
        }
    )


def project_label(label: Any) -> dict[str, Any]:
    parent = getattr(label, "parent_label", None)
    return drop_empty(
        {
            "ref": make_ref("label", label.id),
            "url": _url(label),
            "name": label.name,
            "profile": markup.plain(markup.profile(getattr(label, "profile", None))),
            "urls": _urls_short(getattr(label, "urls", None)),
            "parent": _refs_of([parent], "label")[0] if parent else None,
            "sub_labels": _refs_of(getattr(label, "sub_labels", None) or [], "label"),
        }
    )


def project_label_release(rel: Any) -> dict[str, Any]:
    return drop_empty(
        {
            "ref": make_ref("release", rel.id),
            "url": _url(rel),
            "title": rel.title,
            "artist": getattr(rel, "artist", None),
            "year": known_year(getattr(rel, "year", None)),
            "catno": getattr(rel, "catalog_number", None),
            "format": getattr(rel, "format", None),
            **_community_counts(rel),
        }
    )


def project_master(master: Any) -> dict[str, Any]:
    main = getattr(master, "main_release", None)
    return drop_empty(
        {
            "ref": make_ref("master", master.id),
            "url": _url(master),
            "title": master.title,
            "artists": _artist_refs(getattr(master, "artists", None)),
            "year": known_year(getattr(master, "year", None)),
            "genres": getattr(master, "genres", None),
            "styles": getattr(master, "styles", None),
            "main_release": make_ref("release", main) if main else None,
            "market": {
                "for_sale": getattr(master, "num_for_sale", None),
                "lowest": getattr(master, "lowest_price", None),
            },
            "tracklist": _tracks(getattr(master, "tracklist", None)),
        }
    )


def project_master_version(ver: Any) -> dict[str, Any]:
    return drop_empty(
        {
            "ref": make_ref("release", ver.id),
            "url": _url(ver),
            "title": getattr(ver, "title", None),
            "released": release_date(getattr(ver, "released", None)),
            "country": getattr(ver, "country", None),
            "label": getattr(ver, "label", None),
            "catno": getattr(ver, "catalog_number", None),
            "format": getattr(ver, "format", None),
            **_community_counts(ver),
        }
    )


def project_artist_release(rel: Any) -> dict[str, Any]:
    entity_type = getattr(rel, "type", "release")
    return drop_empty(
        {
            "ref": make_ref(entity_type, rel.id),
            "url": _url(rel),
            "type": entity_type,
            "title": rel.title,
            "year": known_year(getattr(rel, "year", None)),
            "role": getattr(rel, "role", None),
            "label": getattr(rel, "label", None),
            "format": getattr(rel, "format", None),
            **_community_counts(rel),
        }
    )


def project_want(want: Any) -> dict[str, Any]:
    """A `get wantlist` row: the wanted release, titled like a search result,
    with the want's own `notes` and `rating` (Discogs' 0 is unrated)."""
    release = getattr(want, "basic_information", None)
    labels = getattr(release, "labels", None) or []
    first = labels[0] if labels else None
    return drop_empty(
        {
            "ref": make_ref("release", want.id),
            "title": artist_title(release),
            "year": known_year(getattr(release, "year", None)),
            "label": getattr(first, "name", None),
            "catno": getattr(first, "catalog_number", None),
            "format": _format_string(getattr(release, "formats", None)),
            "notes": getattr(want, "notes", None),
            "rating": getattr(want, "rating", None) or None,
        }
    )


def _description(entity: Any) -> str:
    """A list's description with its markup converted, kept whole."""
    return markup.plain(markup.parse(getattr(entity, "description", None) or ""))


def project_list_summary(summary: Any) -> dict[str, Any]:
    """A `get lists` row: what the text row shows, plus the description, the
    timestamp the list was added and its page. `public` is kept when false."""
    return drop_empty(
        {
            "id": summary.id,
            "name": summary.name,
            "description": _description(summary),
            "public": summary.public,
            "added": _timestamp(summary.date_added),
            "changed": _timestamp(summary.date_changed),
            "url": _url(summary),
        }
    )


def _list_item(item: Any) -> dict[str, Any]:
    """One list item: its ref, or for a type with no ref prefix its id and
    type, as a search row; its comment as Discogs sent it."""
    entity_type = _ref_type(item)
    if entity_type is None:
        head = {"id": item.id, "type": item.type}
    else:
        head = {"ref": make_ref(entity_type, item.id)}
    return drop_empty({**head, "title": item.display_title, "comment": item.comment})


def project_list(found: Any) -> dict[str, Any]:
    """`get list`: the list and every item in it."""
    return drop_empty(
        {
            "id": found.id,
            "name": found.name,
            "description": _description(found),
            "public": found.public,
            # Discogs sends the list's page as `uri` (unmodeled); its docs
            # show `url`, which the SDK models.
            "url": _url(found) or _url(found, "url"),
            "items": [_list_item(item) for item in found.items or []],
        }
    )


def _credits(artists: list[Any] | None) -> list[dict[str, Any]]:
    """A record's artists in credit order, each its ref and name as far as
    known; a credit with neither is dropped. No `join`: the row's title
    carries the joins."""
    known = (
        drop_empty({"ref": a["ref"], "name": a["name"]}) for a in _artist_refs(artists)
    )
    return [credit for credit in known if credit]


def project_record(
    record: Any,
    folders: dict[int, str],
    fields: dict[int, CustomField] | None = None,
) -> dict[str, Any]:
    """A `get collection` row: one copy, its release titled like a search
    result, with the copy's folder by name (`folders`), `added`, its full
    RFC 3339 timestamp, `rating` (Discogs' 0 is unrated) and, with the
    user's custom `fields` (`--fields`), its notes by field name.

    Unlike the text row, it also carries the release's artists, genres,
    styles and master (Discogs' master 0 is none), from the same response."""
    release = getattr(record, "basic_information", None)
    labels = getattr(release, "labels", None) or []
    first = labels[0] if labels else None
    master_id = getattr(release, "master_id", None)
    return drop_empty(
        {
            "ref": make_ref("release", record.id),
            "title": artist_title(release),
            "artists": _credits(getattr(release, "artists", None)),
            "year": known_year(getattr(release, "year", None)),
            "label": getattr(first, "name", None),
            "catno": getattr(first, "catalog_number", None),
            "format": _format_string(getattr(release, "formats", None)),
            "genres": getattr(release, "genres", None),
            "styles": getattr(release, "styles", None),
            "master": make_ref("master", master_id) if master_id else None,
            "folder": folder_name(folders, getattr(record, "folder_id", None)),
            "added": _timestamp(getattr(record, "date_added", None)),
            "rating": getattr(record, "rating", None) or None,
            "fields": None if fields is None else field_values(record, fields),
        }
    )


def _project_copies(
    copies: list[Any], folders: dict[int, str], fields: dict[int, CustomField] | None
) -> dict[str, Any]:
    """A `--duplicates` release: the `get collection` row of its first copy
    without the copy's own `folder`, `added` and `rating`, then how many
    `copies` and the `folders` they are in."""
    row = project_record(copies[0], folders, fields)
    for key in ("folder", "added", "rating"):
        row.pop(key, None)
    return drop_empty(
        {**row, "copies": len(copies), "folders": folders_of(folders, copies)}
    )


def project_duplicate(
    group: Duplicate,
    kind: str,
    folders: dict[int, str],
    fields: dict[int, CustomField] | None,
) -> dict[str, Any]:
    """A `--duplicates` group: a release as its copies' entry, or a master
    as its ref, its copies and an entry per release."""
    if kind == "release":
        return _project_copies(group.records, folders, fields)
    return {
        "ref": make_ref("master", group.id),
        "copies": len(group.records),
        "releases": [_project_copies(c, folders, fields) for c in group.releases],
    }


def project_listing(listing: Any) -> dict[str, Any]:
    """A `get inventory` row: the listing's own id (`listing`), the release
    it is a copy of, the grades as short codes, the price with its currency,
    and `listed`, its RFC 3339 timestamp."""
    rid = release_id(listing)
    price = listing.price
    return drop_empty(
        {
            "listing": listing.id,
            "ref": None if rid is None else make_ref("release", rid),
            "title": listing_title(listing.release),
            "format": getattr(listing.release, "format", None),
            "condition": short_grade(listing.condition),
            "sleeve": short_grade(listing.sleeve_condition),
            "price": getattr(price, "value", None),
            "currency": getattr(price, "currency", None),
            "ships_from": listing.ships_from,
            "listed": _timestamp(listing.posted),
            "url": _url(listing),
        }
    )
