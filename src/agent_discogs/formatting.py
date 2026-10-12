"""SDK Pydantic models → compact text output."""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agent_discogs import sanitize
from agent_discogs.config import CACHE_TTL_LABEL
from agent_discogs.refs import TYPE_TO_PREFIX, make_ref

if TYPE_CHECKING:
    from discogs_sdk import AuthMode

    from agent_discogs.pagination import ScanStop


def _text(lines: list[str]) -> str:
    """Join a view's lines. Each line is neutralized here, so a line break or
    escape sequence inside a Discogs field cannot leave its line."""
    return "\n".join(sanitize.line(text) for text in lines)


def _scan_note(stop: ScanStop) -> str:
    """Why a client-side scan returned early, for the page counts."""
    if stop.reason == "rate_limited":
        wait = "" if stop.retry_after is None else f", retry in {stop.retry_after}s"
        return f"scan stopped: rate limited{wait}"
    return f"scan stopped at --max-requests {stop.max_requests}"


def _page_counts(
    page: int,
    shown: int,
    total: int | None,
    noun: str,
    *,
    filtered: bool,
    stop: ScanStop | None = None,
) -> str:
    """`(page 2, 5 of ≤23 results; scan stopped at --max-requests 3)`.

    `≤` marks a client-side filtered list whose total is the unfiltered API
    count; a scan note means the scan stopped early and the page may be
    short. With no total, the scan read no listing page before
    `--max-requests` ran out, so no page, count or bound is known yet.
    """
    if total is None:
        cap = "" if stop is None else f" {stop.max_requests}"
        return f"(--max-requests{cap} ran out before the scan started)"
    bound = "≤" if filtered else ""
    note = "" if stop is None else f"; {_scan_note(stop)}"
    return f"(page {page}, {shown} of {bound}{total:,} {noun}{note})"


def _footer(next_page_cmd: str | None, *, capped: bool) -> list[str]:
    """Continuation footer. `Continue scan:` warns the next window may be short."""
    if not next_page_cmd:
        return []
    label = "Continue scan" if capped else "Next page"
    return ["", f"{label}: {next_page_cmd}"]


def _artist_string(artists: list[Any] | None) -> str:
    """Join artist credits into a display string, each with its ref.

    Refs are always shown: the artist is the most common next hop from a
    release, and `[@a3857]` costs three tokens.
    """
    if not artists:
        return "Unknown Artist"
    parts = []
    for a in artists:
        name = getattr(a, "name", None) or "Unknown"
        parts.append(name)
        artist_id = getattr(a, "id", None)
        if artist_id is not None:
            parts.append(f"[{make_ref('artist', artist_id)}]")
        join = getattr(a, "join", None)
        if join:
            parts.append(join)
    return " ".join(parts)


def _format_string(formats: list[Any] | None) -> str:
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


def _label_string(labels: list[Any] | None) -> str:
    """First label name with its ref and catalog number."""
    if not labels:
        return ""
    lbl = labels[0]
    name = getattr(lbl, "name", None) or ""
    ref_suffix = ""
    label_id = getattr(lbl, "id", None)
    if label_id is not None:
        ref_suffix = f" [{make_ref('label', label_id)}]"
    if catno := getattr(lbl, "catalog_number", None) or "":
        return f"{name}{ref_suffix} ({catno})"
    return f"{name}{ref_suffix}"


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


def format_credits(release: Any) -> str:
    """Release and track credits grouped by role, each person with an artist ref."""
    ref = make_ref("release", release.id)
    by_role = credits_by_role(release)
    people = {c.person for entries in by_role.values() for c in entries}
    lines = [f'Credits: {ref} "{release.title}" ({len(people)})', ""]
    if not by_role:
        lines.append("  (no credits listed)")
        return _text(lines)

    for role, entries in by_role.items():
        names = []
        for credit in entries:
            tag = f" [{make_ref('artist', credit.id)}]" if credit.id else ""
            scope = f" ({', '.join(credit.tracks)})" if credit.tracks else ""
            names.append(f"{credit.name or 'Unknown'}{tag}{scope}")
        lines.append(f"{role}: {', '.join(names)}")
    return _text(lines)


def format_identifiers(release: Any) -> str:
    """Barcodes, matrix/runout, and other identifiers: what tells pressings apart."""
    ref = make_ref("release", release.id)
    identifiers = getattr(release, "identifiers", None) or []
    lines = [f'Identifiers: {ref} "{release.title}"', ""]
    if not identifiers:
        lines.append("  (no identifiers listed)")
        return _text(lines)
    for ident in identifiers:
        desc = getattr(ident, "description", None)
        suffix = f" ({desc})" if desc else ""
        lines.append(
            f"{getattr(ident, 'type', None) or 'Other'}: "
            f"{getattr(ident, 'value', None) or ''}{suffix}"
        )
    return _text(lines)


def _track_artist_prefix(track: Any) -> str:
    """Return 'Artist - ' prefix for a track, or '' if no track-level artists."""
    artists = getattr(track, "artists", None)
    if not artists:
        return ""
    return _artist_string(artists) + " - "


def _format_track_lines(tracklist: list[Any], depth: int = 1) -> list[str]:
    """Format a tracklist into display lines with optional per-track artists.

    An index track (a suite or medley) lists its parts one level deeper.
    """
    indent = "  " * depth
    lines: list[str] = []
    for track in tracklist:
        pos = getattr(track, "position", "") or ""
        title = getattr(track, "title", "") or ""
        dur = getattr(track, "duration", "") or ""
        if getattr(track, "type", None) == "heading":
            lines.append(f"{indent}{title}")
            continue
        artist_prefix = _track_artist_prefix(track)
        dur_str = f" ({dur})" if dur else ""
        if pos:
            lines.append(f"{indent}{pos}. {artist_prefix}{title}{dur_str}")
        else:
            lines.append(f"{indent}{artist_prefix}{title}{dur_str}")
        sub_tracks = getattr(track, "sub_tracks", None) or []
        lines.extend(_format_track_lines(sub_tracks, depth + 1))
    return lines


def _truncate(text: str | None, length: int = 300) -> str:
    """Truncate text to a max length with ellipsis."""
    if not text:
        return ""
    text = text.strip()
    if len(text) <= length:
        return text
    return text[:length].rsplit(" ", 1)[0] + "..."


def _search_result_type(result: Any) -> str | None:
    """The result's entity type if it has a ref prefix, else None.

    Discogs may send any type string, or none. Only a known type says which
    kind of ID `result.id` is, so any other result gets no ref.
    """
    return result.type if result.type in TYPE_TO_PREFIX else None


def _community_have(obj: Any) -> int | None:
    """Community 'have' count from a search result or master version, if present."""
    community = getattr(obj, "community", None)
    if community is None:
        stats = getattr(obj, "stats", None)
        community = getattr(stats, "community", None)
    if community is None:
        return None
    have = getattr(community, "have", None)
    if have is None:
        have = getattr(community, "in_collection", None)
    return have if isinstance(have, int) else None


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


def _release_row(
    *,
    ref: str,
    type_: str | None = None,
    title: str | None = None,
    artist: str | None = None,
    year: int | str | None = None,
    country: str | None = None,
    label: str | None = None,
    catno: str | None = None,
    fmt: str | None = None,
    role: str | None = None,
    have: int | None = None,
    master_ref: str | None = None,
) -> str:
    """One list row: everything an agent needs to pick or navigate, nothing else.

    Shared by search, versions, and discography views so a release looks the
    same wherever it appears. Every part is optional and omitted when empty.
    """
    parts = [ref]
    if type_:
        parts.append(f"[{type_}]")
    if title:
        parts.append(f'"{title}"')
    if artist:
        parts.append(f"by {artist}")
    meta = " ".join(str(p) for p in (known_year(year), country) if p)
    if meta:
        parts.append(meta)
    if label or catno:
        parts.append("· " + " ".join(p for p in (label, catno) if p))
    if fmt:
        parts.append(f"· {fmt}")
    if role:
        parts.append(f"· {role}")
    if have is not None:
        parts.append(f"· have {have:,}")
    if master_ref:
        parts.append(f"→ {master_ref}")
    return " ".join(parts)


def _format_filters(filters: dict[str, str] | None) -> str:
    """Render applied filters for a list header: `year=1994 label="R & S"`."""
    if not filters:
        return ""
    parts = []
    for key, value in filters.items():
        shown = f'"{value}"' if " " in value else value
        parts.append(f"{key}={shown}")
    return " ".join(parts)


def _urls_short(urls: list[str] | None) -> str:
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


def format_artist(artist: Any) -> str:
    """Format an artist profile view."""
    ref = make_ref("artist", artist.id)
    lines = [f'{ref} [artist] "{artist.name}"']

    profile = _truncate(getattr(artist, "profile", None))
    if profile:
        lines.append(f"Profile: {profile}")

    urls = _urls_short(getattr(artist, "urls", None))
    if urls:
        lines.append(f"URLs: {urls}")

    current, former = split_members(artist)
    if current:
        lines.append(f"Members: {named_refs(current, 'artist')}")
    if former:
        lines.append(f"Former: {named_refs(former, 'artist')}")

    return _text(lines)


def field(obj: Any, name: str) -> Any:
    """Attribute or dict key: the SDK leaves untyped API fields as raw dicts."""
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def named_refs(items: list[Any], entity_type: str) -> str:
    """`Name [@a123], Other [@a456]`: every navigable name carries its ref."""
    parts = []
    for item in items:
        name = field(item, "name") or "Unknown"
        item_id = field(item, "id")
        parts.append(f"{name} [{make_ref(entity_type, item_id)}]" if item_id else name)
    return ", ".join(parts)


def format_label_releases(
    releases: list[Any],
    label_ref: str,
    label_name: str,
    page: int,
    total_results: int | None,
    next_page_cmd: str | None,
    *,
    filtered: bool = False,
    stop: ScanStop | None = None,
) -> str:
    """Format a label's catalogue (client-side `--year` scans are `filtered`)."""
    counts = _page_counts(
        page, len(releases), total_results, "results", filtered=filtered, stop=stop
    )
    lines = [f'Releases on {label_ref} "{label_name}" {counts}', ""]
    lines.extend(
        _release_row(
            ref=make_ref("release", rel.id),
            title=rel.title,
            artist=getattr(rel, "artist", None),
            year=getattr(rel, "year", None),
            catno=getattr(rel, "catalog_number", None),
            fmt=getattr(rel, "format", None),
        )
        for rel in releases
    )
    lines.extend(_footer(next_page_cmd, capped=stop is not None))
    return _text(lines)


def format_artist_releases(
    releases: list[Any],
    artist_ref: str,
    artist_name: str,
    page: int,
    total_results: int | None,
    next_page_cmd: str | None,
    *,
    filtered: bool = False,
    stop: ScanStop | None = None,
) -> str:
    """Format artist releases/discography."""
    counts = _page_counts(
        page, len(releases), total_results, "results", filtered=filtered, stop=stop
    )
    lines = [f'Releases by {artist_ref} "{artist_name}" {counts}', ""]

    lines.extend(
        _release_row(
            ref=make_ref(getattr(rel, "type", "release"), rel.id),
            type_=getattr(rel, "type", "release"),
            title=rel.title,
            year=getattr(rel, "year", None),
            label=getattr(rel, "label", None),
            fmt=getattr(rel, "format", None),
            role=getattr(rel, "role", None),
        )
        for rel in releases
    )
    lines.extend(_footer(next_page_cmd, capped=stop is not None))
    return _text(lines)


def format_label(label: Any) -> str:
    """Format a label profile view."""
    ref = make_ref("label", label.id)
    lines = [f'{ref} [label] "{label.name}"']

    profile = _truncate(getattr(label, "profile", None))
    if profile:
        lines.append(f"Profile: {profile}")

    urls = _urls_short(getattr(label, "urls", None))
    if urls:
        lines.append(f"URLs: {urls}")

    parent = getattr(label, "parent_label", None)
    if parent:
        lines.append(f"Parent: {named_refs([parent], 'label')}")

    sub_labels = getattr(label, "sub_labels", None)
    if sub_labels:
        lines.append(f"Sub-labels: {named_refs(sub_labels, 'label')}")

    return _text(lines)


def format_master(master: Any) -> str:
    """Format a master release detail view."""
    ref = make_ref("master", master.id)
    artists = _artist_string(getattr(master, "artists", None))
    year = known_year(getattr(master, "year", None)) or ""
    year_str = f" ({year})" if year else ""

    lines = [f'{ref} [master] "{master.title}" by {artists}{year_str}']

    genres = getattr(master, "genres", None)
    if genres:
        lines.append(f"Genres: {', '.join(genres)}")

    styles = getattr(master, "styles", None)
    if styles:
        lines.append(f"Styles: {', '.join(styles)}")

    main_release = getattr(master, "main_release", None)
    if main_release:
        lines.append(f"Main release: {make_ref('release', main_release)}")

    num_for_sale = getattr(master, "num_for_sale", None)
    lowest_price = getattr(master, "lowest_price", None)
    if num_for_sale is not None:
        sale_str = f"Market: {num_for_sale:,} for sale"
        if lowest_price is not None:
            sale_str += f" from {lowest_price:.2f}"
        lines.append(sale_str)

    tracklist = getattr(master, "tracklist", None)
    if tracklist:
        lines.append("")
        lines.append("Tracklist:")
        lines.extend(_format_track_lines(tracklist))

    return _text(lines)


def format_master_versions(
    versions: list[Any],
    master_ref: str,
    master_title: str,
    page: int,
    total_results: int | None,
    next_page_cmd: str | None,
) -> str:
    """Format master release versions."""
    counts = _page_counts(
        page, len(versions), total_results, "versions", filtered=False
    )
    lines = [f'Versions of {master_ref} "{master_title}" {counts}', ""]

    lines.extend(
        _release_row(
            ref=make_ref("release", ver.id),
            year=release_date(getattr(ver, "released", None)),
            country=getattr(ver, "country", None),
            label=getattr(ver, "label", None),
            catno=getattr(ver, "catalog_number", None),
            fmt=getattr(ver, "format", None),
            have=_community_have(ver),
        )
        for ver in versions
    )

    lines.extend(_footer(next_page_cmd, capped=False))
    return _text(lines)


def _price(price: Any) -> str | None:
    """`EUR 20.00`: an SDK `Price` with the ISO code Discogs sent, never a symbol.

    A dollar sign would be ambiguous (USD, CAD, AUD) and wrong for anything else.
    None when the price carries no value.
    """
    value = getattr(price, "value", None)
    if value is None:
        return None
    currency = getattr(price, "currency", None)
    return f"{currency} {value:.2f}" if currency else f"{value:.2f}"


def format_price_guide(
    release: Any,
    price_suggestions: Any,
    marketplace_stats: Any,
) -> str:
    """Format price guide for a release."""
    ref = make_ref("release", release.id)
    artists = _artist_string(getattr(release, "artists", None))
    year = getattr(release, "year", None) or ""
    year_str = f" ({year})" if year else ""

    lines = [
        f'Price Guide: {ref} "{release.title}" by {artists}{year_str}',
        "",
    ]

    condition_order = [
        "Mint (M)",
        "Near Mint (NM or M-)",
        "Very Good Plus (VG+)",
        "Very Good (VG)",
        "Good Plus (G+)",
        "Good (G)",
        "Fair (F)",
        "Poor (P)",
    ]

    conditions: Any = getattr(price_suggestions, "conditions", None)

    if conditions:
        for cond in condition_order:
            if cond in conditions:
                amount = _price(conditions[cond])
                if amount is not None:
                    lines.append(f"{cond + ':':30s} {amount}")
    else:
        lines.append("  No price suggestions available")

    lines.append("")

    num_for_sale = getattr(marketplace_stats, "num_for_sale", None)
    lowest = _price(getattr(marketplace_stats, "lowest_price", None))
    parts = []
    if num_for_sale is not None:
        parts.append(f"For sale: {num_for_sale:,}")
    if lowest is not None:
        parts.append(f"Lowest: {lowest}")
    if parts:
        lines.append(" · ".join(parts))

    return _text(lines)


def _duration_seconds(duration: str | None) -> int | None:
    """`4:30` or `1:02:03` in seconds; None for a blank or malformed duration."""
    parts = (duration or "").split(":")
    if not all(p.isdigit() for p in parts):
        return None
    seconds = 0
    for part in parts:
        seconds = seconds * 60 + int(part)
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


def format_release(
    release: Any, *, verbose: bool = False, compact: bool = False
) -> str:
    """Format a full release detail view.

    `verbose` appends notes, credits, and identifiers: the facets that matter
    when identifying a pressing or asking who worked on it. `compact` replaces
    the tracklist with a one-line `Tracks:` summary; `tracks @r...` is the
    dedicated tracklist view.
    """
    ref = make_ref("release", release.id)
    artists = _artist_string(getattr(release, "artists", None))
    year = known_year(getattr(release, "year", None)) or ""
    year_str = f" ({year})" if year else ""

    lines = [f'{ref} [release] "{release.title}" by {artists}{year_str}']

    label = _label_string(getattr(release, "labels", None))
    if label:
        lines.append(f"Label: {label}")

    fmt = _format_string(getattr(release, "formats", None))
    if fmt:
        lines.append(f"Format: {fmt}")

    country = getattr(release, "country", None)
    released = release_date(getattr(release, "released", None))
    # `released` repeats the header year unless it carries a month/day.
    origin = [f"Country: {country}" if country else ""]
    if released and released != str(year):
        origin.append(f"Released: {released}")
    added = getattr(release, "date_added", None)
    if added:
        # The day in the timestamp's own offset, not converted to UTC or local.
        origin.append(f"Added: {added.date().isoformat()}")
    if any(origin):
        lines.append(" · ".join(p for p in origin if p))

    genres = getattr(release, "genres", None)
    if genres:
        lines.append(f"Genres: {', '.join(genres)}")

    styles = getattr(release, "styles", None)
    if styles:
        lines.append(f"Styles: {', '.join(styles)}")

    community = getattr(release, "community", None)
    if community:
        rating = getattr(community, "rating", None)
        if rating:
            avg = getattr(rating, "average", None)
            count = getattr(rating, "count", None)
            if avg is not None and count is not None:
                lines.append(f"Rating: {avg:.2f}/5 ({count:,} votes)")

        have = getattr(community, "have", None)
        want = getattr(community, "want", None)
        if have is not None and want is not None:
            lines.append(f"Have: {have:,} · Want: {want:,}")

    num_for_sale = getattr(release, "num_for_sale", None)
    lowest_price = getattr(release, "lowest_price", None)
    if num_for_sale is not None:
        sale_str = f"Market: {num_for_sale:,} for sale"
        if lowest_price is not None:
            sale_str += f" from {lowest_price:.2f}"
        lines.append(sale_str)

    master_id = getattr(release, "master_id", None)
    if master_id:
        master_ref = make_ref("master", master_id)
        lines.append(f"Master: {master_ref}")

    if verbose:
        notes = (getattr(release, "notes", None) or "").strip()
        if notes:
            # Continuation lines are indented: nothing in the notes may start
            # a line at column 0, where it could pass for CLI framing.
            first, *rest = notes.splitlines()
            lines.append(f"Notes: {first}")
            lines.extend(f"  {part}" if part.strip() else "" for part in rest)

    tracklist = getattr(release, "tracklist", None) or []
    if tracklist and compact:
        lines.append(f"Tracks: {track_summary(tracklist)}")
    elif tracklist:
        lines.append("")
        lines.append("Tracklist:")
        lines.extend(_format_track_lines(tracklist))

    if verbose:
        for section in (format_credits(release), format_identifiers(release)):
            lines.append("")
            lines.extend(section.split("\n"))

    return _text(lines)


def format_search_results(
    results: list[Any],
    query: str,
    type_filter: str | None,
    page: int,
    total_results: int | None,
    next_page_cmd: str | None,
    filters: dict[str, str] | None = None,
    *,
    filtered: bool = False,
    stop: ScanStop | None = None,
) -> str:
    """Format search results as compact text.

    The `[type]` tag is shown only for untyped searches; with a type filter the
    ref prefix already carries it. Release rows carry country, catalog number,
    community have-count, and the master ref so an agent can pick and navigate
    without a detail call. A result of an unknown or missing type has no ref:
    its row starts with `id N` and always shows its tag.
    """
    subject = " ".join(
        p for p in (f'"{query}"' if query else "", _format_filters(filters)) if p
    )
    head = " ".join(p for p in ("Search:", type_filter or "all", subject) if p)
    counts = _page_counts(
        page, len(results), total_results, "results", filtered=filtered, stop=stop
    )
    lines = [f"{head} {counts}", ""]

    for result in results:
        result_type = _search_result_type(result)
        if result_type is None:
            lines.append(
                _release_row(
                    ref=f"id {result.id}",
                    type_=result.type or "no type",
                    title=result.title,
                )
            )
            continue
        ref = make_ref(result_type, result.id)
        show_type = None if type_filter else result_type

        if result_type in ("release", "master"):
            label_list = getattr(result, "label", None)
            fmt_list = getattr(result, "format", None)
            master_id = getattr(result, "master_id", None)
            master_ref = (
                make_ref("master", master_id)
                if result_type == "release" and master_id
                else None
            )
            lines.append(
                _release_row(
                    ref=ref,
                    type_=show_type,
                    title=result.title,
                    year=getattr(result, "year", None),
                    country=getattr(result, "country", None),
                    label=label_list[0] if label_list else None,
                    catno=getattr(result, "catalog_number", None),
                    fmt=", ".join(fmt_list) if fmt_list else None,
                    have=_community_have(result),
                    master_ref=master_ref,
                )
            )
        else:
            lines.append(_release_row(ref=ref, type_=show_type, title=result.title))

    lines.extend(_footer(next_page_cmd, capped=stop is not None))
    return _text(lines)


# Discogs serves 60 req/min to any credentials and 25 without. Consumer
# key/secret identify an app, not a user, so user-only endpoints such as price
# suggestions refuse them.
_AUTH_LABELS: dict[AuthMode, str] = {
    "token": "token (authenticated)",
    "oauth": "oauth (authenticated)",
    "consumer": "consumer (authenticated app, not a user: no price suggestions)",
    "none": "none (unauthenticated, 25 req/min)",
}


def format_status(version: str, auth_mode: AuthMode, cache_dir: Path) -> str:
    """Format status output."""
    auth_str = _AUTH_LABELS[auth_mode]
    return _text(
        [
            f"agent-discogs v{version}",
            f"Auth: {auth_str}",
            f"Cache: {cache_dir} ({CACHE_TTL_LABEL} TTL)",
        ]
    )


def format_tracklist(release: Any) -> str:
    """Format just the tracklist from a release."""
    ref = make_ref("release", release.id)
    artists = _artist_string(getattr(release, "artists", None))
    year = getattr(release, "year", None) or ""
    year_str = f" ({year})" if year else ""

    lines = [f'Tracklist: {ref} "{release.title}" by {artists}{year_str}', ""]

    tracklist = getattr(release, "tracklist", None) or []
    if not tracklist:
        lines.append("  (no tracklist available)")
        return _text(lines)

    lines.extend(_format_track_lines(tracklist))

    return _text(lines)
