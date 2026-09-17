"""Get command handler — dispatches by noun to entity-specific functions."""

from __future__ import annotations

import sys
from typing import Any

import click
from discogs_sdk import ArtistRelease, Discogs, LabelRelease, MasterVersion

from agent_discogs.client import get_client
from agent_discogs.errors import error_document, fail, format_error
from agent_discogs.formatting import (
    format_artist,
    format_artist_releases,
    format_credits,
    format_identifiers,
    format_label,
    format_label_releases,
    format_master,
    format_master_versions,
    format_price_guide,
    format_release,
    format_tracklist,
)
from agent_discogs.json_output import Mode, dump, raw
from agent_discogs.pagination import (
    DEFAULT_LIMIT,
    fetch_filtered_page,
    fetch_page,
    next_page_cmd,
    parse_cursor,
)
from agent_discogs.projections import (
    drop_empty,
    project_artist,
    project_artist_release,
    project_credits,
    project_identifiers,
    project_label,
    project_label_release,
    project_master,
    project_master_version,
    project_price,
    project_release,
    project_tracklist,
)
from agent_discogs.refs import make_ref, parse_ref

# Which entity type each noun expects
NOUN_EXPECTED_TYPE = {
    "artist": "artist",
    "credits": "release",
    "identifiers": "release",
    "label": "label",
    "master": "master",
    "price": "release",
    "release": "release",
    "releases": "artist",
    "tracklist": "release",
    "versions": "master",
}

NOUN_ALIASES = {"ids": "identifiers"}

GET_NOUNS = [*NOUN_EXPECTED_TYPE, *NOUN_ALIASES]


def _resolve_ref(ref_string: str, noun: str) -> tuple[str, int]:
    """Parse a ref or raw ID, validating type against noun expectation.

    Returns (entity_type, entity_id).
    Raises ValueError with helpful message on mismatch.

    When a raw numeric ID is given, parse_ref returns type "unknown".
    We skip type validation in that case and trust the noun to determine
    the entity type (e.g. "get release 367113" treats 367113 as a release).
    This accommodates AI agents that pass raw Discogs IDs without our
    typed-prefix convention.
    """
    entity_type, entity_id = parse_ref(ref_string)

    expected = NOUN_EXPECTED_TYPE.get(noun)
    if entity_type == "unknown" or expected is None:
        return entity_type, entity_id

    if entity_type != expected:
        # Smart resolution: versions with a release ref
        if noun == "versions" and entity_type == "release":
            return entity_type, entity_id
        # A label has a catalogue too: `get releases @l647`.
        if noun == "releases" and entity_type == "label":
            return entity_type, entity_id

        def _a(word: str) -> str:
            return f"an {word}" if word[0] in "aeiou" else f"a {word}"

        raise ValueError(
            f"{ref_string} is {_a(entity_type)}, not {_a(expected)}. "
            f"Use {_a(expected)} ref or ID with 'get {noun}'."
        )

    return entity_type, entity_id


def _get_artist(client: Discogs, entity_id: int, mode: Mode) -> Any:
    artist = client.artists.get(entity_id)
    if mode.json:
        return mode.entity(artist, project_artist)
    return format_artist(artist)


def _get_credits(client: Discogs, entity_id: int, mode: Mode) -> Any:
    release = client.releases.get(entity_id)
    if mode.full:
        return {"credits": [c.model_dump() for c in release.extra_artists or []]}
    if mode.json:
        return drop_empty(
            {"ref": make_ref("release", entity_id), "credits": project_credits(release)}
        )
    return format_credits(release)


def _get_identifiers(client: Discogs, entity_id: int, mode: Mode) -> Any:
    release = client.releases.get(entity_id)
    if mode.full:
        return {"identifiers": [i.model_dump() for i in release.identifiers or []]}
    if mode.json:
        return drop_empty(
            {
                "ref": make_ref("release", entity_id),
                "identifiers": project_identifiers(release),
            }
        )
    return format_identifiers(release)


def _get_label(client: Discogs, entity_id: int, mode: Mode) -> Any:
    label = client.labels.get(entity_id)
    if mode.json:
        return mode.entity(label, project_label)
    return format_label(label)


def _get_master(client: Discogs, entity_id: int, mode: Mode) -> Any:
    master = client.masters.get(entity_id)
    if mode.json:
        return mode.entity(master, project_master)
    return format_master(master)


def _get_price(client: Discogs, entity_id: int, mode: Mode) -> Any:
    release = client.releases.get(entity_id)
    price_suggestions = release.price_suggestions.get()
    marketplace_stats = release.marketplace_stats.get()
    if mode.full:
        return {**raw(price_suggestions), "marketplace_stats": raw(marketplace_stats)}
    if mode.json:
        return project_price(release, price_suggestions, marketplace_stats)
    return format_price_guide(release, price_suggestions, marketplace_stats)


def _get_release(
    client: Discogs, entity_id: int, *, verbose: bool, compact: bool, mode: Mode
) -> Any:
    release = client.releases.get(entity_id)
    if mode.json:
        return mode.entity(
            release, lambda r: project_release(r, verbose=verbose, compact=compact)
        )
    return format_release(release, verbose=verbose, compact=compact)


def _sort_params(sort: str | None, desc: bool) -> dict[str, str]:
    """Server-side ordering; `--desc` only means something with `--sort`."""
    if not sort:
        return {}
    return {"sort": sort, "sort_order": "desc" if desc else "asc"}


def _get_label_releases(
    client: Discogs,
    entity_id: int,
    *,
    page: int | None,
    limit: int,
    after: str | None,
    year: str | None,
    mode: Mode,
) -> Any:
    """A label's catalogue. The API has no filters or sorting here, so `--year`
    is a client-side scan with cursor continuation (like `--role`)."""
    label = client.labels.get(entity_id)
    label_ref = make_ref("label", entity_id)
    path = f"/labels/{entity_id}/releases"

    if year:
        result = fetch_filtered_page(
            client,
            path,
            {},
            LabelRelease,
            "releases",
            limit=limit,
            keep=lambda item: str(getattr(item, "year", None) or "") == year,
            cursor=after,
        )
    else:
        result = fetch_page(
            client,
            path,
            {"page": page or 1, "per_page": limit},
            LabelRelease,
            "releases",
        )

    if mode.json:
        return mode.page(result, project_label_release)

    footer_cmd = None
    if result.has_next:
        footer_cmd = next_page_cmd(
            ["get", "releases", label_ref],
            year=year,
            limit=limit if limit != DEFAULT_LIMIT else None,
            after=result.next_cursor,
            page=None if result.filtered else result.page + 1,
        )
    return format_label_releases(
        result.items,
        label_ref,
        label.name,
        result.page,
        result.total_items,
        footer_cmd,
        filtered=result.filtered,
        capped=result.capped,
    )


def _get_releases(
    client: Discogs,
    entity_type: str,
    entity_id: int,
    *,
    page: int | None,
    limit: int,
    after: str | None,
    role: str | None,
    year: str | None,
    sort: str | None,
    desc: bool,
    mode: Mode,
) -> Any:
    if entity_type == "label":
        return _get_label_releases(
            client,
            entity_id,
            page=page,
            limit=limit,
            after=after,
            year=year,
            mode=mode,
        )

    artist = client.artists.get(entity_id)
    artist_name = artist.name
    artist_ref = make_ref("artist", entity_id)

    path = f"/artists/{entity_id}/releases"
    params: dict[str, Any] = _sort_params(sort, desc)

    # Flag validity is checked in _dispatch before any API call.
    if role:
        role_lower = role.lower()
        result = fetch_filtered_page(
            client,
            path,
            params,
            ArtistRelease,
            "releases",
            limit=limit,
            keep=lambda item: role_lower in (getattr(item, "role", "") or "").lower(),
            cursor=after,
        )
    else:
        params["page"] = page or 1
        params["per_page"] = limit
        result = fetch_page(client, path, params, ArtistRelease, "releases")

    if mode.json:
        return mode.page(result, project_artist_release)

    footer_cmd = None
    if result.has_next:
        footer_cmd = next_page_cmd(
            ["get", "releases", artist_ref],
            role=role,
            sort=sort,
            desc=desc,
            limit=limit if limit != DEFAULT_LIMIT else None,
            after=result.next_cursor,
            page=None if result.filtered else result.page + 1,
        )

    return format_artist_releases(
        result.items,
        artist_ref,
        artist_name,
        result.page,
        result.total_items,
        footer_cmd,
        filtered=result.filtered,
        capped=result.capped,
    )


def _get_tracklist(client: Discogs, entity_id: int, mode: Mode) -> Any:
    release = client.releases.get(entity_id)
    if mode.full:
        return {"tracklist": [t.model_dump() for t in release.tracklist or []]}
    if mode.json:
        return project_tracklist(release)
    return format_tracklist(release)


def _get_versions(
    client: Discogs,
    entity_type: str,
    entity_id: int,
    ref_string: str,
    *,
    page: int | None,
    limit: int,
    country: str | None,
    format: str | None,  # noqa: A002  # click option name for --format
    label: str | None,
    year: str | None,
    sort: str | None,
    desc: bool,
    mode: Mode,
) -> Any:
    master_id = entity_id
    master_title = ""

    # Smart resolution: release ref → look up its master_id
    if entity_type == "release":
        release = client.releases.get(entity_id)
        if not release.master_id:
            raise ValueError(
                f"{ref_string} is a release with no master. "
                "This release isn't linked to a master release. "
                "Try searching for the master directly: "
                f'agent-discogs search master "{release.title}"'
            )
        master_id = release.master_id
        master_title = release.title
    else:
        master = client.masters.get(master_id)
        master_title = master.title

    master_ref = make_ref("master", master_id)

    params: dict[str, Any] = {"page": page or 1, "per_page": limit}
    if country:
        params["country"] = country
    if format:
        params["format"] = format
    if label:
        params["label"] = label
    if year:
        params["released"] = year  # the API calls the year filter `released`
    params.update(_sort_params(sort, desc))

    result = fetch_page(
        client,
        f"/masters/{master_id}/versions",
        params,
        MasterVersion,
        "versions",
    )

    if mode.json:
        return mode.page(result, project_master_version)

    footer_cmd = None
    if result.has_next:
        footer_cmd = next_page_cmd(
            ["get", "versions", master_ref],
            country=country,
            format=format,
            label=label,
            year=year,
            sort=sort,
            desc=desc,
            limit=limit if limit != DEFAULT_LIMIT else None,
            page=result.page + 1,
        )

    return format_master_versions(
        result.items,
        master_ref,
        master_title,
        result.page,
        result.total_items,
        footer_cmd,
    )


SORT_KEYS: dict[str, tuple[str, ...]] = {
    "releases": ("year", "title", "format"),
    "versions": ("released", "title", "format", "label", "catno", "country"),
}


def _flag_error(
    noun: str,
    refs: tuple[str, ...],
    *,
    page: int | None,
    after: str | None,
    role: str | None,
    year: str | None,
    sort: str | None,
    desc: bool,
) -> str | None:
    """Reject flag combinations that make no sense for this noun.

    Checked before any API call so a bad flag never costs a request or gets
    masked by an auth/network failure. Client-side filtered listings (which
    page by cursor, not by number) are `releases --role` on an artist and
    `releases --year` on a label.
    """
    label_refs = noun == "releases" and any(r.startswith("@l") for r in refs)
    artist_refs = noun == "releases" and not all(r.startswith("@l") for r in refs)
    filtered = noun == "releases" and (bool(role) or (label_refs and bool(year)))
    if after is not None and not filtered:
        return (
            "--after only continues a client-side filtered listing "
            "(get releases --role, or get releases @l... --year). This command "
            "pages server-side or is not paginated; use --page where applicable."
        )
    if filtered and page is not None:
        return (
            "--page is not available while results are filtered client-side "
            "(--role, or --year on a label catalogue). Use the Next page "
            "command from the previous output."
        )
    if after is not None:
        try:
            parse_cursor(after)
        except ValueError as exc:
            return str(exc)
    if year and noun not in ("versions", "releases"):
        return "--year applies to 'get versions' and 'get releases @l...' only."
    if year and artist_refs:
        return (
            "--year filters versions and label catalogues; for an artist "
            "discography use --sort year (optionally --desc) and page."
        )
    if (sort or desc) and noun not in SORT_KEYS:
        return "--sort/--desc apply to 'get releases' and 'get versions' only."
    if desc and not sort:
        return "--desc needs --sort <key>."
    if sort and sort not in SORT_KEYS.get(noun, ()):
        keys = ", ".join(SORT_KEYS[noun])
        return f"--sort {sort!r} is not valid for 'get {noun}'. Keys: {keys}."
    if label_refs and (role or sort):
        return (
            "Label catalogues cannot be role-filtered or sorted: the Discogs API "
            "offers no --role or --sort for /labels/{id}/releases. Use --year "
            "(client-side), page with --page, or search: "
            "agent-discogs search release --label <name>."
        )
    return None


def _mode(json_output: bool, full: bool) -> Mode:
    """`--full` is a JSON shape switch, so it needs `--json`; say so up front."""
    if full and not json_output:
        fail(ValueError("--full requires --json."), json_output=False)
    return Mode(json=json_output, full=full)


MAX_REFS = 10


def _run_one(
    client: Discogs,
    noun: str,
    ref: str,
    *,
    page: int | None,
    after: str | None,
    limit: int,
    country: str | None,
    format: str | None,  # noqa: A002  # click option name for --format
    label: str | None,
    year: str | None,
    sort: str | None,
    desc: bool,
    role: str | None,
    verbose: bool,
    compact: bool,
    mode: Mode,
) -> Any:
    """One noun for one ref: text, or the JSON document."""
    entity_type, entity_id = _resolve_ref(ref, noun)

    if noun == "artist":
        return _get_artist(client, entity_id, mode)
    if noun == "credits":
        return _get_credits(client, entity_id, mode)
    if noun == "identifiers":
        return _get_identifiers(client, entity_id, mode)
    if noun == "master":
        return _get_master(client, entity_id, mode)
    if noun == "label":
        return _get_label(client, entity_id, mode)
    if noun == "price":
        return _get_price(client, entity_id, mode)
    if noun == "release":
        return _get_release(
            client, entity_id, verbose=verbose, compact=compact, mode=mode
        )
    if noun == "releases":
        return _get_releases(
            client,
            entity_type,
            entity_id,
            page=page,
            after=after,
            limit=limit,
            role=role,
            year=year,
            sort=sort,
            desc=desc,
            mode=mode,
        )
    if noun == "tracklist":
        return _get_tracklist(client, entity_id, mode)
    return _get_versions(
        client,
        entity_type,
        entity_id,
        ref,
        page=page,
        limit=limit,
        country=country,
        format=format,
        label=label,
        year=year,
        sort=sort,
        desc=desc,
        mode=mode,
    )


def _dispatch(
    noun: str,
    refs: tuple[str, ...],
    *,
    page: int | None,
    after: str | None,
    limit: int,
    country: str | None,
    format: str | None,  # noqa: A002  # click option name for --format
    label: str | None,
    year: str | None,
    sort: str | None,
    desc: bool,
    role: str | None,
    verbose: bool,
    compact: bool,
    mode: Mode,
) -> None:
    """Shared dispatch for get, tracks, and price: one or many refs.

    Several refs run in sequence, one API round trip each, so "compare these
    pressings" is one command. Output is one block per ref in ref order,
    blank-line separated, on stdout: a failing ref contributes its `✗` error
    block and the rest still run; JSON becomes a list with `{"ref", "error"}`
    items for failures. Exit 1 if any ref failed. With one ref the shape is
    unchanged, including the single-error path (`fail()`: stderr text or a
    JSON envelope).
    """
    flag_error = _flag_error(
        noun,
        refs,
        page=page,
        after=after,
        role=role,
        year=year,
        sort=sort,
        desc=desc,
    )
    if flag_error:
        fail(ValueError(flag_error), json_output=mode.json)
    if len(refs) > MAX_REFS:
        fail(
            ValueError(f"At most {MAX_REFS} refs per command ({len(refs)} given)."),
            json_output=mode.json,
        )

    try:
        client = get_client()
    # classify() maps every exception to a coded, recovery-oriented error, so
    # catching broadly is the point.
    except Exception as e:  # noqa: BLE001
        fail(e, json_output=mode.json)

    single = len(refs) == 1
    blocks: list[Any] = []  # one per ref, in order: text/document or error
    failed = False
    for ref in refs:
        context = f"{noun.title()} {ref}"
        try:
            blocks.append(
                _run_one(
                    client,
                    noun,
                    ref,
                    page=page,
                    after=after,
                    limit=limit,
                    country=country,
                    format=format,
                    label=label,
                    year=year,
                    sort=sort,
                    desc=desc,
                    role=role,
                    verbose=verbose,
                    compact=compact,
                    mode=mode,
                )
            )
        except Exception as e:  # noqa: BLE001
            if single:
                fail(e, context, json_output=mode.json)
            failed = True
            if mode.json:
                blocks.append({"ref": ref, "error": error_document(e, context)})
            else:
                blocks.append(format_error(e, context))

    if mode.json:
        dump(blocks[0] if single else blocks)
    else:
        print("\n\n".join(blocks))
    if failed:
        sys.exit(1)


_JSON_OPTIONS = [
    click.option(
        "--json",
        "json_output",
        is_flag=True,
        default=False,
        help="Output JSON (a compact projection of what the text view shows)",
    ),
    click.option(
        "--full",
        is_flag=True,
        default=False,
        help="With --json: the raw SDK model instead of the projection",
    ),
]


def _json_options(command: Any) -> Any:
    for option in reversed(_JSON_OPTIONS):
        command = option(command)
    return command


@click.command()
@click.argument("noun", type=click.Choice(GET_NOUNS))
@click.argument("refs", nargs=-1, required=True, metavar="REF...")
@click.option(
    "--after",
    help="Continuation cursor copied from a previous Next page / Continue scan line",
)
@click.option(
    "-c",
    "--compact",
    is_flag=True,
    default=False,
    help="release: replace the tracklist with a one-line Tracks: summary",
)
@click.option("--country", help="Filter versions by country")
@click.option(
    "--desc", is_flag=True, default=False, help="With --sort: descending order"
)
@click.option("--format", "format_", help="Filter versions by format")
@_json_options
@click.option("--label", help="Filter versions by label")
@click.option("--limit", type=int, default=DEFAULT_LIMIT, help="Results per page")
@click.option("--page", type=int, help="Page number (server-side pages only)")
@click.option("--role", help="Filter releases by credit role (e.g., Main, Remix)")
@click.option(
    "--sort",
    help=(
        "Server-side order. releases: year|title|format; "
        "versions: released|title|format|label|catno|country"
    ),
)
@click.option(
    "-v",
    "--verbose",
    is_flag=True,
    default=False,
    help="release: also print notes, credits, and identifiers",
)
@click.option("--year", help="Filter versions by release year")
def get(
    noun: str,
    refs: tuple[str, ...],
    json_output: bool,
    full: bool,
    after: str | None,
    compact: bool,
    country: str | None,
    desc: bool,
    format_: str | None,
    label: str | None,
    limit: int,
    page: int | None,
    role: str | None,
    sort: str | None,
    verbose: bool,
    year: str | None,
) -> None:
    """Get entity details for one or more refs.

    NOUN is the entity type: artist, credits, identifiers (alias: ids), label,
    master, price, release, releases, tracklist, versions.
    REF is a typed ref (@r123, @a456) or raw Discogs ID. Several refs run in
    sequence (at most 10).
    """
    _dispatch(
        NOUN_ALIASES.get(noun, noun),
        refs,
        page=page,
        after=after,
        limit=limit,
        country=country,
        format=format_,
        label=label,
        year=year,
        sort=sort,
        desc=desc,
        role=role,
        verbose=verbose,
        compact=compact,
        mode=_mode(json_output, full),
    )


def _shortcut(noun: str, refs: tuple[str, ...], json_output: bool, full: bool) -> None:
    _dispatch(
        noun,
        refs,
        page=None,
        after=None,
        limit=DEFAULT_LIMIT,
        country=None,
        format=None,
        label=None,
        year=None,
        sort=None,
        desc=False,
        role=None,
        verbose=False,
        compact=False,
        mode=_mode(json_output, full),
    )


@click.command()
@click.argument("refs", nargs=-1, required=True, metavar="REF...")
@_json_options
def tracks(refs: tuple[str, ...], json_output: bool, full: bool) -> None:
    """Shortcut for: get tracklist REF..."""
    _shortcut("tracklist", refs, json_output, full)


@click.command()
@click.argument("refs", nargs=-1, required=True, metavar="REF...")
@_json_options
def price(refs: tuple[str, ...], json_output: bool, full: bool) -> None:
    """Shortcut for: get price REF..."""
    _shortcut("price", refs, json_output, full)
