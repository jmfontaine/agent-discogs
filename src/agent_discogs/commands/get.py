"""Get command handler — dispatches by noun to entity-specific functions."""

from __future__ import annotations

import functools
import math
import sys
from decimal import Decimal
from typing import Any, get_args

import click
from click.core import ParameterSource
from discogs_sdk import CacheMissError, CurrencyCode, Discogs, NotFoundError

from agent_discogs import account, style, trace
from agent_discogs.account import UserRequiredError, require_user
from agent_discogs.client import get_client
from agent_discogs.collection import (
    ALL_FOLDER,
    copies,
    custom_fields,
    folder_id,
    folder_names,
    mark_rows,
    mark_rows_with,
    master_copies,
    ownership_of,
    record_filter,
    records,
    stat_copies,
    stat_wanted,
    valuation,
)
from agent_discogs.config import RELEASE_TYPES, UNOFFICIAL
from agent_discogs.errors import classify, error_document, fail, format_error
from agent_discogs.formatting import (
    format_artist,
    format_artist_releases,
    format_collection,
    format_collection_value,
    format_credits,
    format_identifiers,
    format_inventory,
    format_label,
    format_label_releases,
    format_list,
    format_lists,
    format_master,
    format_master_versions,
    format_owned,
    format_owned_master,
    format_price_guide,
    format_release,
    format_stats,
    format_tracklist,
    format_wantlist,
    iter_tracks,
    known_year,
)
from agent_discogs.inventory import GRADES, Filter, grade, read_wantlist, release_id
from agent_discogs.json_output import Mode, dump, full_dump
from agent_discogs.pagination import (
    DEFAULT_LIMIT,
    DropRepeats,
    PageResult,
    ScanStop,
    fetch_filtered_page,
    fetch_page,
    max_requests_error,
    next_page_cmd,
    paging_error,
    parse_cursor,
    sort_params,
    unscanned,
    without_repeats,
)
from agent_discogs.projections import (
    drop_empty,
    project_artist,
    project_artist_release,
    project_credits,
    project_identifiers,
    project_label,
    project_label_release,
    project_list,
    project_list_summary,
    project_listing,
    project_market,
    project_master,
    project_master_version,
    project_price,
    project_record,
    project_release,
    project_stats,
    project_tracklist,
    project_want,
)
from agent_discogs.refs import make_ref, parse_ref
from agent_discogs.wantlist import ForSale, market_of

# Which entity type each noun expects
NOUN_EXPECTED_TYPE = {
    "artist": "artist",
    "credits": "release",
    "identifiers": "release",
    "label": "label",
    "master": "master",
    "owned": "release",
    "price": "release",
    "release": "release",
    "releases": "artist",
    "stats": "release",
    "tracklist": "release",
    "versions": "master",
}

NOUN_ALIASES = {"ids": "identifiers"}

# Nouns that take another entity type besides the one they expect, named in
# the error for any other: `get owned @m...` checks any pressing of a master.
NOUN_ALSO_ACCEPTS = {"owned": "master"}

# Nouns whose ref is a bare Discogs id of something with no ref prefix.
ID_NOUNS = ("list",)

# Nouns that take a Discogs username in place of a ref.
USERNAME_NOUNS = ("inventory",)

# Nouns about the user behind the credentials, which take no ref.
REFLESS_NOUNS = ("collection", "lists", "wantlist")

GET_NOUNS = [
    *NOUN_EXPECTED_TYPE,
    *ID_NOUNS,
    *USERNAME_NOUNS,
    *REFLESS_NOUNS,
    *NOUN_ALIASES,
]

# The currencies Discogs prices the Marketplace in (`curr_abbr`).
CURRENCIES: tuple[CurrencyCode, ...] = get_args(CurrencyCode)


def _currency(code: str) -> CurrencyCode | None:
    """The currency `code` names, in any case, or None for an unknown one."""
    return next((c for c in CURRENCIES if c == code.upper()), None)


def _complete_currency(
    _ctx: click.Context, _param: click.Parameter, incomplete: str
) -> list[str]:
    """Tab offers the currency codes, uppercase, matching what was typed in
    any case."""
    return [c for c in CURRENCIES if c.startswith(incomplete.upper())]


def _complete_grade(
    _ctx: click.Context, _param: click.Parameter, incomplete: str
) -> list[str]:
    """Tab offers the grades' short codes, best first, matching what was
    typed in any case."""
    return [c for c in GRADES.values() if c.startswith(incomplete.upper())]


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
    if noun in ID_NOUNS:
        if not ref_string.isdecimal():
            raise ValueError(
                f"get {noun} takes a {noun} id, a plain number such as 123 "
                f"({ref_string!r} given): {noun}s have no ref prefix."
            )
        return noun, int(ref_string)

    entity_type, entity_id = parse_ref(ref_string)

    expected = NOUN_EXPECTED_TYPE.get(noun)
    if entity_type == "unknown" or expected is None:
        return entity_type, entity_id

    also = NOUN_ALSO_ACCEPTS.get(noun)
    if entity_type not in (expected, also):
        # Smart resolution: versions with a release ref
        if noun == "versions" and entity_type == "release":
            return entity_type, entity_id
        # A label has a catalogue too: `get releases @l647`.
        if noun == "releases" and entity_type == "label":
            return entity_type, entity_id

        def _a(word: str) -> str:
            return f"an {word}" if word[0] in "aeiou" else f"a {word}"

        accepted = _a(expected) + ("" if also is None else f" or {_a(also)}")
        kinds = expected + ("" if also is None else f" or {also}")
        raise ValueError(
            f"{ref_string} is {_a(entity_type)}, not {accepted}. "
            f"Use {_a(kinds)} ref or ID with 'get {noun}'."
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
        return {
            "credits": [full_dump(c) for c in release.extra_artists or []],
            "track_credits": [
                {
                    "position": track.position,
                    "title": track.title,
                    "extra_artists": [full_dump(c) for c in track.extra_artists],
                }
                for track in iter_tracks(release.tracklist)
                if track.extra_artists
            ],
        }
    if mode.json:
        return drop_empty(
            {"ref": make_ref("release", entity_id), "credits": project_credits(release)}
        )
    return format_credits(release)


def _get_identifiers(client: Discogs, entity_id: int, mode: Mode) -> Any:
    release = client.releases.get(entity_id)
    if mode.full:
        return {"identifiers": [full_dump(i) for i in release.identifiers or []]}
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


def _get_owned(client: Discogs, entity_type: str, entity_id: int, mode: Mode) -> Any:
    """The user's copies of one release, with their folder names, or of any
    release of a master. `--full` gives the collection instances, which name
    their folders by id, so it never asks for the folder list."""
    if entity_type == "master":
        return _get_owned_master(client, entity_id, mode)
    found = copies(client, entity_id, folders=not mode.full)
    if mode.full:
        return {"instances": [full_dump(i) for i in found.instances]}
    if mode.json:
        return drop_empty(
            {
                "ref": make_ref("release", entity_id),
                "owned": found.count,
                "folders": found.folders,
            }
        )
    return format_owned(entity_id, found)


def _get_owned_master(client: Discogs, master_id: int, mode: Mode) -> Any:
    """The user's copies of any release of a master, from one read of the
    whole collection; every document carries the read's `collection` object,
    and `owned` stays `null` when the part read holds no copy."""
    found = master_copies(client, master_id, folders=not mode.full)
    collection = found.ownership.document()
    if mode.full:
        return {
            "instances": [full_dump(r) for r in found.records],
            "collection": collection,
        }
    if mode.json:
        document: dict[str, Any] = {
            "ref": make_ref("master", master_id),
            "owned": found.owned,
        }
        if found.releases:
            document["releases"] = [
                drop_empty(
                    {
                        "ref": make_ref("release", release_id),
                        "owned": count,
                        "folders": folders,
                    }
                )
                for release_id, count, folders in found.releases
            ]
        document["collection"] = collection
        return document
    return format_owned_master(master_id, found)


def _get_list(client: Discogs, entity_id: int, mode: Mode) -> Any:
    """One list, every item in one response."""
    found = client.lists.get(entity_id)
    if mode.json:
        return mode.entity(found, project_list)
    return format_list(found)


def _get_lists(client: Discogs, *, page: int | None, limit: int, mode: Mode) -> Any:
    """The user's own lists, private ones included, server-side pages."""
    # The username, before the listing, as for the wantlist.
    user = account.user(client)
    result = fetch_page(user.lists.list, page=page or 1, per_page=limit)
    if mode.json:
        return mode.page(result, project_list_summary)
    return format_lists(
        result.items,
        account.username(client),
        result.page,
        result.total_items,
        next_page_cmd(["get", "lists"], result, limit=limit),
    )


def _page_json(
    result: PageResult, project: Any, *, owned: bool, wanted: bool, mode: Mode
) -> dict[str, Any]:
    """A `get versions` or `get releases` JSON document, each release row
    marked under `--owned` with the copies and under `--wanted` with the want
    its own stats count."""
    document = mode.page(result, project)
    if owned:
        mark_rows_with(document, result.items, "owned", stat_copies)
    if wanted:
        mark_rows_with(document, result.items, "wanted", stat_wanted)
    return document


def _get_price(client: Discogs, entity_id: int, mode: Mode) -> Any:
    release = client.releases.get(entity_id)
    price_suggestions = release.price_suggestions.get()
    marketplace_stats = release.marketplace_stats.get()
    if mode.full:
        return {
            **full_dump(price_suggestions),
            "marketplace_stats": full_dump(marketplace_stats),
        }
    if mode.json:
        return project_price(release, price_suggestions, marketplace_stats)
    return format_price_guide(release, price_suggestions, marketplace_stats)


def _get_release(
    client: Discogs,
    entity_id: int,
    *,
    verbose: bool,
    compact: bool,
    master_year: bool,
    mode: Mode,
) -> Any:
    release = client.releases.get(entity_id)
    # The release is read first: one that fails sends no master request.
    master_id = release.master_id if master_year else None
    year: int | str | None = None
    miss: CacheMissError | NotFoundError | None = None
    if master_id:
        try:
            # A lazy proxy: reading a field sends the one request, the one
            # `get master` sends. A miss or a deleted master leaves the release
            # whole; any other failure fails the ref.
            year = known_year(client.masters.get(master_id).year)
        except (CacheMissError, NotFoundError) as exc:
            miss = exc
    context = f"Master {make_ref('master', master_id)}" if master_id else None
    error = error_document(miss, context) if miss else None
    if mode.full:
        extra = {"master_year": year, "master_year_error": error}
        return {**full_dump(release), **drop_empty(extra)}
    if mode.json:
        return mode.entity(
            release,
            lambda r: project_release(
                r,
                verbose=verbose,
                compact=compact,
                master_year=year,
                master_year_error=error,
            ),
        )
    return format_release(
        release,
        verbose=verbose,
        compact=compact,
        master_year=year,
        master_error=classify(miss, context).message if miss else None,
    )


def _get_stats(
    client: Discogs, entity_id: int, *, currency: CurrencyCode | None, mode: Mode
) -> Any:
    """A release's community numbers, from its record, and its Marketplace
    numbers, from its stats, which name the currency Discogs priced them in
    (`currency`, sent as `curr_abbr`, else the account's). Two requests,
    the release first: one that does not exist sends no stats request, and
    a failure of either fails the ref rather than print half the numbers."""
    release = client.releases.get(entity_id)
    # Reading a field of a lazy proxy sends its request: the release first.
    _ = release.title
    stats = release.marketplace_stats.get(curr_abbr=currency)
    market = market_of(stats)
    if mode.full:
        return {"release": full_dump(release), "marketplace_stats": full_dump(stats)}
    if mode.json:
        return project_stats(release, market, blocked=bool(stats.blocked_from_sale))
    return format_stats(release, market)


def _get_label_releases(
    client: Discogs,
    entity_id: int,
    *,
    page: int | None,
    limit: int,
    after: str | None,
    year: str | None,
    max_requests: int | None,
    owned: bool,
    wanted: bool,
    mode: Mode,
) -> Any:
    """A label's catalogue. The API has no filters or sorting here, so `--year`
    is a client-side scan with cursor continuation (like `--role`). Discogs
    sends some rows twice in a row: a row equal to the one before it is
    dropped, on the page or as the scan reads it, and counted."""
    # A lazy proxy: reading .name sends the request, so only the text header
    # pays for it. It is read before the scan so that, under --max-requests,
    # the lookup the output needs counts first.
    label = client.labels.get(entity_id)
    label_name = "" if mode.json else label.name
    label_ref = make_ref("label", entity_id)

    if year:
        keep = DropRepeats(lambda item: str(getattr(item, "year", None) or "") == year)
        result = fetch_filtered_page(
            label.releases.list,
            limit=limit,
            keep=keep,
            cursor=after,
            max_requests=max_requests,
        )
        result.duplicates_dropped = keep.dropped
    else:
        result = without_repeats(
            fetch_page(label.releases.list, page=page or 1, per_page=limit)
        )

    if mode.json:
        return _page_json(
            result, project_label_release, owned=owned, wanted=wanted, mode=mode
        )

    footer_cmd = next_page_cmd(
        ["get", "releases", label_ref],
        result,
        limit=limit,
        year=year,
        max_requests=max_requests,
        owned=owned,
        wanted=wanted,
    )
    return format_label_releases(
        result.items,
        label_ref,
        label_name,
        result.page,
        result.total_items,
        footer_cmd,
        filtered=result.filtered,
        stop=result.stop,
        dropped=result.duplicates_dropped,
        header_resent=_fresh(),
        owned=owned,
        wanted=wanted,
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
    max_requests: int | None,
    owned: bool,
    wanted: bool,
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
            max_requests=max_requests,
            owned=owned,
            wanted=wanted,
            mode=mode,
        )

    # A lazy proxy: reading .name sends the request, so only the text header
    # pays for it (as for a label's catalogue), before the scan.
    artist = client.artists.get(entity_id)
    artist_name = "" if mode.json else artist.name
    artist_ref = make_ref("artist", entity_id)

    listing = functools.partial(artist.releases.list, **sort_params(sort, desc))

    # Flag validity is checked in _dispatch before any API call.
    if role:
        role_lower = role.lower()
        result = fetch_filtered_page(
            listing,
            limit=limit,
            keep=lambda item: role_lower in (getattr(item, "role", "") or "").lower(),
            cursor=after,
            max_requests=max_requests,
        )
    else:
        result = fetch_page(listing, page=page or 1, per_page=limit)

    if mode.json:
        return _page_json(
            result, project_artist_release, owned=owned, wanted=wanted, mode=mode
        )

    footer_cmd = next_page_cmd(
        ["get", "releases", artist_ref],
        result,
        limit=limit,
        role=role,
        sort=sort,
        desc=desc,
        max_requests=max_requests,
        owned=owned,
        wanted=wanted,
    )

    return format_artist_releases(
        result.items,
        artist_ref,
        artist_name,
        result.page,
        result.total_items,
        footer_cmd,
        filtered=result.filtered,
        stop=result.stop,
        header_resent=_fresh(),
        owned=owned,
        wanted=wanted,
    )


def _versions_scan(release_type: str, format: str | None) -> bool:  # noqa: A002
    """Whether `get versions --release-type` filters client-side: `official`
    always, as Discogs has no filter for it; `unofficial` only with
    `--format`, which takes the `format` filter Discogs applies it with."""
    return release_type == "official" or (release_type == "unofficial" and bool(format))


def _is_unofficial_version(version: Any) -> bool:
    """Whether a version row lists the "Unofficial Release" description. Its
    `format` is one string of descriptions joined by `, `, so the match is on
    a whole description: "Partially Unofficial" is not one."""
    return UNOFFICIAL in (version.format or "").split(", ")


def _get_tracklist(client: Discogs, entity_id: int, mode: Mode) -> Any:
    release = client.releases.get(entity_id)
    if mode.full:
        return {"tracklist": [full_dump(t) for t in release.tracklist or []]}
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
    release_type: str,
    after: str | None,
    owned: bool,
    wanted: bool,
    max_requests: int | None,
    mode: Mode,
) -> Any:
    master_id = entity_id

    # Smart resolution: release ref → look up its master_id, which selects the
    # endpoint. Either way the title is read only for the text header: free on
    # the fetched release, one lazy-proxy request for a master ref.
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
        titled = release
    else:
        titled = client.masters.get(master_id)

    master_ref = make_ref("master", master_id)
    scan = _versions_scan(release_type, format)
    # Before a scan the header goes first, so --max-requests counts it first;
    # otherwise it stays lazy, read only once the listing succeeded, so a
    # listing that fails costs its one request.
    early_title = titled.title if scan and not mode.json else None

    # The SDK drops the filters left unset; `released` is the API's year filter.
    # Discogs filters `unofficial` itself, on the whole description, so it
    # pages server-side with an exact total.
    server_unofficial = release_type == "unofficial" and not scan
    listing = functools.partial(
        client.masters.get(master_id).versions.list,
        country=country,
        format=UNOFFICIAL if server_unofficial else format,
        label=label,
        released=year,
        **sort_params(sort, desc),
    )
    if scan:
        unofficial = release_type == "unofficial"
        result = fetch_filtered_page(
            listing,
            limit=limit,
            keep=lambda version: _is_unofficial_version(version) == unofficial,
            cursor=after,
            max_requests=max_requests,
        )
    else:
        result = fetch_page(listing, page=page or 1, per_page=limit)

    if mode.json:
        return _page_json(
            result, project_master_version, owned=owned, wanted=wanted, mode=mode
        )

    footer_cmd = next_page_cmd(
        ["get", "versions", master_ref],
        result,
        limit=limit,
        country=country,
        format=format,
        label=label,
        year=year,
        release_type=None if release_type == "all" else release_type,
        sort=sort,
        desc=desc,
        owned=owned,
        wanted=wanted,
        max_requests=max_requests,
    )

    return format_master_versions(
        result.items,
        master_ref,
        titled.title if early_title is None else early_title,
        result.page,
        result.total_items,
        footer_cmd,
        filtered=result.filtered,
        stop=result.stop,
        header_resent=_fresh(),
        owned=owned,
        wanted=wanted,
    )


def _amount(value: float) -> str:
    """`20`, `12.5`, `1000000`, `19.9999999`: a `--max-price` as the header
    shows it and the continuation repeats it. Exact: `repr` round-trips the
    float, and `Decimal` spells it without an exponent."""
    text = format(Decimal(repr(value)), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _get_collection_value(client: Discogs, mode: Mode) -> Any:
    """`get collection --value`: the collection's value in the user's
    currency. `--full` prints the record as Discogs sent it, unparsed."""
    found = valuation(client)
    if mode.full:
        return {"currency": found.currency, "value": full_dump(found.record)}
    amounts = found.amounts()
    if mode.json:
        return {"currency": found.currency, **amounts}
    return format_collection_value(found.currency, amounts)


def _get_collection(
    client: Discogs,
    *,
    page: int | None,
    limit: int,
    after: str | None,
    filters: dict[str, str],
    folder: str | None,
    fields: bool,
    sort: str | None,
    desc: bool,
    max_requests: int | None,
    mode: Mode,
) -> Any:
    """The user's collection, or with `folder` one folder of it, one row
    per copy: server-side pages, or with a filter a client-side scan of
    100-record pages. With `fields`, each row names the copy's notes."""

    def cap_spent() -> bool:
        return max_requests is not None and trace.sends() >= max_requests

    # The username, the folder list and the fields list go before the
    # listing, the way a header does, so --max-requests counts them first.
    # --full names folders by id, so it reads the list only to resolve
    # --folder; once the cap is spent the scan reads no row to name and no
    # folder to resolve.
    user = account.user(client)
    spent = cap_spent()
    names = {} if spent or (mode.full and folder is None) else folder_names(user)
    keep = record_filter(filters)
    custom = None
    if spent:
        # Only a scan takes --max-requests; it continues where it was asked.
        result = unscanned(after, ScanStop("max_requests", max_requests=max_requests))
    else:
        listing = records(
            user,
            folder=ALL_FOLDER if folder is None else folder_id(names, folder),
            sort=sort,
            desc=desc,
        )
        if fields and not cap_spent():
            custom = custom_fields(user)
        if keep:
            result = fetch_filtered_page(
                listing, limit=limit, keep=keep, cursor=after, max_requests=max_requests
            )
        else:
            result = fetch_page(listing, page=page or 1, per_page=limit)

    if mode.json:
        return mode.page(result, lambda record: project_record(record, names, custom))

    footer_cmd = next_page_cmd(
        ["get", "collection"],
        result,
        limit=limit,
        folder=folder,
        fields=fields,
        format=filters.get("format"),
        genre=filters.get("genre"),
        style=filters.get("style"),
        year=filters.get("year"),
        label=filters.get("label"),
        artist=filters.get("artist"),
        title=filters.get("title"),
        sort=sort,
        desc=desc,
        max_requests=max_requests,
    )
    shown = {"folder": folder, **filters} if folder is not None else filters
    return format_collection(
        result.items,
        account.username(client),
        result.page,
        result.total_items,
        footer_cmd,
        folders=names,
        fields=custom,
        filters=shown,
        filtered=result.filtered,
        stop=result.stop,
        header_resent=_fresh(),
    )


def _get_wantlist(
    client: Discogs,
    *,
    page: int | None,
    limit: int,
    after: str | None,
    for_sale: bool,
    max_price: float | None,
    currency: CurrencyCode | None,
    max_requests: int | None,
    mode: Mode,
) -> tuple[Any, bool]:
    """The user's wantlist, server-side pages; with `--for-sale`, a scan that
    keeps the wants for sale, one stats request each. Returns the text or
    JSON document, and whether a want's stats were a `--cached` miss."""
    # The username, before the listing, so --max-requests counts it first.
    user = account.user(client)
    if for_sale:
        check = ForSale(client, max_price=max_price, currency=currency)
        result = fetch_filtered_page(
            user.wantlist.list,
            limit=limit,
            keep=check,
            cursor=after,
            max_requests=max_requests,
            keep_sends=True,
        )
        markets, misses = check.markets, check.misses
    else:
        result = fetch_page(user.wantlist.list, page=page or 1, per_page=limit)
        markets, misses = {}, {}

    if mode.json:
        document = mode.page(result, project_want)
        for row, want in zip(document["results"], result.items, strict=True):
            if want.id in misses:
                context = f"Want {make_ref('release', want.id)}"
                row["error"] = error_document(misses[want.id], context)
            elif want.id in markets:
                row["market"] = project_market(markets[want.id])
        return document, bool(misses)

    amount = None if max_price is None else _amount(max_price)
    footer_cmd = next_page_cmd(
        ["get", "wantlist"],
        result,
        limit=limit,
        for_sale=for_sale,
        max_price=amount,
        currency=currency,
        max_requests=max_requests,
    )
    filters = {"max-price": amount, "currency": currency}
    text = format_wantlist(
        result.items,
        account.username(client),
        result.page,
        result.total_items,
        footer_cmd,
        filters={k: v for k, v in filters.items() if v},
        filtered=result.filtered,
        stop=result.stop,
        header_resent=_fresh(),
        markets=markets,
        misses={rid: classify(exc).message for rid, exc in misses.items()},
    )
    return text, bool(misses)


def _get_inventory(
    client: Discogs,
    username: str,
    *,
    page: int | None,
    limit: int,
    after: str | None,
    sort: str | None,
    desc: bool,
    condition: str | None,
    sleeve: str | None,
    ships_from: str | None,
    max_price: float | None,
    currency: CurrencyCode | None,
    in_wantlist: bool,
    owned: bool,
    max_requests: int | None,
    mode: Mode,
) -> Any:
    """A seller's listings, server-side pages; with a filter, a client-side
    scan. `--in-wantlist` reads the whole wantlist first, and scans nothing
    when that read stops short. `condition` and `sleeve` are grade names
    (`GRADES` keys); `max_price` comes with its `currency`."""
    wants = read_wantlist(client, max_requests=max_requests) if in_wantlist else None
    # _inventory_error lets through both or neither.
    cap = None if max_price is None or currency is None else (max_price, currency)
    check = Filter(
        condition=condition,
        sleeve=sleeve,
        ships_from=ships_from,
        max_price=cap,
        wanted=None if wants is None else wants.ids,
    )
    listing = functools.partial(
        client.users.get(username).inventory.list, **sort_params(sort, desc)
    )
    if wants is not None and wants.stop is not None:
        result = unscanned(after, wants.stop)
    elif check.active:
        result = fetch_filtered_page(
            listing,
            limit=limit,
            keep=check,
            cursor=after,
            max_requests=max_requests,
        )
    else:
        result = fetch_page(listing, page=page or 1, per_page=limit)

    ownership = (
        ownership_of(client, result, max_requests=max_requests) if owned else None
    )
    if mode.json:
        document = mode.page(result, project_listing)
        if max_price is not None:
            document["pagination"]["skipped_currency"] = check.skipped_currency
        if wants is not None:
            document["wantlist"] = wants.document()
        if ownership is not None:
            mark_rows(document, result.items, ownership, release_id=release_id)
        return document

    amount = None if max_price is None else _amount(max_price)
    condition_code = None if condition is None else GRADES[condition]
    sleeve_code = None if sleeve is None else GRADES[sleeve]
    # A username after `--`, so one starting with `-` is never an option.
    dashed = username.startswith("-")
    footer_cmd = next_page_cmd(
        ["get", "inventory"] if dashed else ["get", "inventory", username],
        result,
        limit=limit,
        positionals=(username,) if dashed else (),
        condition=condition_code,
        sleeve=sleeve_code,
        ships_from=ships_from,
        max_price=amount,
        currency=currency,
        in_wantlist=in_wantlist,
        sort=sort,
        desc=desc,
        owned=owned,
        max_requests=max_requests,
    )
    filters = {"ships-from": ships_from, "max-price": amount, "currency": currency}
    return format_inventory(
        result.items,
        username,
        result.page,
        result.total_items,
        footer_cmd,
        condition=condition_code,
        sleeve=sleeve_code,
        filters={k: v for k, v in filters.items() if v},
        in_wantlist=in_wantlist,
        filtered=result.filtered,
        stop=result.stop,
        skipped_currency=check.skipped_currency,
        wants=wants,
        wants_resent=_fresh(),
        ownership=ownership,
    )


SORT_KEYS: dict[str, tuple[str, ...]] = {
    "releases": ("year", "title", "format"),
    "versions": ("released", "title", "format", "label", "catno", "country"),
    "collection": (
        "label",
        "artist",
        "title",
        "catno",
        "format",
        "rating",
        "added",
        "year",
    ),
    "inventory": ("listed", "price", "item", "artist", "label", "catno"),
}


def _complete_sort(
    ctx: click.Context, _param: click.Parameter, incomplete: str
) -> list[str]:
    """Tab offers the noun's sort keys, or before a noun every key some noun
    takes. A noun without --sort gets none."""
    # After `--sort ` click stops parsing at the option awaiting its value, so
    # the words typed before it, noun first, are left unconverted in ctx.args;
    # after `--sort=` (one word in zsh and fish) they parse, into ctx.params.
    noun = ctx.params.get("noun") or next(iter(ctx.args), None)
    if noun is None:
        keys = list(dict.fromkeys(k for ks in SORT_KEYS.values() for k in ks))
    else:
        keys = list(SORT_KEYS.get(noun, ()))
    return [k for k in keys if k.startswith(incomplete)]


# Which `get` options each noun takes, by click parameter name. Every other
# noun takes only --json/--full. The per-ref nuances of `releases` (--role and
# --sort need an artist, --year a label) are checked in _flag_error.
NOUN_OPTIONS: dict[str, frozenset[str]] = {
    "collection": frozenset(
        {
            "after",
            "artist",
            "desc",
            "fields",
            "folder",
            "format_",
            "genre",
            "label",
            "limit",
            "max_requests",
            "page",
            "sort",
            "style_",
            "title",
            "value",
            "year",
        },
    ),
    "lists": frozenset({"limit", "page"}),
    "inventory": frozenset(
        {
            "after",
            "condition",
            "currency",
            "desc",
            "in_wantlist",
            "limit",
            "max_price",
            "max_requests",
            "owned",
            "page",
            "ships_from",
            "sleeve",
            "sort",
        },
    ),
    "release": frozenset({"compact", "master_year", "verbose"}),
    "releases": frozenset(
        {
            "after",
            "desc",
            "limit",
            "max_requests",
            "owned",
            "page",
            "role",
            "sort",
            "wanted",
            "year",
        },
    ),
    "stats": frozenset({"currency"}),
    "versions": frozenset(
        {
            "after",
            "country",
            "desc",
            "format_",
            "label",
            "limit",
            "max_requests",
            "owned",
            "page",
            "release_type",
            "sort",
            "wanted",
            "year",
        },
    ),
    "wantlist": frozenset(
        {
            "after",
            "currency",
            "for_sale",
            "limit",
            "max_price",
            "max_requests",
            "page",
        },
    ),
}

NOUN_SPECIFIC_OPTIONS: frozenset[str] = frozenset().union(*NOUN_OPTIONS.values())


def _fresh() -> bool:
    """Whether this invocation runs under `--fresh`, which caches nothing, so
    the text header lookup is sent again on every run."""
    return bool(click.get_current_context().find_root().params.get("fresh"))


def _option_flag(name: str) -> str:
    return "--" + name.rstrip("_").replace("_", "-")


def _explicit_options() -> frozenset[str]:
    """The noun-specific options the user supplied, by parameter name.

    Uses click's parameter source rather than the value, so an explicit value
    equal to the default (`--limit 5`) still counts as supplied. The `tracks`
    and `price` shortcuts have none of these options, so their set is empty.
    """
    ctx = click.get_current_context()
    defaults = (None, ParameterSource.DEFAULT, ParameterSource.DEFAULT_MAP)
    return frozenset(
        name
        for name in NOUN_SPECIFIC_OPTIONS
        if ctx.get_parameter_source(name) not in defaults
    )


def _price_error(max_price: float | None, currency: str | None) -> str | None:
    """What `--max-price`/`--currency` cannot take: an unknown currency, or
    a threshold without one, so it is always in a currency the user named,
    never the account's."""
    if currency is not None and _currency(currency) is None:
        return (
            f"--currency must be one of {', '.join(CURRENCIES)} ({currency!r} given)."
        )
    if max_price is not None and currency is None:
        return (
            "--max-price needs --currency CODE (e.g. --currency EUR), so the "
            "threshold and the prices shown are in a currency you named."
        )
    if max_price is not None and not (math.isfinite(max_price) and max_price >= 0):
        return f"--max-price must be a number, 0 or more ({max_price} given)."
    return None


_GRADE_CODES = list(GRADES.values())


def _inventory_error(
    refs: tuple[str, ...],
    *,
    condition: str | None,
    sleeve: str | None,
    ships_from: str | None,
    max_price: float | None,
    currency: str | None,
) -> str | None:
    """What `get inventory` cannot take: other than one username, a grade
    Discogs does not use, a blank country (which a continuation could not
    repeat), or a price filter that is not an amount in a named currency."""
    if len(refs) != 1:
        return f"get inventory takes exactly one username ({len(refs)} given)."
    if not refs[0].strip():
        return f"get inventory needs a seller's Discogs username ({refs[0]!r} given)."
    for flag, value in (("--condition", condition), ("--sleeve", sleeve)):
        if value is not None and grade(value) is None:
            codes = f"{', '.join(_GRADE_CODES[:-1])} or {_GRADE_CODES[-1]}"
            return (
                f"{flag} must be a Discogs grade: {codes}, or its full name such "
                f"as 'Very Good Plus (VG+)' (any case; {value!r} given)."
            )
    if ships_from is not None and not ships_from.strip():
        return (
            f"--ships-from needs a country, as Discogs names it ({ships_from!r} given)."
        )
    price_error = _price_error(max_price, currency)
    if price_error:
        return price_error
    if currency is not None and max_price is None:
        return (
            "--currency needs --max-price with get inventory: together they keep "
            "the listings priced in that currency up to that amount."
        )
    return None


def _wantlist_error(
    refs: tuple[str, ...],
    *,
    for_sale: bool,
    max_price: float | None,
    currency: str | None,
    max_requests: int | None,
) -> str | None:
    """What `get wantlist` cannot take: a ref, or a --for-sale flag without
    --for-sale. --max-price needs --currency, so the threshold is always in a
    currency the user named, never the account's."""
    if refs:
        return "get wantlist takes no ref: it lists your own wantlist."
    stray = [
        flag
        for flag, given in (
            ("--max-price", max_price is not None),
            ("--currency", currency is not None),
            ("--max-requests", max_requests is not None),
        )
        if given
    ]
    if stray and not for_sale:
        verb = "applies" if len(stray) == 1 else "apply"
        return (
            f"{', '.join(stray)} only {verb} to get wantlist --for-sale, which "
            "checks each want's Marketplace stats; get wantlist alone pages "
            "server-side."
        )
    price_error = _price_error(max_price, currency)
    if price_error:
        return price_error
    if max_requests is not None and max_requests < 3:  # noqa: PLR2004
        return (
            f"--max-requests must be 3 or more with --for-sale ({max_requests} "
            "given): the username, a wants page and one want's stats, which a "
            "cold cache or --fresh sends on every run."
        )
    return None


def _collection_error(
    refs: tuple[str, ...],
    *,
    explicit: frozenset[str],
    filters: dict[str, str],
    folder: str | None,
    max_requests: int | None,
    full: bool,
) -> str | None:
    """What `get collection` cannot take: a ref, any other flag with
    --value, --fields with --json --full, an empty filter (it would match
    nothing) or folder name, or --max-requests without a filter, which
    leaves no scan to cap (`--folder` alone pages server-side)."""
    if refs:
        return "get collection takes no ref: it lists your own collection."
    others = sorted(_option_flag(name) for name in explicit - {"value"})
    if "value" in explicit and others:
        return (
            f"--value takes no other get collection flag ({', '.join(others)} "
            "given): it prints your collection's value, not a listing."
        )
    if full and "fields" in explicit:
        return (
            "--fields does not apply with --json --full: --full prints each "
            "record's notes as Discogs sends them, by field id. Drop --full, "
            "or --fields."
        )
    empty = [f"--{key}" for key, value in filters.items() if not value.strip()]
    if empty:
        verb = "needs a value" if len(empty) == 1 else "need values"
        return (
            f"{', '.join(empty)} {verb}: a filter matches a whole value, "
            "ignoring case, with * for any run of characters."
        )
    if folder is not None and not folder.strip():
        return (
            "--folder needs a folder name, as get collection prints it (· folder Jazz)."
        )
    if max_requests is not None and not filters:
        return (
            "--max-requests caps a filtered get collection scan (--format, "
            "--genre, --style, --year, --label, --artist or --title); get "
            "collection alone, or with --folder alone, pages server-side and "
            "sends one request per page."
        )
    return None


def _flag_error(
    noun: str,
    refs: tuple[str, ...],
    *,
    explicit: frozenset[str],
    limit: int,
    page: int | None,
    after: str | None,
    role: str | None,
    year: str | None,
    sort: str | None,
    desc: bool,
    format: str | None,  # noqa: A002  # click option name for --format
    release_type: str,
    max_requests: int | None,
    owned: bool,
    for_sale: bool,
    max_price: float | None,
    currency: str | None,
    collection_filters: dict[str, str],
    condition: str | None,
    sleeve: str | None,
    ships_from: str | None,
    in_wantlist: bool,
    folder: str | None,
    full: bool,
) -> str | None:
    """Reject flag combinations that make no sense for this noun.

    Checked before any API call so a bad flag never costs a request or gets
    masked by an auth/network failure. Client-side filtered listings (which
    page by cursor, not by number) are `releases --role` on an artist,
    `releases --year` on a label, `versions --release-type official` (or
    `unofficial` with `--format`), `collection` with a filter, `wantlist
    --for-sale` and `inventory` with any of its filters.
    """
    if noun == "lists" and refs:
        return (
            "get lists takes no ref: it lists your own lists. "
            "Open one with get list ID."
        )
    if noun == "inventory" and "wanted" in explicit:
        return (
            "get inventory takes no --wanted: to keep only the listings whose "
            "release is on your wantlist, use --in-wantlist."
        )
    if noun == "inventory":
        inventory_error = _inventory_error(
            refs,
            condition=condition,
            sleeve=sleeve,
            ships_from=ships_from,
            max_price=max_price,
            currency=currency,
        )
        if inventory_error:
            return inventory_error
    if noun == "stats":
        price_error = _price_error(None, currency)
        if price_error:
            return price_error
    if noun == "wantlist":
        wantlist_error = _wantlist_error(
            refs,
            for_sale=for_sale,
            max_price=max_price,
            currency=currency,
            max_requests=max_requests,
        )
        if wantlist_error:
            return wantlist_error
    if noun == "collection":
        collection_error = _collection_error(
            refs,
            explicit=explicit,
            filters=collection_filters,
            folder=folder,
            max_requests=max_requests,
            full=full,
        )
        if collection_error:
            return collection_error
    label_refs = noun == "releases" and any(r.startswith("@l") for r in refs)
    artist_refs = noun == "releases" and not all(r.startswith("@l") for r in refs)
    inventory_filters = (condition, sleeve, ships_from, max_price)
    filtered = (
        (noun == "releases" and (bool(role) or (label_refs and bool(year))))
        or (noun == "versions" and _versions_scan(release_type, format))
        or (noun == "wantlist" and for_sale)
        or (noun == "collection" and bool(collection_filters))
        or (
            noun == "inventory"
            and (in_wantlist or any(f is not None for f in inventory_filters))
        )
    )
    if after is not None and not filtered:
        return (
            "--after only continues a client-side filtered listing "
            "(get releases --role, get releases @l... --year, get versions "
            "--release-type official or unofficial with --format, get "
            "collection with --format, --genre, --style, --year, --label, "
            "--artist or --title, get "
            "wantlist --for-sale, or get inventory with --condition, --sleeve, "
            "--ships-from, --max-price or --in-wantlist). This command pages "
            "server-side or is not paginated; use --page where applicable."
        )
    if after is not None and len(refs) > 1:
        return (
            "--after continues one listing, so it takes exactly one ref "
            f"({len(refs)} given)."
        )
    # --owned and --wanted read nothing on these nouns (each row carries the
    # user's own counts), so only a scan is left to cap.
    if max_requests is not None and noun in ("releases", "versions") and not filtered:
        if noun == "versions":
            return (
                "--max-requests caps a client-side filtered scan (get versions "
                "--release-type official, or unofficial with --format). This "
                "listing pages server-side and sends one request per page."
            )
        return (
            "--max-requests caps a client-side filtered scan (get releases "
            "--role, or get releases @l... --year). This listing pages "
            "server-side and sends one request per page."
        )
    if max_requests is not None and noun == "inventory" and not (filtered or owned):
        return (
            "--max-requests caps the get inventory filter scan (--condition, "
            "--sleeve, --ships-from, --max-price or --in-wantlist) or the --owned "
            "collection read. get inventory alone pages server-side and sends one "
            "request per page."
        )
    if max_requests is not None and len(refs) > 1:
        return (
            "--max-requests caps one scan, so it takes exactly one ref "
            f"({len(refs)} given): a capped listing continues one ref at a time."
        )
    if filtered and page is not None:
        return (
            "--page is not available while results are filtered client-side "
            "(--role, --year on a label catalogue, --release-type official or "
            "unofficial with --format on versions, a get collection filter "
            "(--folder alone pages), "
            "--for-sale, or a get inventory filter). Use the Next page / "
            "Continue scan command from the previous output."
        )
    if after is not None:
        try:
            parse_cursor(after)
        except ValueError as exc:
            return str(exc)
    accepted = NOUN_OPTIONS.get(noun, frozenset())
    rejected = sorted(_option_flag(name) for name in explicit - accepted)
    if rejected:
        verb = "does" if len(rejected) == 1 else "do"
        takes = (
            ", ".join(sorted(_option_flag(name) for name in accepted))
            if accepted
            else "only --json and --full"
        )
        return (
            f"{', '.join(rejected)} {verb} not apply to 'get {noun}'. "
            f"'get {noun}' takes {takes}."
        )
    # After the applicability check, so a flag the noun does not take is
    # reported as such rather than as out of range.
    bad_paging = paging_error(limit, page)
    if bad_paging:
        return bad_paging
    bad_cap = max_requests_error(max_requests)
    if bad_cap:
        return bad_cap
    if year and artist_refs:
        return (
            "--year filters versions and label catalogues; for an artist "
            "discography use --sort year (optionally --desc) and page."
        )
    if desc and not sort:
        return "--desc needs --sort <key>."
    if sort and sort not in SORT_KEYS[noun]:
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
    release_type: str,
    role: str | None,
    max_requests: int | None,
    owned: bool,
    wanted: bool,
    verbose: bool,
    compact: bool,
    master_year: bool,
    currency: CurrencyCode | None,
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
    if noun == "list":
        return _get_list(client, entity_id, mode)
    if noun == "owned":
        return _get_owned(client, entity_type, entity_id, mode)
    if noun == "price":
        return _get_price(client, entity_id, mode)
    if noun == "release":
        return _get_release(
            client,
            entity_id,
            verbose=verbose,
            compact=compact,
            master_year=master_year,
            mode=mode,
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
            max_requests=max_requests,
            owned=owned,
            wanted=wanted,
            mode=mode,
        )
    if noun == "stats":
        return _get_stats(client, entity_id, currency=currency, mode=mode)
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
        release_type=release_type,
        after=after,
        owned=owned,
        wanted=wanted,
        max_requests=max_requests,
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
    genre: str | None,
    style_: str | None,
    sort: str | None,
    desc: bool,
    release_type: str,
    role: str | None,
    max_requests: int | None,
    owned: bool,
    wanted: bool,
    for_sale: bool,
    max_price: float | None,
    currency: str | None,
    value: bool,
    condition: str | None,
    sleeve: str | None,
    ships_from: str | None,
    in_wantlist: bool,
    artist: str | None,
    title: str | None,
    folder: str | None,
    fields: bool,
    verbose: bool,
    compact: bool,
    master_year: bool,
    mode: Mode,
) -> None:
    """Shared dispatch for get, tracks, and price: one or many refs.

    Several refs run in sequence, one API round trip each, so "compare these
    pressings" is one command. Output is one block per ref in ref order,
    blank-line separated, on stdout: a failing ref contributes its `✗` error
    block and the rest still run; JSON becomes a list with `{"ref", "error"}`
    items for failures. Exit 1 if any ref failed. With one ref the shape is
    unchanged, including the single-error path (`fail()`: stderr text or a
    JSON envelope). `collection`, `lists` and `wantlist` take no ref and
    `inventory` one username; each prints one document.
    """
    # The `get collection` filters, in the order the header and the
    # continuation repeat them.
    collection_filters = {
        key: value
        for key, value in (
            ("format", format),
            ("genre", genre),
            ("style", style_),
            ("year", year),
            ("label", label),
            ("artist", artist),
            ("title", title),
        )
        if value is not None
    }
    flag_error = _flag_error(
        noun,
        refs,
        explicit=_explicit_options(),
        limit=limit,
        page=page,
        after=after,
        role=role,
        year=year,
        sort=sort,
        desc=desc,
        format=format,
        release_type=release_type,
        max_requests=max_requests,
        owned=owned,
        for_sale=for_sale,
        max_price=max_price,
        currency=currency,
        collection_filters=collection_filters,
        folder=folder,
        condition=condition,
        sleeve=sleeve,
        ships_from=ships_from,
        in_wantlist=in_wantlist,
        full=mode.full,
    )
    if flag_error:
        fail(ValueError(flag_error), json_output=mode.json)
    if len(refs) > MAX_REFS:
        fail(
            ValueError(f"At most {MAX_REFS} refs per command ({len(refs)} given)."),
            json_output=mode.json,
        )
    feature = {
        "collection": "get collection",
        "lists": "get lists",
        "owned": "get owned",
        "wantlist": "get wantlist",
    }.get(noun)
    if feature is None and in_wantlist:
        feature = "--in-wantlist"
    elif feature is None and owned:
        feature = "--owned"
    elif feature is None and wanted:
        feature = "--wanted"
    if feature:
        try:
            require_user(feature)
        except UserRequiredError as e:
            fail(e, json_output=mode.json)

    try:
        client = get_client()
    # classify() maps every exception to a coded, recovery-oriented error, so
    # catching broadly is the point.
    except Exception as e:  # noqa: BLE001
        fail(e, json_output=mode.json)

    if noun in REFLESS_NOUNS:
        try:
            if noun == "collection" and value:
                output, failed = _get_collection_value(client, mode), False
            elif noun == "collection":
                output, failed = (
                    _get_collection(
                        client,
                        page=page,
                        limit=limit,
                        after=after,
                        filters=collection_filters,
                        folder=folder,
                        fields=fields,
                        sort=sort,
                        desc=desc,
                        max_requests=max_requests,
                        mode=mode,
                    ),
                    False,
                )
            elif noun == "lists":
                output = _get_lists(client, page=page, limit=limit, mode=mode)
                failed = False
            else:
                output, failed = _get_wantlist(
                    client,
                    page=page,
                    limit=limit,
                    after=after,
                    for_sale=for_sale,
                    max_price=max_price,
                    currency=_currency(currency) if currency else None,
                    max_requests=max_requests,
                    mode=mode,
                )
        except Exception as e:  # noqa: BLE001
            fail(e, noun.title(), json_output=mode.json)
        if mode.json:
            dump(output)
        else:
            print(output)
        if failed:
            sys.exit(1)
        return

    if noun == "inventory":
        try:
            output = _get_inventory(
                client,
                refs[0],
                page=page,
                limit=limit,
                after=after,
                sort=sort,
                desc=desc,
                condition=grade(condition) if condition else None,
                sleeve=grade(sleeve) if sleeve else None,
                ships_from=ships_from,
                max_price=max_price,
                currency=_currency(currency) if currency else None,
                in_wantlist=in_wantlist,
                owned=owned,
                max_requests=max_requests,
                mode=mode,
            )
        except Exception as e:  # noqa: BLE001
            fail(e, f"Inventory {refs[0]}", json_output=mode.json)
        if mode.json:
            dump(output)
        else:
            print(output)
        return

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
                    release_type=release_type,
                    role=role,
                    max_requests=max_requests,
                    owned=owned,
                    wanted=wanted,
                    verbose=verbose,
                    compact=compact,
                    master_year=master_year,
                    currency=_currency(currency) if currency else None,
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
                # The block goes to stdout with the others.
                blocks.append(format_error(e, context, color=style.enabled(sys.stdout)))

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
        callback=style.off_for_json,
        help="Output JSON (a compact projection of what the text view shows)",
    ),
    click.option(
        "--full",
        is_flag=True,
        default=False,
        help="With --json: the complete, normalized record instead of the projection",
    ),
]


def _json_options(command: Any) -> Any:
    for option in reversed(_JSON_OPTIONS):
        command = option(command)
    return command


@click.command()
@click.argument("noun", type=click.Choice(GET_NOUNS))
@click.argument("refs", nargs=-1, metavar="REF...")
@click.option(
    "--artist",
    metavar="PATTERN",
    help="collection: keep records with a credited artist whose name matches "
    "PATTERN: the whole name, any case, * for any run of characters, \\ to "
    "escape (\\* is a literal *)",
)
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
@click.option(
    "--condition",
    metavar="GRADE",
    shell_complete=_complete_grade,
    help="inventory: keep listings whose media grade is GRADE or better "
    f"({'|'.join(GRADES.values())} or the Discogs name, any case)",
)
@click.option("--country", help="Filter versions by country")
@click.option(
    "--currency",
    metavar="CODE",
    shell_complete=_complete_currency,
    help=f"{'|'.join(CURRENCIES)}, any case. wantlist --for-sale, stats: the "
    "currency of the Marketplace prices (sent as curr_abbr); inventory: with "
    "--max-price, keep listings priced in it (matched client-side, never sent)",
)
@click.option(
    "--desc", is_flag=True, default=False, help="With --sort: descending order"
)
@click.option(
    "--format",
    "format_",
    help="versions: filter by format; collection: keep records with this format "
    'name or description (e.g. Vinyl, 12"; a pattern, as --artist)',
)
@click.option(
    "--for-sale",
    is_flag=True,
    default=False,
    help="wantlist: keep the wants with copies for sale (one stats request each)",
)
@click.option(
    "--fields",
    is_flag=True,
    default=False,
    help="collection: show each copy's notes and custom fields by name "
    "(Media Condition: VG+); one more request (not with --json --full)",
)
@click.option(
    "--folder",
    metavar="NAME",
    help="collection: list only this folder, by name as rows print it (any "
    "case; not a pattern); pages server-side unless a filter is given",
)
@click.option(
    "--genre", help="collection: keep records with this genre (a pattern, as --artist)"
)
@click.option(
    "--in-wantlist",
    is_flag=True,
    default=False,
    help="inventory: keep listings of releases on your wantlist, read whole "
    "first (needs DISCOGS_TOKEN or OAuth)",
)
@_json_options
@click.option(
    "--label",
    help="versions: filter by label; collection: keep records with this label "
    "name (a pattern, as --artist)",
)
@click.option(
    "--limit", type=int, default=DEFAULT_LIMIT, help="Results per page (1-100)"
)
@click.option(
    "--max-price",
    type=float,
    help="wantlist --for-sale: keep wants whose lowest price is at most this; "
    "inventory: keep listings priced at most this in --currency (needs --currency)",
)
@click.option(
    "--master-year",
    is_flag=True,
    default=False,
    help="release: show its master's year (1 more request per release with a "
    "master, shared with get master)",
)
@click.option(
    "--max-requests",
    type=int,
    help="releases --role / @l --year, versions --release-type official, a "
    "collection filter (not --folder alone), wantlist --for-sale, or an inventory "
    "filter or --owned: cap the command's HTTP sends (default: no cap)",
)
@click.option(
    "--owned",
    is_flag=True,
    default=False,
    help="releases / versions / inventory: mark each release row with the copies "
    "in your own collection (needs DISCOGS_TOKEN or OAuth; releases and versions "
    "rows carry the count, inventory reads your collection)",
)
@click.option(
    "--page",
    type=int,
    help="Page number, from 1 (server-side pages only: not with a collection "
    "filter; get collection --folder alone pages)",
)
@click.option(
    "--release-type",
    type=click.Choice(RELEASE_TYPES),
    default="all",
    help="versions: all (default, unlike search), unofficial (filtered by Discogs; "
    "with --format a client-side scan) or official (client-side scan, continues "
    "via --after)",
)
@click.option("--role", help="Filter releases by credit role (e.g., Main, Remix)")
@click.option(
    "--ships-from",
    metavar="COUNTRY",
    help="inventory: keep listings shipping from COUNTRY (exact, any case)",
)
@click.option(
    "--sleeve",
    metavar="GRADE",
    shell_complete=_complete_grade,
    help="inventory: keep listings whose sleeve grade is GRADE or better, as "
    "--condition (Generic, Not Graded and No Cover never match)",
)
@click.option(
    "--sort",
    shell_complete=_complete_sort,
    help=(
        "Server-side order. releases @a: year|title|format; "
        "versions: released|title|format|label|catno|country; "
        "collection: label|artist|title|catno|format|rating|added|year; "
        "inventory: listed|price|item|artist|label|catno"
    ),
)
@click.option(
    "--style",
    "style_",
    help="collection: keep records with this style (a pattern, as --artist)",
)
@click.option(
    "--title",
    metavar="PATTERN",
    help="collection: keep records whose release title (without the artist) "
    "matches PATTERN",
)
@click.option(
    "-v",
    "--verbose",
    is_flag=True,
    default=False,
    help="release: also print notes, credits, and identifiers",
)
@click.option(
    "--value",
    is_flag=True,
    default=False,
    help="collection: print its value (min, median, max) in your currency "
    "instead of the listing",
)
@click.option(
    "--wanted",
    is_flag=True,
    default=False,
    help="releases / versions: mark each release row with whether it is on your "
    "own wantlist, from the want count the row carries (needs DISCOGS_TOKEN or "
    "OAuth)",
)
@click.option(
    "--year",
    help="versions: filter by release year; releases @l, collection: keep that "
    "year (client-side scan, continues via --after)",
)
def get(
    noun: str,
    refs: tuple[str, ...],
    json_output: bool,
    full: bool,
    after: str | None,
    compact: bool,
    condition: str | None,
    country: str | None,
    currency: str | None,
    desc: bool,
    format_: str | None,
    for_sale: bool,
    genre: str | None,
    in_wantlist: bool,
    label: str | None,
    limit: int,
    master_year: bool,
    max_price: float | None,
    max_requests: int | None,
    owned: bool,
    page: int | None,
    release_type: str,
    role: str | None,
    ships_from: str | None,
    sleeve: str | None,
    sort: str | None,
    style_: str | None,
    value: bool,
    artist: str | None,
    title: str | None,
    folder: str | None,
    fields: bool,
    verbose: bool,
    wanted: bool,
    year: str | None,
) -> None:
    """Get entity details for one or more refs.

    NOUN is the entity type: artist, credits, identifiers (alias: ids), label,
    list, master, owned, price, release, releases, stats, tracklist, versions;
    inventory, a seller's listings, which takes one Discogs username as REF;
    or collection, lists and wantlist, your own collection, lists and
    wantlist, which take no REF.
    REF is a typed ref (@r123, @a456) or raw Discogs ID; a list is a raw ID
    only. Several refs run in sequence (at most 10).
    """
    if not refs and noun not in REFLESS_NOUNS:
        ctx = click.get_current_context()
        param = next(p for p in ctx.command.params if p.name == "refs")
        raise click.MissingParameter(ctx=ctx, param=param)
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
        genre=genre,
        style_=style_,
        sort=sort,
        desc=desc,
        release_type=release_type,
        role=role,
        max_requests=max_requests,
        owned=owned,
        wanted=wanted,
        for_sale=for_sale,
        max_price=max_price,
        currency=currency,
        value=value,
        artist=artist,
        title=title,
        folder=folder,
        fields=fields,
        condition=condition,
        sleeve=sleeve,
        ships_from=ships_from,
        in_wantlist=in_wantlist,
        verbose=verbose,
        compact=compact,
        master_year=master_year,
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
        genre=None,
        style_=None,
        sort=None,
        desc=False,
        release_type="all",
        role=None,
        max_requests=None,
        owned=False,
        wanted=False,
        for_sale=False,
        max_price=None,
        currency=None,
        value=False,
        artist=None,
        title=None,
        folder=None,
        fields=False,
        condition=None,
        sleeve=None,
        ships_from=None,
        in_wantlist=False,
        verbose=False,
        compact=False,
        master_year=False,
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
