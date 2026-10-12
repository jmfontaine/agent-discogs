"""Search command handler."""

from __future__ import annotations

from typing import Any

import click
from discogs_sdk import SearchResult

from agent_discogs.client import get_client
from agent_discogs.errors import fail
from agent_discogs.formatting import format_search_results
from agent_discogs.json_output import dump, page_document
from agent_discogs.pagination import (
    DEFAULT_LIMIT,
    fetch_filtered_page,
    fetch_page,
    next_page_cmd,
    paging_error,
    parse_cursor,
    sort_params,
)
from agent_discogs.projections import project_search_result

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
    """Build Discogs API search parameters from CLI args (without pagination).

    Blank values are dropped: they constrain nothing, so they must not count
    as a query or filter either.
    """
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
            if value and value.strip()
        }
    )

    return params


UNOFFICIAL = "Unofficial Release"


def _is_unofficial(item: SearchResult) -> bool:
    """Check if a search result is an unofficial release."""
    return UNOFFICIAL in (item.format or [])


def _is_official(item: SearchResult) -> bool:
    return not _is_unofficial(item)


def _parse_type_and_query(args_list: tuple[str, ...]) -> tuple[str | None, str]:
    """Extract optional type filter and query from positional args.

    If the first word matches a known type, treat it as the type filter.
    Everything else is the query, stripped so a blank one counts as none.
    """
    if args_list and args_list[0].lower() in KNOWN_TYPES:
        return args_list[0].lower(), " ".join(args_list[1:]).strip()
    return None, " ".join(args_list).strip()


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
@click.option(
    "--desc", is_flag=True, default=False, help="With --sort: descending order"
)
@click.option("--format", "format_", help="Filter by format")
@click.option("--genre", help="Filter by genre")
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    default=False,
    help="Output JSON (a compact projection of what the text view shows)",
)
@click.option(
    "--full",
    is_flag=True,
    default=False,
    help="With --json: the complete, normalized record instead of the projection",
)
@click.option("--label", help="Filter by label")
@click.option(
    "--limit", type=int, default=DEFAULT_LIMIT, help="Results per page (1-100)"
)
@click.option(
    "--page",
    type=int,
    help="Page number, from 1 (server-side pages only: --release-type all or "
    "unofficial, artist/label searches)",
)
@click.option(
    "--release-type",
    type=click.Choice(["official", "unofficial", "all"]),
    default="official",
    help="Filter by release type",
)
@click.option(
    "--sort",
    type=click.Choice(["date_added"]),
    help="search release only: server-side order (undocumented by Discogs)",
)
@click.option("--style", help="Filter by style")
@click.option("--year", help="Filter by year")
def search(
    args: tuple[str, ...],
    json_output: bool,
    full: bool,
    after: str | None,
    artist: str | None,
    barcode: str | None,
    catno: str | None,
    country: str | None,
    desc: bool,
    format_: str | None,
    genre: str | None,
    label: str | None,
    limit: int,
    page: int | None,
    release_type: str,
    sort: str | None,
    style: str | None,
    year: str | None,
) -> None:
    """Search Discogs database.

    ARGS is [type] query, where type is one of: release, master, artist, label.
    """
    type_filter, query = _parse_type_and_query(args)

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
    if not params.keys() - {"type"}:
        fail(
            ValueError("No search query or filters provided."),
            json_output=json_output,
        )
    bad_paging = paging_error(limit, page)
    if bad_paging:
        fail(ValueError(bad_paging), json_output=json_output)
    if desc and not sort:
        fail(ValueError("--desc needs --sort date_added."), json_output=json_output)
    if sort and type_filter != "release":
        fail(
            ValueError(
                "--sort works only on release searches: agent-discogs search "
                "release ... --sort date_added. Discogs documents no sort for "
                "search, and date order has only been seen on release results."
            ),
            json_output=json_output,
        )
    # Kept out of `params`, which doubles as the header filters and the
    # footer's flags. A filtered scan keeps the server's order.
    request = {**params, **sort_params(sort, desc)}

    # Release-type filtering only applies to release/master searches.
    # Artist and label results never have format metadata, so filtering
    # is a no-op that wastes bandwidth by overfetching 3x.
    release_typed = type_filter in (None, "release", "master")
    # Discogs filters unofficial releases itself via `format`, so they page
    # server-side with an exact total. Not alongside `--format`: a repeated
    # `format` is ignored and a combined value matches loosely, so that
    # combination keeps the client-side scan. "Official" has no server
    # filter at all.
    server_unofficial = (
        release_typed and release_type == "unofficial" and "format" not in params
    )
    if server_unofficial:
        request["format"] = UNOFFICIAL
    needs_release_filter = (
        release_typed and release_type != "all" and not server_unofficial
    )

    if full and not json_output:
        fail(ValueError("--full requires --json."), json_output=False)
    if needs_release_filter and page is not None:
        # Unofficial without --format pages server-side, so dropping it keeps
        # the filter; for official only `all` pages server-side.
        because, escape = (
            (" with --format", "drop --format")
            if release_type == "unofficial"
            else ("", "pass --release-type all")
        )
        fail(
            ValueError(
                "--page is not available while results are filtered client-side "
                f"(--release-type {release_type}{because}). Use the Next page "
                f"command from the previous output, or {escape} for server-side "
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
                request,
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
                {**request, "page": page or 1, "per_page": limit},
                SearchResult,
                "results",
            )
    # classify() maps every exception to a coded, recovery-oriented error, so
    # catching broadly is the point.
    except Exception as e:  # noqa: BLE001
        fail(e, "Search", json_output=json_output)

    # Rendering is inside the boundary too, so a row the renderers cannot
    # handle is a coded error (an envelope under --json), not a traceback.
    if json_output:
        try:
            document = page_document(result, project_search_result, full=full)
        except Exception as e:  # noqa: BLE001
            fail(e, "Search", json_output=True)
        dump(document)
        return

    filters = {k: str(v) for k, v in params.items() if k not in ("q", "type")}
    footer_cmd = next_page_cmd(
        ["search"],
        result,
        limit=limit,
        positionals=args,  # as typed: re-parses to the same type and query
        **filters,
        release_type=release_type if release_type != "official" else None,
        sort=sort,
        desc=desc,
    )

    if release_type != "official" and type_filter in (None, "release", "master"):
        filters["release-type"] = release_type
    if sort:
        filters["sort"] = sort
        filters["order"] = "desc" if desc else "asc"
    try:
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
    except Exception as e:  # noqa: BLE001
        fail(e, "Search", json_output=False)
    print(output)
