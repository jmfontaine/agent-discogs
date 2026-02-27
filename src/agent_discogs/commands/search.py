"""Search command handler."""

from __future__ import annotations

import sys
from typing import Any

import click
from discogs_sdk import SearchResult

from agent_discogs.client import get_client
from agent_discogs.errors import format_error
from agent_discogs.formatting import format_search_results
from agent_discogs.json_output import dump_page
from agent_discogs.pagination import fetch_filtered_page, fetch_page

KNOWN_TYPES = {"artist", "label", "master", "release"}


def _build_search_params(
    *,
    query: str,
    type_filter: str | None,
    artist: str | None,
    barcode: str | None,
    catno: str | None,
    country: str | None,
    format: str | None,
    genre: str | None,
    label: str | None,
    style: str | None,
    year: str | None,
) -> dict[str, Any]:
    """Build Discogs API search parameters from CLI args (without pagination)."""
    params: dict[str, Any] = {"q": query} if query else {}

    if type_filter:
        params["type"] = type_filter

    for name, value in [
        ("year", year),
        ("genre", genre),
        ("style", style),
        ("country", country),
        ("format", format),
        ("catno", catno),
        ("barcode", barcode),
        ("artist", artist),
        ("label", label),
    ]:
        if value:
            params[name] = value

    return params


def _is_unofficial(item: SearchResult) -> bool:
    """Check if a search result is an unofficial release."""
    return "Unofficial Release" in (item.format or [])


def _parse_type_and_query(args_list: tuple[str, ...]) -> tuple[str | None, str]:
    """Extract optional type filter and query from positional args.

    If the first word matches a known type, treat it as the type filter.
    Everything else is the query.
    """
    if args_list and args_list[0].lower() in KNOWN_TYPES:
        return args_list[0].lower(), " ".join(args_list[1:])
    return None, " ".join(args_list)


@click.command()
@click.argument("args", nargs=-1)
@click.option("--artist", help="Filter by artist")
@click.option("--barcode", help="Filter by barcode")
@click.option("--catno", help="Filter by catalog number")
@click.option("--country", help="Filter by country")
@click.option("--format", "format_", help="Filter by format")
@click.option("--genre", help="Filter by genre")
@click.option(
    "--json", "json_output", is_flag=True, default=False, help="Output raw JSON"
)
@click.option("--label", help="Filter by label")
@click.option("--limit", type=int, default=5, help="Results per page")
@click.option("--page", type=int, default=1, help="Page number")
@click.option(
    "--release-type",
    type=click.Choice(["official", "unofficial", "all"]),
    default="official",
    help="Filter by release type",
)
@click.option("--style", help="Filter by style")
@click.option("--year", help="Filter by year")
def search(
    args: tuple[str, ...],
    json_output: bool,
    artist: str | None,
    barcode: str | None,
    catno: str | None,
    country: str | None,
    format_: str | None,
    genre: str | None,
    label: str | None,
    limit: int,
    page: int,
    release_type: str,
    style: str | None,
    year: str | None,
) -> None:
    """Search Discogs database.

    ARGS is [type] query, where type is one of: release, master, artist, label.
    """
    type_filter, query = _parse_type_and_query(args)

    has_filters = any(
        v is not None
        for v in (artist, barcode, catno, country, format_, genre, label, style, year)
    )
    if not query and not has_filters:
        print("✗ No search query or filters provided.", file=sys.stderr)
        sys.exit(1)

    client = get_client()
    params = _build_search_params(
        query=query,
        type_filter=type_filter,
        artist=artist,
        barcode=barcode,
        catno=catno,
        country=country,
        format=format_,
        genre=genre,
        label=label,
        style=style,
        year=year,
    )

    # Release-type filtering only applies to release/master searches.
    # Artist and label results never have format metadata, so filtering
    # is a no-op that wastes bandwidth by overfetching 3x.
    needs_release_filter = release_type != "all" and type_filter in (
        None,
        "release",
        "master",
    )

    try:
        if not needs_release_filter:
            result = fetch_page(
                client,
                "/database/search",
                {**params, "page": page, "per_page": limit},
                SearchResult,
                "results",
            )
        elif release_type == "unofficial":
            result = fetch_filtered_page(
                client,
                "/database/search",
                params,
                SearchResult,
                "results",
                page,
                limit,
                keep=_is_unofficial,
            )
        else:
            # official (default): exclude unofficial
            result = fetch_filtered_page(
                client,
                "/database/search",
                params,
                SearchResult,
                "results",
                page,
                limit,
                keep=lambda item: not _is_unofficial(item),
            )
    except Exception as e:
        print(format_error(e, "Search"), file=sys.stderr)
        sys.exit(1)

    if json_output:
        dump_page(result)
        return

    # Build next page command
    next_page_cmd = None
    if result.has_next:
        cmd_parts = ["agent-discogs search"]
        if type_filter:
            cmd_parts.append(type_filter)
        if query:
            cmd_parts.append(f'"{query}"')
        for flag, value in [
            ("--artist", artist),
            ("--barcode", barcode),
            ("--catno", catno),
            ("--country", country),
            ("--format", format_),
            ("--genre", genre),
            ("--label", label),
            ("--style", style),
            ("--year", year),
        ]:
            if value:
                cmd_parts.append(f"{flag} {value}")
        if release_type != "official":
            cmd_parts.append(f"--release-type {release_type}")
        cmd_parts.append(f"--page {result.page + 1}")
        next_page_cmd = " ".join(cmd_parts)

    output = format_search_results(
        results=result.items,
        query=query,
        type_filter=type_filter,
        page=result.page,
        total_results=result.total_items,
        next_page_cmd=next_page_cmd,
    )
    print(output)
