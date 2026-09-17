"""Search command handler."""

from __future__ import annotations

from typing import Any

import click
from discogs_sdk import SearchResult

from agent_discogs.client import get_client
from agent_discogs.errors import fail
from agent_discogs.formatting import format_search_results
from agent_discogs.json_output import dump_page
from agent_discogs.pagination import (
    DEFAULT_LIMIT,
    fetch_filtered_page,
    fetch_page,
    next_page_cmd,
    parse_cursor,
)

KNOWN_TYPES = {"artist", "label", "master", "release"}


def _build_search_params(
    *,
    query: str,
    type_filter: str | None,
    artist: str | None,
    barcode: str | None,
    catno: str | None,
    country: str | None,
    format: str | None,  # noqa: A002  # click option name for --format
    genre: str | None,
    label: str | None,
    style: str | None,
    year: str | None,
) -> dict[str, Any]:
    """Build Discogs API search parameters from CLI args (without pagination)."""
    params: dict[str, Any] = {"q": query} if query else {}

    if type_filter:
        params["type"] = type_filter

    params.update(
        {
            name: value
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
            ]
            if value
        }
    )

    return params


def _is_unofficial(item: SearchResult) -> bool:
    """Check if a search result is an unofficial release."""
    return "Unofficial Release" in (item.format or [])


def _is_official(item: SearchResult) -> bool:
    return not _is_unofficial(item)


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
@click.option(
    "--after",
    help="Continuation cursor copied from a previous Next page / Continue scan line",
)
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
@click.option("--limit", type=int, default=DEFAULT_LIMIT, help="Results per page")
@click.option(
    "--page",
    type=int,
    help="Page number (server-side pages only; see --release-type all)",
)
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
    after: str | None,
    artist: str | None,
    barcode: str | None,
    catno: str | None,
    country: str | None,
    format_: str | None,
    genre: str | None,
    label: str | None,
    limit: int,
    page: int | None,
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
        fail(
            ValueError("No search query or filters provided."),
            json_output=json_output,
        )

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

    if needs_release_filter and page is not None:
        fail(
            ValueError(
                "--page is not available while results are filtered client-side "
                f"(--release-type {release_type}). Use the Next page command from "
                "the previous output, or pass --release-type all for server-side "
                "pages."
            ),
            json_output=json_output,
        )
    if not needs_release_filter and after is not None:
        fail(
            ValueError(
                "--after only continues a client-side filtered search; this search "
                "pages server-side. Use --page instead."
            ),
            json_output=json_output,
        )
    if after is not None:
        try:
            parse_cursor(after)
        except ValueError as exc:
            fail(exc, json_output=json_output)

    try:
        client = get_client()
        if needs_release_filter:
            keep = _is_unofficial if release_type == "unofficial" else _is_official
            result = fetch_filtered_page(
                client,
                "/database/search",
                params,
                SearchResult,
                "results",
                limit=limit,
                keep=keep,
                cursor=after,
            )
        else:
            result = fetch_page(
                client,
                "/database/search",
                {**params, "page": page or 1, "per_page": limit},
                SearchResult,
                "results",
            )
    # classify() maps every exception to a coded, recovery-oriented error, so
    # catching broadly is the point.
    except Exception as e:  # noqa: BLE001
        fail(e, "Search", json_output=json_output)

    if json_output:
        dump_page(result)
        return

    footer_cmd = None
    if result.has_next:
        argv = ["search", *filter(None, (type_filter, query))]
        footer_cmd = next_page_cmd(
            argv,
            artist=artist,
            barcode=barcode,
            catno=catno,
            country=country,
            format=format_,
            genre=genre,
            label=label,
            style=style,
            year=year,
            release_type=release_type if release_type != "official" else None,
            limit=limit if limit != DEFAULT_LIMIT else None,
            after=result.next_cursor,
            page=None if result.filtered else result.page + 1,
        )

    filters = {k: str(v) for k, v in params.items() if k not in ("q", "type")}
    if release_type != "official" and type_filter in (None, "release", "master"):
        filters["release-type"] = release_type
    output = format_search_results(
        results=result.items,
        query=query,
        type_filter=type_filter,
        page=result.page,
        total_results=result.total_items,
        next_page_cmd=footer_cmd,
        filters=filters,
        filtered=result.filtered,
        capped=result.capped,
    )
    print(output)
