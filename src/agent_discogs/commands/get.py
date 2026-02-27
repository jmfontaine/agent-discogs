"""Get command handler — dispatches by noun to entity-specific functions."""

from __future__ import annotations

import sys
from typing import Any

import click
from discogs_sdk import ArtistRelease, Discogs, MasterVersion

from agent_discogs.client import get_client
from agent_discogs.errors import format_error
from agent_discogs.formatting import (
    format_artist,
    format_artist_releases,
    format_label,
    format_master,
    format_master_versions,
    format_price_guide,
    format_release,
    format_tracklist,
)
from agent_discogs.json_output import dump_entity, dump_list, dump_page
from agent_discogs.pagination import fetch_filtered_page, fetch_page
from agent_discogs.refs import make_ref, parse_ref

# Which entity type each noun expects
NOUN_EXPECTED_TYPE = {
    "artist": "artist",
    "label": "label",
    "master": "master",
    "price": "release",
    "release": "release",
    "releases": "artist",
    "tracklist": "release",
    "versions": "master",
}

GET_NOUNS = [
    "artist",
    "label",
    "master",
    "price",
    "release",
    "releases",
    "tracklist",
    "versions",
]


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

        article = "an" if expected[0] in "aeiou" else "a"
        raise ValueError(
            f"{ref_string} is a {entity_type}, not {article} {expected}. "
            f"Use {article} {expected} ref or ID with 'get {noun}'."
        )

    return entity_type, entity_id


def _get_artist(client: Discogs, entity_id: int, *, json_output: bool) -> None:
    artist = client.artists.get(entity_id)
    if json_output:
        dump_entity(artist)
    else:
        print(format_artist(artist))


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
    page: int,
    limit: int,
    role: str | None,
    json_output: bool,
) -> None:
    artist = client.artists.get(entity_id)
    artist_name = artist.name
    artist_ref = make_ref("artist", entity_id)

    path = f"/artists/{entity_id}/releases"
    params: dict[str, Any] = {}

    if role:
        role_lower = role.lower()
        result = fetch_filtered_page(
            client,
            path,
            params,
            ArtistRelease,
            "releases",
            page,
            limit,
            keep=lambda item: role_lower in (getattr(item, "role", "") or "").lower(),
        )
    else:
        params["page"] = page
        params["per_page"] = limit
        result = fetch_page(client, path, params, ArtistRelease, "releases")

    if json_output:
        dump_page(result)
        return

    next_page_cmd = None
    if result.has_next:
        parts = [f"agent-discogs get releases {artist_ref}"]
        if role:
            parts.append(f"--role {role}")
        parts.append(f"--page {result.page + 1}")
        next_page_cmd = " ".join(parts)

    print(
        format_artist_releases(
            result.items,
            artist_ref,
            artist_name,
            result.page,
            result.total_items,
            next_page_cmd,
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
    page: int,
    limit: int,
    country: str | None,
    format: str | None,
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

    params: dict[str, Any] = {"page": page, "per_page": limit}
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

    next_page_cmd = None
    if result.has_next:
        parts = [f"agent-discogs get versions {master_ref}"]
        if country:
            parts.append(f"--country {country}")
        if format:
            parts.append(f"--format {format}")
        if label:
            parts.append(f"--label {label}")
        parts.append(f"--page {result.page + 1}")
        next_page_cmd = " ".join(parts)

    print(
        format_master_versions(
            result.items,
            master_ref,
            master_title,
            result.page,
            result.total_items,
            next_page_cmd,
        )
    )


def _dispatch(
    noun: str,
    ref: str,
    *,
    page: int,
    limit: int,
    country: str | None,
    format: str | None,
    label: str | None,
    role: str | None,
    verbose: bool,
    json_output: bool,
) -> None:
    """Shared dispatch logic for get, tracks, and price commands."""
    client = get_client()

    try:
        entity_type, entity_id = _resolve_ref(ref, noun)

        if noun == "artist":
            _get_artist(client, entity_id, json_output=json_output)
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
    except Exception as e:
        print(format_error(e, f"{noun.title()} {ref}"), file=sys.stderr)
        sys.exit(1)


@click.command()
@click.argument("noun", type=click.Choice(GET_NOUNS))
@click.argument("ref")
@click.option("--country", help="Filter versions by country")
@click.option("--format", "format_", help="Filter versions by format")
@click.option(
    "--json", "json_output", is_flag=True, default=False, help="Output raw JSON"
)
@click.option("--label", help="Filter versions by label")
@click.option("--limit", type=int, default=5, help="Results per page")
@click.option("--page", type=int, default=1, help="Page number")
@click.option("--role", help="Filter releases by credit role (e.g., Main, Remix)")
@click.option(
    "-v",
    "--verbose",
    is_flag=True,
    default=False,
    help="Show additional details (e.g., release notes)",
)
def get(
    noun: str,
    ref: str,
    json_output: bool,
    country: str | None,
    format_: str | None,
    label: str | None,
    limit: int,
    page: int,
    role: str | None,
    verbose: bool,
) -> None:
    """Get entity details.

    NOUN is the entity type: artist, label, master, price, release, releases, tracklist, versions.
    REF is a typed ref (@r123, @a456) or raw Discogs ID.
    """
    _dispatch(
        noun,
        ref,
        page=page,
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
        page=1,
        limit=5,
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
        page=1,
        limit=5,
        country=None,
        format=None,
        label=None,
        role=None,
        verbose=False,
        json_output=json_output,
    )
