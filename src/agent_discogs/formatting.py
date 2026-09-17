"""SDK Pydantic models → compact text output."""

from __future__ import annotations

import re
from typing import Any

from agent_discogs.pagination import MAX_API_CALLS
from agent_discogs.refs import make_ref


def _page_counts(
    page: int, shown: int, total: int, noun: str, *, filtered: bool, capped: bool
) -> str:
    """`(page 2, 5 of ≤23 results; scan capped at 5 API calls)`.

    `≤` marks a client-side filtered list whose total is the unfiltered API
    count; the capped note means the scan stopped early and the page may be
    short.
    """
    bound = "≤" if filtered else ""
    note = f"; scan capped at {MAX_API_CALLS} API calls" if capped else ""
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
    """Join format info into a compact string."""
    if not formats:
        return ""
    parts = []
    for f in formats:
        name = getattr(f, "name", None) or ""
        descriptions = getattr(f, "descriptions", None) or []
        if name:
            parts.append(name)
        parts.extend(descriptions)
    return ", ".join(parts)


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


def credits_by_role(release: Any) -> dict[str, list[Any]]:
    """Invert Discogs credits from per-person to per-role.

    Discogs stores one entry per person with a comma-joined role string such
    as `Producer [Production], Written-By`; both the text and JSON views group
    by role so "who produced this?" is one line or one key. Roles sort
    alphabetically; people keep Discogs order within a role.
    """
    by_role: dict[str, list[Any]] = {}
    for credit in getattr(release, "extra_artists", None) or []:
        for role in _ROLE_SPLIT.split(getattr(credit, "role", None) or ""):
            by_role.setdefault(role.strip() or "Other", []).append(credit)
    return dict(sorted(by_role.items()))


def format_credits(release: Any) -> str:
    """Release credits grouped by role, each person with an artist ref."""
    ref = make_ref("release", release.id)
    entries = getattr(release, "extra_artists", None) or []
    lines = [f'Credits: {ref} "{release.title}" ({len(entries)})', ""]
    if not entries:
        lines.append("  (no credits listed)")
        return "\n".join(lines)

    for role, people_credits in credits_by_role(release).items():
        people = []
        for credit in people_credits:
            name = getattr(credit, "name", None) or "Unknown"
            artist_id = getattr(credit, "id", None)
            tag = f" [{make_ref('artist', artist_id)}]" if artist_id else ""
            tracks = getattr(credit, "tracks", None) or ""
            scope = f" ({tracks})" if tracks else ""
            people.append(f"{name}{tag}{scope}")
        lines.append(f"{role}: {', '.join(people)}")
    return "\n".join(lines)


def format_identifiers(release: Any) -> str:
    """Barcodes, matrix/runout, and other identifiers: what tells pressings apart."""
    ref = make_ref("release", release.id)
    identifiers = getattr(release, "identifiers", None) or []
    lines = [f'Identifiers: {ref} "{release.title}"', ""]
    if not identifiers:
        lines.append("  (no identifiers listed)")
        return "\n".join(lines)
    for ident in identifiers:
        desc = getattr(ident, "description", None)
        suffix = f" ({desc})" if desc else ""
        lines.append(
            f"{getattr(ident, 'type', None) or 'Other'}: "
            f"{getattr(ident, 'value', None) or ''}{suffix}"
        )
    return "\n".join(lines)


def _track_artist_prefix(track: Any) -> str:
    """Return 'Artist - ' prefix for a track, or '' if no track-level artists."""
    artists = getattr(track, "artists", None)
    if not artists:
        return ""
    return _artist_string(artists) + " - "


def _format_track_lines(tracklist: list[Any]) -> list[str]:
    """Format a tracklist into display lines with optional per-track artists."""
    lines: list[str] = []
    for track in tracklist:
        pos = getattr(track, "position", "") or ""
        title = getattr(track, "title", "") or ""
        dur = getattr(track, "duration", "") or ""
        type_ = getattr(track, "type_", "") or ""
        if type_ == "heading":
            lines.append(f"  {title}")
            continue
        artist_prefix = _track_artist_prefix(track)
        dur_str = f" ({dur})" if dur else ""
        if pos:
            lines.append(f"  {pos}. {artist_prefix}{title}{dur_str}")
        else:
            lines.append(f"  {artist_prefix}{title}{dur_str}")
    return lines


def _truncate(text: str | None, length: int = 300) -> str:
    """Truncate text to a max length with ellipsis."""
    if not text:
        return ""
    text = text.strip()
    if len(text) <= length:
        return text
    return text[:length].rsplit(" ", 1)[0] + "..."


def _search_result_type(result: Any) -> str:
    """Determine entity type for a search result."""
    return getattr(result, "type", "release") or "release"


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


def _release_row(
    *,
    ref: str,
    type_: str | None = None,
    title: str | None = None,
    artist: str | None = None,
    year: object = None,
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
    meta = " ".join(str(p) for p in (year, country) if p)
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
        # Per-URL try/except: one malformed URL must not discard the others.
        try:
            parts = url.split("//", 1)
            domain = parts[1].split("/", 1)[0] if len(parts) > 1 else parts[0]
            domain = domain.removeprefix("www.")
            domains.append(domain)
        except (IndexError, AttributeError):  # noqa: PERF203
            continue
    return ", ".join(domains)


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

    members = getattr(artist, "members", None) or []
    current = [m for m in members if getattr(m, "active", True)]
    former = [m for m in members if not getattr(m, "active", True)]
    if current:
        lines.append(f"Members: {named_refs(current, 'artist')}")
    if former:
        lines.append(f"Former: {named_refs(former, 'artist')}")

    return "\n".join(lines)


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
    total_results: int,
    next_page_cmd: str | None,
    *,
    filtered: bool = False,
    capped: bool = False,
) -> str:
    """Format a label's catalogue (client-side `--year` scans are `filtered`)."""
    counts = _page_counts(
        page, len(releases), total_results, "results", filtered=filtered, capped=capped
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
    lines.extend(_footer(next_page_cmd, capped=capped))
    return "\n".join(lines)


def format_artist_releases(
    releases: list[Any],
    artist_ref: str,
    artist_name: str,
    page: int,
    total_results: int,
    next_page_cmd: str | None,
    *,
    filtered: bool = False,
    capped: bool = False,
) -> str:
    """Format artist releases/discography."""
    counts = _page_counts(
        page, len(releases), total_results, "results", filtered=filtered, capped=capped
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
    lines.extend(_footer(next_page_cmd, capped=capped))
    return "\n".join(lines)


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

    return "\n".join(lines)


def format_master(master: Any) -> str:
    """Format a master release detail view."""
    ref = make_ref("master", master.id)
    artists = _artist_string(getattr(master, "artists", None))
    year = getattr(master, "year", None) or ""
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
            sale_str += f" from ${lowest_price:.2f}"
        lines.append(sale_str)

    tracklist = getattr(master, "tracklist", None)
    if tracklist:
        lines.append("")
        lines.append("Tracklist:")
        lines.extend(_format_track_lines(tracklist))

    return "\n".join(lines)


def format_master_versions(
    versions: list[Any],
    master_ref: str,
    master_title: str,
    page: int,
    total_results: int,
    next_page_cmd: str | None,
) -> str:
    """Format master release versions."""
    counts = _page_counts(
        page, len(versions), total_results, "versions", filtered=False, capped=False
    )
    lines = [f'Versions of {master_ref} "{master_title}" {counts}', ""]

    lines.extend(
        _release_row(
            ref=make_ref("release", ver.id),
            year=getattr(ver, "released", None),
            country=getattr(ver, "country", None),
            label=getattr(ver, "label", None),
            catno=getattr(ver, "catalog_number", None),
            fmt=getattr(ver, "format", None),
            have=_community_have(ver),
        )
        for ver in versions
    )

    lines.extend(_footer(next_page_cmd, capped=False))
    return "\n".join(lines)


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

    conditions: Any = getattr(price_suggestions, "conditions", {})
    # Some SDK versions expose `conditions` as a zero-arg method, not a mapping.
    if conditions is not None and not isinstance(conditions, dict):
        conditions = conditions()

    if conditions:
        for cond in condition_order:
            if cond in conditions:
                price = conditions[cond]
                value = getattr(price, "value", None)
                if value is not None:
                    lines.append(f"{cond + ':':30s} ${value:.2f}")
    else:
        lines.append("  No price suggestions available")

    lines.append("")

    num_for_sale = getattr(marketplace_stats, "num_for_sale", None)
    lowest_price = getattr(marketplace_stats, "lowest_price", None)
    parts = []
    if num_for_sale is not None:
        parts.append(f"For sale: {num_for_sale:,}")
    if lowest_price is not None:
        value = getattr(lowest_price, "value", None)
        if value is not None:
            parts.append(f"Lowest: ${value:.2f}")
    if parts:
        lines.append(" · ".join(parts))

    return "\n".join(lines)


def track_summary(tracklist: list[Any]) -> str:
    """`14 (65:01)`: track count and summed duration, for compact views.

    Headings are not counted. The total is shown only when every counted track
    has a valid duration; a partial sum would read as the whole release's
    running time, so a single blank duration drops it to the bare count.
    """
    tracks = [
        t for t in tracklist if (getattr(t, "type_", None) or "track") != "heading"
    ]
    seconds = 0
    for track in tracks:
        parts = (getattr(track, "duration", None) or "").split(":")
        if not all(p.isdigit() for p in parts):
            return str(len(tracks))
        track_seconds = 0
        for part in parts:
            track_seconds = track_seconds * 60 + int(part)
        seconds += track_seconds
    if not tracks:
        return "0"
    minutes, secs = divmod(seconds, 60)
    return f"{len(tracks)} ({minutes}:{secs:02d})"


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
    year = getattr(release, "year", None) or ""
    year_str = f" ({year})" if year else ""

    lines = [f'{ref} [release] "{release.title}" by {artists}{year_str}']

    label = _label_string(getattr(release, "labels", None))
    if label:
        lines.append(f"Label: {label}")

    fmt = _format_string(getattr(release, "formats", None))
    if fmt:
        lines.append(f"Format: {fmt}")

    country = getattr(release, "country", None)
    released = getattr(release, "released", None)
    # `released` repeats the header year unless it carries a month/day.
    origin = [f"Country: {country}" if country else ""]
    if released and released != str(year):
        origin.append(f"Released: {released}")
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
            sale_str += f" from ${lowest_price:.2f}"
        lines.append(sale_str)

    master_id = getattr(release, "master_id", None)
    if master_id:
        master_ref = make_ref("master", master_id)
        lines.append(f"Master: {master_ref}")

    if verbose:
        notes = (getattr(release, "notes", None) or "").strip()
        if notes:
            lines.append(f"Notes: {notes}")

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

    return "\n".join(lines)


def format_search_results(
    results: list[Any],
    query: str,
    type_filter: str | None,
    page: int,
    total_results: int,
    next_page_cmd: str | None,
    filters: dict[str, str] | None = None,
    *,
    filtered: bool = False,
    capped: bool = False,
) -> str:
    """Format search results as compact text.

    The `[type]` tag is shown only for untyped searches; with a type filter the
    ref prefix already carries it. Release rows carry country, catalog number,
    community have-count, and the master ref so an agent can pick and navigate
    without a detail call.
    """
    subject = " ".join(
        p for p in (f'"{query}"' if query else "", _format_filters(filters)) if p
    )
    head = " ".join(p for p in ("Search:", type_filter or "all", subject) if p)
    counts = _page_counts(
        page, len(results), total_results, "results", filtered=filtered, capped=capped
    )
    lines = [f"{head} {counts}", ""]

    for result in results:
        result_type = _search_result_type(result)
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

    lines.extend(_footer(next_page_cmd, capped=capped))
    return "\n".join(lines)


def format_status(
    version: str,
    authenticated: bool,
    cache_dir: Any = None,
) -> str:
    """Format status output."""
    auth_str = (
        "token (authenticated)"
        if authenticated
        else "none (unauthenticated, 25 req/min)"
    )
    lines = [
        f"agent-discogs v{version}",
        f"Auth: {auth_str}",
    ]
    if cache_dir is not None:
        lines.append(f"Cache: {cache_dir} (1h TTL)")
    return "\n".join(lines)


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
        return "\n".join(lines)

    lines.extend(_format_track_lines(tracklist))

    return "\n".join(lines)
