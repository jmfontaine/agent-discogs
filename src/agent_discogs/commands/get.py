"""Get command handler — dispatches by noun to entity-specific functions."""

from __future__ import annotations

from typing import Any

import click
from discogs_sdk import ArtistRelease, Discogs, MasterVersion

from agent_discogs.client import get_client
from agent_discogs.errors import fail
from agent_discogs.formatting import (
    format_artist,
    format_artist_releases,
    format_credits,
    format_identifiers,
    format_label,
    format_master,
    format_master_versions,
    format_price_guide,
    format_release,
    format_tracklist,
)
from agent_discogs.json_output import dump_entity, dump_list, dump_page
from agent_discogs.pagination import (
    DEFAULT_LIMIT,
    fetch_filtered_page,
    fetch_page,
    next_page_cmd,
    parse_cursor,
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

        def _a(word: str) -> str:
            return f"an {word}" if word[0] in "aeiou" else f"a {word}"

        raise ValueError(
            f"{ref_string} is {_a(entity_type)}, not {_a(expected)}. "
            f"Use {_a(expected)} ref or ID with 'get {noun}'."
        )

    return entity_type, entity_id


def _get_artist(client: Discogs, entity_id: int, *, json_output: bool) -> None:
    artist = client.artists.get(entity_id)
    if json_output:
        dump_entity(artist)
    else:
        print(format_artist(artist))


def _get_credits(client: Discogs, entity_id: int, *, json_output: bool) -> None:
    release = client.releases.get(entity_id)
    if json_output:
        dump_list("credits", getattr(release, "extra_artists", None) or [])
    else:
        print(format_credits(release))


def _get_identifiers(client: Discogs, entity_id: int, *, json_output: bool) -> None:
    release = client.releases.get(entity_id)
    if json_output:
        dump_list("identifiers", getattr(release, "identifiers", None) or [])
    else:
        print(format_identifiers(release))


def _get_label(client: Discogs, entity_id: int, *, json_output: bool) -> None:
    label = client.labels.get(entity_id)
    if json_output:
        dump_entity(label)
    else:
        print(format_label(label))


def _get_master(client: Discogs, entity_id: int, *, json_output: bool) -> None:
    master = client.masters.get(entity_id)
    if json_output:
        dump_entity(master)
    else:
        print(format_master(master))


def _get_price(client: Discogs, entity_id: int, *, json_output: bool) -> None:
    release = client.releases.get(entity_id)
    price_suggestions = release.price_suggestions.get()
    marketplace_stats = release.marketplace_stats.get()
    if json_output:
        dump_entity(
            price_suggestions,
            marketplace_stats=marketplace_stats,
        )
    else:
        print(format_price_guide(release, price_suggestions, marketplace_stats))


def _get_release(
    client: Discogs, entity_id: int, *, verbose: bool, json_output: bool
) -> None:
    release = client.releases.get(entity_id)
    if json_output:
        dump_entity(release)
    else:
        print(format_release(release, verbose=verbose))


def _get_releases(
    client: Discogs,
    entity_id: int,
    *,
    page: int | None,
    limit: int,
    after: str | None,
    role: str | None,
    json_output: bool,
) -> None:
    artist = client.artists.get(entity_id)
    artist_name = artist.name
    artist_ref = make_ref("artist", entity_id)

    path = f"/artists/{entity_id}/releases"
    params: dict[str, Any] = {}

    # --page/--after validity is checked in _dispatch before any API call.
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

    if json_output:
        dump_page(result)
        return

    footer_cmd = None
    if result.has_next:
        footer_cmd = next_page_cmd(
            ["get", "releases", artist_ref],
            role=role,
            limit=limit if limit != DEFAULT_LIMIT else None,
            after=result.next_cursor,
            page=None if result.filtered else result.page + 1,
        )

    print(
        format_artist_releases(
            result.items,
            artist_ref,
            artist_name,
            result.page,
            result.total_items,
            footer_cmd,
            filtered=result.filtered,
            capped=result.capped,
        )
    )


def _get_tracklist(client: Discogs, entity_id: int, *, json_output: bool) -> None:
    release = client.releases.get(entity_id)
    if json_output:
        tracklist = getattr(release, "tracklist", None) or []
        dump_list("tracklist", tracklist)
    else:
        print(format_tracklist(release))


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
    json_output: bool,
) -> None:
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

    result = fetch_page(
        client,
        f"/masters/{master_id}/versions",
        params,
        MasterVersion,
        "versions",
    )

    if json_output:
        dump_page(result)
        return

    footer_cmd = None
    if result.has_next:
        footer_cmd = next_page_cmd(
            ["get", "versions", master_ref],
            country=country,
            format=format,
            label=label,
            limit=limit if limit != DEFAULT_LIMIT else None,
            page=result.page + 1,
        )

    print(
        format_master_versions(
            result.items,
            master_ref,
            master_title,
            result.page,
            result.total_items,
            footer_cmd,
        )
    )


def _pagination_flag_error(
    noun: str, *, page: int | None, after: str | None, role: str | None
) -> str | None:
    """Reject --page/--after combinations that make no sense for this noun.

    Checked before any API call so a bad flag never costs a request or gets
    masked by an auth/network failure.
    """
    filtered = noun == "releases" and bool(role)
    if after is not None and not filtered:
        return (
            "--after only continues a client-side filtered listing "
            "(get releases --role). This command pages server-side or is not "
            "paginated; use --page where applicable."
        )
    if filtered and page is not None:
        return (
            "--page is not available while results are filtered client-side "
            "(--role). Use the Next page command from the previous output."
        )
    if after is not None:
        try:
            parse_cursor(after)
        except ValueError as exc:
            return str(exc)
    return None


def _dispatch(
    noun: str,
    ref: str,
    *,
    page: int | None,
    after: str | None,
    limit: int,
    country: str | None,
    format: str | None,  # noqa: A002  # click option name for --format
    label: str | None,
    role: str | None,
    verbose: bool,
    json_output: bool,
) -> None:
    """Shared dispatch logic for get, tracks, and price commands."""
    flag_error = _pagination_flag_error(noun, page=page, after=after, role=role)
    if flag_error:
        fail(ValueError(flag_error), json_output=json_output)

    try:
        client = get_client()
        entity_type, entity_id = _resolve_ref(ref, noun)

        if noun == "artist":
            _get_artist(client, entity_id, json_output=json_output)
        elif noun == "credits":
            _get_credits(client, entity_id, json_output=json_output)
        elif noun == "identifiers":
            _get_identifiers(client, entity_id, json_output=json_output)
        elif noun == "master":
            _get_master(client, entity_id, json_output=json_output)
        elif noun == "label":
            _get_label(client, entity_id, json_output=json_output)
        elif noun == "price":
            _get_price(client, entity_id, json_output=json_output)
        elif noun == "release":
            _get_release(client, entity_id, verbose=verbose, json_output=json_output)
        elif noun == "releases":
            _get_releases(
                client,
                entity_id,
                page=page,
                after=after,
                limit=limit,
                role=role,
                json_output=json_output,
            )
        elif noun == "tracklist":
            _get_tracklist(client, entity_id, json_output=json_output)
        elif noun == "versions":
            _get_versions(
                client,
                entity_type,
                entity_id,
                ref,
                page=page,
                limit=limit,
                country=country,
                format=format,
                label=label,
                json_output=json_output,
            )
    # classify() maps every exception to a coded, recovery-oriented error, so
    # catching broadly is the point.
    except Exception as e:  # noqa: BLE001
        fail(e, f"{noun.title()} {ref}", json_output=json_output)


@click.command()
@click.argument("noun", type=click.Choice(GET_NOUNS))
@click.argument("ref")
@click.option(
    "--after",
    help="Continuation cursor copied from a previous Next page / Continue scan line",
)
@click.option("--country", help="Filter versions by country")
@click.option("--format", "format_", help="Filter versions by format")
@click.option(
    "--json", "json_output", is_flag=True, default=False, help="Output raw JSON"
)
@click.option("--label", help="Filter versions by label")
@click.option("--limit", type=int, default=DEFAULT_LIMIT, help="Results per page")
@click.option("--page", type=int, help="Page number (server-side pages only)")
@click.option("--role", help="Filter releases by credit role (e.g., Main, Remix)")
@click.option(
    "-v",
    "--verbose",
    is_flag=True,
    default=False,
    help="Show additional details (e.g., release notes, entity refs)",
)
def get(
    noun: str,
    ref: str,
    json_output: bool,
    after: str | None,
    country: str | None,
    format_: str | None,
    label: str | None,
    limit: int,
    page: int | None,
    role: str | None,
    verbose: bool,
) -> None:
    """Get entity details.

    NOUN is the entity type: artist, credits, identifiers (alias: ids), label,
    master, price, release, releases, tracklist, versions.
    REF is a typed ref (@r123, @a456) or raw Discogs ID.
    """
    _dispatch(
        NOUN_ALIASES.get(noun, noun),
        ref,
        page=page,
        after=after,
        limit=limit,
        country=country,
        format=format_,
        label=label,
        role=role,
        verbose=verbose,
        json_output=json_output,
    )


@click.command()
@click.argument("ref")
@click.option(
    "--json", "json_output", is_flag=True, default=False, help="Output raw JSON"
)
def tracks(ref: str, json_output: bool) -> None:
    """Shortcut for: get tracklist <ref>."""
    _dispatch(
        "tracklist",
        ref,
        page=None,
        after=None,
        limit=DEFAULT_LIMIT,
        country=None,
        format=None,
        label=None,
        role=None,
        verbose=False,
        json_output=json_output,
    )


@click.command()
@click.argument("ref")
@click.option(
    "--json", "json_output", is_flag=True, default=False, help="Output raw JSON"
)
def price(ref: str, json_output: bool) -> None:
    """Shortcut for: get price <ref>."""
    _dispatch(
        "price",
        ref,
        page=None,
        after=None,
        limit=DEFAULT_LIMIT,
        country=None,
        format=None,
        label=None,
        role=None,
        verbose=False,
        json_output=json_output,
    )
