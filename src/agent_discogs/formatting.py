"""SDK Pydantic models → compact text output."""

from __future__ import annotations

from typing import Any

from agent_discogs.refs import make_ref


def _artist_string(artists: list[Any] | None, *, verbose: bool = False) -> str:
    """Join artist credits into a display string."""
    if not artists:
        return "Unknown Artist"
    parts = []
    for a in artists:
        name = getattr(a, "name", None) or "Unknown"
        parts.append(name)
        if verbose:
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
        descs = getattr(f, "descriptions", None) or []
        if name:
            parts.append(name)
        parts.extend(descs)
    return ", ".join(parts)


def _label_string(labels: list[Any] | None, *, verbose: bool = False) -> str:
    """First label name and catalog number."""
    if not labels:
        return ""
    lbl = labels[0]
    name = getattr(lbl, "name", None) or ""
    ref_suffix = ""
    if verbose:
        label_id = getattr(lbl, "id", None)
        if label_id is not None:
            ref_suffix = f" [{make_ref('label', label_id)}]"
    if catno := getattr(lbl, "catalog_number", None) or "":
        return f"{name}{ref_suffix} ({catno})"
    return f"{name}{ref_suffix}"


def _track_artist_prefix(track: Any, *, verbose: bool = False) -> str:
    """Return 'Artist - ' prefix for a track, or '' if no track-level artists."""
    artists = getattr(track, "artists", None)
    if not artists:
        return ""
    return _artist_string(artists, verbose=verbose) + " - "


def _format_track_lines(tracklist: list[Any], *, verbose: bool = False) -> list[str]:
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
        artist_prefix = _track_artist_prefix(track, verbose=verbose)
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


def _urls_short(urls: list[str] | None) -> str:
    """Extract domain names from URLs for compact display."""
    if not urls:
        return ""
    domains = []
    for url in urls:
        try:
            parts = url.split("//", 1)
            domain = parts[1].split("/", 1)[0] if len(parts) > 1 else parts[0]
            domain = domain.removeprefix("www.")
            domains.append(domain)
        except (IndexError, AttributeError):
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

    members = getattr(artist, "members", None)
    if members:
        member_names = [
            getattr(m, "name", "Unknown") for m in members if getattr(m, "active", True)
        ]
        if member_names:
            lines.append(f"Members: {', '.join(member_names)}")

    return "\n".join(lines)


def format_artist_releases(
    releases: list[Any],
    artist_ref: str,
    artist_name: str,
    page: int,
    total_results: int,
    next_page_cmd: str | None,
) -> str:
    """Format artist releases/discography."""
    header = f'Releases by {artist_ref} "{artist_name}" (page {page}, {len(releases)} of {total_results:,} results)'
    lines = [header, ""]

    for rel in releases:
        rel_type = getattr(rel, "type", "release")
        ref = make_ref(rel_type, rel.id)
        title = rel.title
        year = getattr(rel, "year", None) or ""
        role = getattr(rel, "role", "") or ""

        parts = [f"{ref} [{rel_type}]"]
        parts.append(f'"{title}"')
        if year:
            parts.append(f"({year})")
        if role:
            parts.append(f"· {role}")

        lines.append(" ".join(parts))

    if next_page_cmd:
        lines.append("")
        lines.append(f"Next page: {next_page_cmd}")

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

    sub_labels = getattr(label, "sub_labels", None)
    if sub_labels:
        names = [getattr(s, "name", "Unknown") for s in sub_labels]
        lines.append(f"Sub-labels: {', '.join(names)}")

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
    header = f'Versions of {master_ref} "{master_title}" (page {page}, {len(versions)} of {total_results:,} versions)'
    lines = [header, ""]

    for ver in versions:
        ref = make_ref("release", ver.id)
        released = getattr(ver, "released", "") or ""
        country = getattr(ver, "country", "") or ""
        label = getattr(ver, "label", "") or ""
        catno = getattr(ver, "catno", "") or ""
        fmt = getattr(ver, "format", "") or ""

        parts = [f"{ref} [release]"]
        if released:
            parts.append(f"({released})")
        if country:
            parts.append(country)
        if label and catno:
            parts.append(f"· {label} {catno}")
        elif label:
            parts.append(f"· {label}")
        if fmt:
            parts.append(f"· {fmt}")

        lines.append(" ".join(parts))

    if next_page_cmd:
        lines.append("")
        lines.append(f"Next page: {next_page_cmd}")

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

    conditions = getattr(price_suggestions, "conditions", {})
    if callable(conditions):
        conditions = conditions()  # type: ignore[operator]

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


def format_release(release: Any, *, verbose: bool = False) -> str:
    """Format a full release detail view."""
    ref = make_ref("release", release.id)
    artists = _artist_string(getattr(release, "artists", None), verbose=verbose)
    year = getattr(release, "year", None) or ""
    year_str = f" ({year})" if year else ""

    lines = [f'{ref} [release] "{release.title}" by {artists}{year_str}']

    label = _label_string(getattr(release, "labels", None), verbose=verbose)
    if label:
        lines.append(f"Label: {label}")

    fmt = _format_string(getattr(release, "formats", None))
    if fmt:
        lines.append(f"Format: {fmt}")

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
        notes = getattr(release, "notes", None)
        if notes:
            notes = notes.strip()
            if notes:
                lines.append(f"Notes: {notes}")

    tracklist = getattr(release, "tracklist", None)
    if tracklist:
        lines.append("")
        lines.append("Tracklist:")
        lines.extend(_format_track_lines(tracklist, verbose=verbose))

    return "\n".join(lines)


def format_search_results(
    results: list[Any],
    query: str,
    type_filter: str | None,
    page: int,
    total_results: int,
    next_page_cmd: str | None,
) -> str:
    """Format search results as compact text."""
    type_label = type_filter or "all"
    header = f'Search: {type_label} "{query}" (page {page}, {len(results)} of {total_results:,} results)'
    lines = [header, ""]

    for result in results:
        result_type = _search_result_type(result)
        ref = make_ref(result_type, result.id)
        title = result.title
        year = getattr(result, "year", None) or ""

        parts = [f"{ref} [{result_type}]"]

        if result_type in ("release", "master"):
            parts.append(f'"{title}"')
            if year:
                parts.append(f"({year})")
            label_list = getattr(result, "label", None)
            fmt_list = getattr(result, "format", None)
            extras = []
            if label_list:
                extras.append(label_list[0])
            if fmt_list:
                extras.append(", ".join(fmt_list))
            if extras:
                parts.append("·")
                parts.append(" · ".join(extras))
        else:
            parts.append(f'"{title}"')

        lines.append(" ".join(parts))

    if next_page_cmd:
        lines.append("")
        lines.append(f"Next page: {next_page_cmd}")

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
