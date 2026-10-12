"""Tests for CLI argument parsing and dispatch."""

from __future__ import annotations

import contextlib
import errno
import functools
import importlib
import importlib.metadata
import io
import json
import logging
import os
import re
import shlex
import sqlite3
import sys
from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import click
import httpx2
import pytest
from click.testing import CliRunner, Result
from discogs_sdk import (
    Artist,
    CacheMissError,
    Discogs,
    MarketplaceReleaseStats,
    Master,
    NotFoundError,
    PriceSuggestions,
    RateLimit,
    RateLimitError,
    Release,
    RequestEvent,
    SearchResult,
)

import agent_discogs.client as client_module
from agent_discogs import cli, main, trace
from agent_discogs.json_output import dumps
from agent_discogs.pagination import PageResult


def _fake(**kwargs: object) -> SimpleNamespace:
    return SimpleNamespace(**kwargs)


class _Listing:
    """A fake SDK listing method (`client.search`, `<proxy>.releases.list`) for
    tests that fake `fetch_page`: it records the params of each call."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def __call__(self, **params: object) -> None:
        self.calls.append(params)


def _listed(**kwargs: object) -> SimpleNamespace:
    """A fake artist, label or master proxy with fake `releases`/`versions`."""
    return SimpleNamespace(
        **kwargs, releases=_fake(list=_Listing()), versions=_fake(list=_Listing())
    )


def _fake_client(
    *,
    artists_get: object = None,
    labels_get: object = None,
    masters_get: object = None,
    releases_get: object = None,
) -> SimpleNamespace:
    """Build a fake Discogs client with specified resource getters."""
    return SimpleNamespace(
        artists=SimpleNamespace(get=artists_get or (lambda _id: _listed())),
        labels=SimpleNamespace(get=labels_get or (lambda _id: _listed())),
        masters=SimpleNamespace(get=masters_get or (lambda _id: _listed())),
        releases=SimpleNamespace(get=releases_get or (lambda _id: None)),
        search=_Listing(),
    )


class _FakeAPI:
    """The real SDK client, with its in-memory cache and real `cache_only()` /
    `no_cache()`, over a mock transport: `sends` lists every request that
    reached the network as `(path, query params)`. `respond(path, params)`
    returns the JSON body."""

    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        respond: Callable[[str, dict[str, str]], object],
    ) -> None:
        self.sends: list[tuple[str, dict[str, str]]] = []
        self._respond = respond
        http = httpx2.Client(transport=httpx2.MockTransport(self._handle))
        # The SDK caches nothing for an injected http_client without SDK
        # credentials, so the fake needs a token for cache flags to mean anything.
        client = Discogs(
            token="fake",  # noqa: S106  # placeholder, never reaches Discogs
            cache=True,
            http_client=http,
            on_request=trace.record,
        )
        monkeypatch.setattr("agent_discogs.client._client", client)

    def _handle(self, request: httpx2.Request) -> httpx2.Response:
        params = dict(request.url.params)
        self.sends.append((request.url.path, params))
        return httpx2.Response(200, json=self._respond(request.url.path, params))


_LISTING_KEYS = {"search": "results", "releases": "releases", "versions": "versions"}


def _empty_listing(path: str, params: dict[str, str]) -> object:
    """Every listing is an empty page; every entity is `{"id": 1, ...}`."""
    key = _LISTING_KEYS.get(path.rsplit("/", 1)[-1])
    if key is None:
        return {"id": 1, "name": "Entity", "title": "Entity"}
    page = {"page": int(params.get("page", 1)), "pages": 1, "items": 0}
    return {"pagination": page, key: []}


class _Catalogue:
    """`rows` rows (default 18) behind every listing, titled `Row<id>e<edition>`.
    Only rows with `id % 6 >= 4` pass the client-side filters (official
    format, role Main, year 1999), so a `--limit 2` filtered page fills on the
    last row of each 6-row API page and every continuation needs a new
    request; with `sparse`, no row passes. Bumping `edition` stands in for
    Discogs changing data after a page was cached."""

    def __init__(self, rows: int = 18, *, sparse: bool = False) -> None:
        self.rows, self.sparse = rows, sparse
        self.edition = 1

    def __call__(self, path: str, params: dict[str, str]) -> object:
        key = _LISTING_KEYS.get(path.rsplit("/", 1)[-1])
        if key is None:
            return {"id": 1, "name": "Entity", "title": "Entity"}
        page, per = int(params["page"]), int(params["per_page"])
        rows = []
        for i in range((page - 1) * per, min(page * per, self.rows)):
            match = not self.sparse and i % 6 >= 4
            title = f"Row{i}e{self.edition}"
            row: dict[str, object] = {"id": i, "title": title, "type": "release"}
            if key == "results":
                row["format"] = ["Vinyl"] if match else ["Unofficial Release"]
            elif key == "releases":
                row["role"] = "Main" if match else "Remix"
                row["year"] = 1999 if match else 2000
            else:  # version rows print the label, not the title
                row["label"] = title
            rows.append(row)
        pagination = {"page": page, "pages": -(-self.rows // per), "items": self.rows}
        return {"pagination": pagination, key: rows}


class TestCacheCommand:
    def test_cache_clear(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "agent_discogs.commands.cache.get_client",
            lambda: _fake(clear_cache=lambda: None),
        )
        result = CliRunner().invoke(cli, ["cache", "clear"])
        assert result.exit_code == 0
        assert "Cache cleared" in result.output

    def test_cache_help(self) -> None:
        result = CliRunner().invoke(cli, ["cache", "--help"])
        assert result.exit_code == 0
        assert "clear" in result.output


class TestCLIBasics:
    def test_version(self) -> None:
        result = CliRunner().invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert "agent-discogs" in result.output
        expected = importlib.metadata.version("agent-discogs")
        assert expected in result.output

    def test_help(self) -> None:
        result = CliRunner().invoke(cli, ["--help"])
        assert result.exit_code == 0
        # Commands section
        assert "search [type] <query>" in result.output
        assert "get <noun> <ref>" in result.output
        assert "tracks <ref>" in result.output
        assert "price <ref>" in result.output
        assert "cache clear" in result.output
        assert "status" in result.output
        # Aliases
        assert "aliases: find, query" in result.output
        assert "aliases: fetch, show" in result.output
        # Search types and get nouns
        assert "Search Types:" in result.output
        assert "Get Nouns:" in result.output
        # Ref system explanation
        assert "@a3857 (artist)" in result.output
        assert "@r847868 (release)" in result.output
        # Environment
        assert "DISCOGS_TOKEN" in result.output
        # Examples
        assert "Examples:" in result.output
        assert 'agent-discogs search "The Downward Spiral"' in result.output

    def test_no_args_shows_help(self) -> None:
        result = CliRunner().invoke(cli, [])
        assert result.exit_code == 0
        assert "agent-discogs - token-efficient Discogs CLI" in result.output
        assert "Commands:" in result.output
        assert "Refs:" in result.output
        assert "Examples:" in result.output

    def test_search_help(self) -> None:
        result = CliRunner().invoke(cli, ["search", "--help"])
        assert result.exit_code == 0
        assert "--year" in result.output
        assert "--genre" in result.output
        assert "--limit" in result.output

    def test_get_help(self) -> None:
        result = CliRunner().invoke(cli, ["get", "--help"])
        assert result.exit_code == 0
        assert "release" in result.output
        assert "artist" in result.output
        assert "tracklist" in result.output
        assert "price" in result.output

    def test_unknown_command(self) -> None:
        result = CliRunner().invoke(cli, ["nonexistent"])
        assert result.exit_code != 0

    def test_main_entry_point(self) -> None:
        result = CliRunner().invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert main is not None


class TestStatusCommand:
    def test_status(self) -> None:
        result = CliRunner().invoke(cli, ["status"])
        assert result.exit_code == 0
        version = importlib.metadata.version("agent-discogs")
        assert f"agent-discogs v{version}" in result.output
        assert "Auth:" in result.output
        assert "Cache:" in result.output

    @pytest.mark.parametrize(
        ("env", "auth"),
        [
            ({"DISCOGS_TOKEN": "t"}, "token (authenticated)"),
            (
                {"DISCOGS_CONSUMER_KEY": "k", "DISCOGS_CONSUMER_SECRET": "s"},
                "consumer (authenticated app, not a user: no price suggestions)",
            ),
            (
                {
                    "DISCOGS_CONSUMER_KEY": "k",
                    "DISCOGS_CONSUMER_SECRET": "s",
                    "DISCOGS_ACCESS_TOKEN": "a",
                    "DISCOGS_ACCESS_TOKEN_SECRET": "b",
                },
                "oauth (authenticated)",
            ),
            ({}, "none (unauthenticated, 25 req/min)"),
        ],
        ids=["token", "consumer", "oauth", "none"],
    )
    def test_reports_the_auth_mode_in_use(
        self, env: dict[str, str], auth: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        result = CliRunner().invoke(cli, ["status"])
        assert result.exit_code == 0
        assert f"Auth: {auth}" in result.stdout.splitlines()

    def test_never_touches_the_cache(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("DISCOGS_TOKEN", "t")
        monkeypatch.setattr("agent_discogs.client._client", None)
        not_a_dir = tmp_path / "file"
        not_a_dir.write_text("")
        monkeypatch.setenv("XDG_CACHE_HOME", str(not_a_dir))
        result = CliRunner().invoke(cli, ["status"])
        assert result.exit_code == 0, result.output
        assert "Auth: token (authenticated)" in result.stdout.splitlines()

        absent = tmp_path / "absent"
        monkeypatch.setenv("XDG_CACHE_HOME", str(absent))
        result = CliRunner().invoke(cli, ["status"])
        assert result.exit_code == 0, result.output
        assert not absent.exists()


class TestAliases:
    def test_find_alias(self) -> None:
        result = CliRunner().invoke(cli, ["find", "--help"])
        assert result.exit_code == 0
        assert "--year" in result.output
        assert result.output.startswith("Usage: cli search ")  # canonical name

    def test_fetch_alias(self) -> None:
        result = CliRunner().invoke(cli, ["fetch", "--help"])
        assert result.exit_code == 0
        assert "release" in result.output
        assert result.output.startswith("Usage: cli get ")

    def test_query_alias(self) -> None:
        result = CliRunner().invoke(cli, ["query", "--help"])
        assert result.exit_code == 0
        assert "--year" in result.output
        assert result.output.startswith("Usage: cli search ")

    def test_show_alias(self) -> None:
        result = CliRunner().invoke(cli, ["show", "--help"])
        assert result.exit_code == 0
        assert "release" in result.output
        assert result.output.startswith("Usage: cli get ")


class TestSearchCommand:
    @pytest.fixture(autouse=True)
    def _patch_search(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._monkeypatch = monkeypatch
        monkeypatch.setattr("agent_discogs.commands.search.get_client", _fake_client)

    def _set_fetch_result(self, result: PageResult) -> None:
        def mock(*_a: object, **_kw: object) -> PageResult:
            return result

        self._monkeypatch.setattr("agent_discogs.commands.search.fetch_page", mock)
        self._monkeypatch.setattr("agent_discogs.pagination.fetch_page", mock)

    def test_basic_search(self) -> None:
        result_item = _fake(
            id=367113,
            type="release",
            title="The Downward Spiral",
            year="1994",
            label=["Nothing Records"],
            format=["Vinyl"],
        )
        self._set_fetch_result(
            PageResult(items=[result_item], page=1, total_items=1, total_pages=1)
        )

        result = CliRunner().invoke(cli, ["search", "downward spiral"])
        assert result.exit_code == 0
        assert "Search:" in result.output
        assert "The Downward Spiral" in result.output

    def test_search_with_type_prefix(self) -> None:
        result_item = _fake(
            id=3857,
            type="artist",
            title="Nine Inch Nails",
            year=None,
            label=None,
            format=None,
        )
        self._set_fetch_result(
            PageResult(items=[result_item], page=1, total_items=1, total_pages=1)
        )

        result = CliRunner().invoke(cli, ["search", "artist", "Nine Inch Nails"])
        assert result.exit_code == 0
        assert "Nine Inch Nails" in result.output

    def test_search_with_filters(self) -> None:
        self._set_fetch_result(
            PageResult(items=[], page=1, total_items=0, total_pages=1)
        )
        result = CliRunner().invoke(
            cli,
            ["search", "test", "--year", "1994", "--genre", "Rock", "--country", "US"],
        )
        assert result.exit_code == 0

    def test_search_empty_query_after_type_no_filters(self) -> None:
        result = CliRunner().invoke(cli, ["search", "release"])
        assert result.exit_code == 1
        assert "No search query or filters" in result.output

    def test_search_no_args_no_filters(self) -> None:
        result = CliRunner().invoke(cli, ["search"])
        assert result.exit_code == 1
        assert "No search query or filters" in result.output

        result = CliRunner().invoke(cli, ["search", "--json"])
        assert result.exit_code == 1
        assert json.loads(result.output)["error"]["code"] == "invalid_argument"

    def test_search_filter_only(self) -> None:
        """Filter-only search without query text (e.g. --catno)."""
        self._set_fetch_result(
            PageResult(items=[], page=1, total_items=0, total_pages=1)
        )
        result = CliRunner().invoke(cli, ["search", "release", "--catno", "INT-92346"])
        assert result.exit_code == 0

    def test_search_error(self) -> None:
        def _raise(*_a: object, **_kw: object) -> None:
            raise RuntimeError("connection failed")

        self._monkeypatch.setattr("agent_discogs.pagination.fetch_page", _raise)
        result = CliRunner().invoke(cli, ["search", "test query"])
        assert result.exit_code == 1
        assert "error" in result.output.lower()

    def test_search_header_marks_the_filtered_bound(self) -> None:
        """Default (official) search filters client-side; the total is a bound."""
        result_item = _fake(
            id=1, type="release", title="Test", year=None, label=None, format=None
        )
        self._set_fetch_result(
            PageResult(items=[result_item], page=1, total_items=50, total_pages=10)
        )
        result = CliRunner().invoke(cli, ["search", "test"])
        assert result.exit_code == 0
        assert "of ≤50 results" in result.output

    def _forbid_fetch(self) -> None:
        def _boom(*_a: object, **_kw: object) -> None:
            raise AssertionError("API call made before flag validation")

        self._monkeypatch.setattr("agent_discogs.commands.search.fetch_page", _boom)
        self._monkeypatch.setattr("agent_discogs.pagination.fetch_page", _boom)

    def test_search_page_rejected_on_filtered_path(self) -> None:
        self._forbid_fetch()
        result = CliRunner().invoke(cli, ["search", "test", "--page", "2"])
        assert result.exit_code == 1
        assert "--page is not available" in result.output
        assert "--release-type all" in result.output

    def test_search_after_rejected_on_server_side_path(self) -> None:
        self._forbid_fetch()
        result = CliRunner().invoke(
            cli, ["search", "artist", "test", "--after", "2:1.0"]
        )
        assert result.exit_code == 1
        assert "--after only continues" in result.output

    def test_search_invalid_cursor_rejected_before_request(self) -> None:
        self._forbid_fetch()
        result = CliRunner().invoke(cli, ["search", "test", "--after", "garbage"])
        assert result.exit_code == 1
        assert "Invalid --after cursor" in result.output

    def test_search_server_side_pagination(self) -> None:
        """--release-type all pages server-side with --page and no ≤ bound."""
        self._set_fetch_result(
            PageResult(items=[], page=3, total_items=15, total_pages=3)
        )
        result = CliRunner().invoke(
            cli, ["search", "test", "--release-type", "all", "--page", "3"]
        )
        assert result.exit_code == 0
        assert "(page 3, 0 of 15 results)" in result.output
        assert "Next page:" not in result.output

    def test_search_capped_scan_footer(self) -> None:
        """Sparse filter: 5 API calls, no match, Continue scan footer."""
        bootleg = _fake(
            id=1,
            type="release",
            title="Boot",
            year=None,
            label=None,
            format=["Unofficial Release"],
        )
        self._set_fetch_result(
            PageResult(items=[bootleg], page=1, total_items=500, total_pages=34)
        )
        result = CliRunner().invoke(cli, ["search", "test"])
        assert result.exit_code == 0
        assert (
            "(page 1, 0 of ≤500 results; scan capped at 5 API calls)" in result.output
        )
        assert "\nContinue scan: " in result.output

    def test_search_all_filters(self) -> None:
        self._set_fetch_result(
            PageResult(items=[], page=1, total_items=0, total_pages=1)
        )
        result = CliRunner().invoke(
            cli,
            [
                "search",
                "test",
                "--artist",
                "NIN",
                "--barcode",
                "123",
                "--catno",
                "ABC",
                "--format",
                "Vinyl",
                "--label",
                "Nothing",
                "--style",
                "Industrial",
                "--limit",
                "10",
                "--release-type",
                "all",
            ],
        )
        assert result.exit_code == 0

    def test_release_type_official_filters_unofficial(self) -> None:
        """Default --release-type official excludes unofficial releases."""
        official = _fake(
            id=1,
            type="release",
            title="Official",
            year=None,
            label=None,
            format=["Vinyl"],
        )
        unofficial = _fake(
            id=2,
            type="release",
            title="Bootleg",
            year=None,
            label=None,
            format=["Vinyl", "Unofficial Release"],
        )
        self._set_fetch_result(
            PageResult(
                items=[official, unofficial], page=1, total_items=2, total_pages=1
            )
        )
        result = CliRunner().invoke(cli, ["search", "test"])
        assert result.exit_code == 0
        assert "Official" in result.output
        assert "Bootleg" not in result.output

    def test_release_type_all_shows_everything(self) -> None:
        official = _fake(
            id=1,
            type="release",
            title="Official",
            year=None,
            label=None,
            format=["Vinyl"],
        )
        unofficial = _fake(
            id=2,
            type="release",
            title="Bootleg",
            year=None,
            label=None,
            format=["Vinyl", "Unofficial Release"],
        )
        self._set_fetch_result(
            PageResult(
                items=[official, unofficial], page=1, total_items=2, total_pages=1
            )
        )
        result = CliRunner().invoke(cli, ["search", "test", "--release-type", "all"])
        assert result.exit_code == 0
        assert "Official" in result.output
        assert "Bootleg" in result.output

    def test_release_type_header_echo_only_when_it_applies(self) -> None:
        """Header shows release-type when non-default on release/master searches,
        never on artist/label searches where the option is a no-op."""
        item = _fake(
            id=1, type="release", title="Test", year=None, label=None, format=["Vinyl"]
        )
        self._set_fetch_result(
            PageResult(items=[item], page=1, total_items=1, total_pages=1)
        )
        result = CliRunner().invoke(cli, ["search", "test", "--release-type", "all"])
        assert 'Search: all "test" release-type=all' in result.output

        result = CliRunner().invoke(cli, ["search", "release", "test"])
        assert "release-type" not in result.output.split("\n")[0]

        artist = _fake(id=1, type="artist", title="Test")
        self._set_fetch_result(
            PageResult(items=[artist], page=1, total_items=1, total_pages=1)
        )
        result = CliRunner().invoke(
            cli, ["search", "artist", "test", "--release-type", "unofficial"]
        )
        assert result.exit_code == 0
        assert "release-type" not in result.output.split("\n")[0]

    def test_artist_search_skips_release_filter(self) -> None:
        """Artist type search uses direct fetch, not filtered overfetch."""
        pages: list[tuple[int, int]] = []

        def _capture_fetch(_listing: object, *, page: int, per_page: int) -> PageResult:
            pages.append((page, per_page))
            item = _fake(
                id=1, type="artist", title="Test", year=None, label=None, format=None
            )
            return PageResult(items=[item], page=1, total_items=1, total_pages=1)

        self._monkeypatch.setattr(
            "agent_discogs.commands.search.fetch_page", _capture_fetch
        )
        result = CliRunner().invoke(cli, ["search", "artist", "test"])
        assert result.exit_code == 0
        # Direct path uses per_page=limit (5), not limit*3 (15)
        assert pages == [(1, 5)]

    def test_filtered_search_continues_from_cursor(self) -> None:
        """--after resumes at the API page and offset encoded in the cursor."""
        seen: list[tuple[int, int]] = []

        def _mock_fetch(_listing: object, *, page: int, per_page: int) -> PageResult:
            seen.append((page, per_page))
            items = [
                _fake(
                    id=page * 10 + i,
                    type="release",
                    title=f"Official {page}-{i}",
                    year=None,
                    label=None,
                    format=["Vinyl"],
                )
                for i in range(9)
            ]
            return PageResult(items=items, page=page, total_items=30, total_pages=4)

        self._monkeypatch.setattr("agent_discogs.pagination.fetch_page", _mock_fetch)
        result = CliRunner().invoke(
            cli, ["search", "test", "--limit", "3", "--after", "2:1.3"]
        )
        assert result.exit_code == 0
        assert seen == [(1, 9)]
        assert "(page 2, 3 of ≤30 results)" in result.output
        assert "Official 1-3" in result.output
        assert "Official 1-5" in result.output
        assert "Official 1-2" not in result.output

    def test_filtered_search_multi_page(self) -> None:
        """Filtering consumes multiple API pages to fill the limit."""
        call_count = 0

        def _mock_fetch(*_a: object, **_kw: object) -> PageResult:
            nonlocal call_count
            call_count += 1
            # Each API page has 1 official + 1 unofficial
            official = _fake(
                id=call_count * 10,
                type="release",
                title=f"Official {call_count}",
                year=None,
                label=None,
                format=["Vinyl"],
            )
            unofficial = _fake(
                id=call_count * 10 + 1,
                type="release",
                title=f"Bootleg {call_count}",
                year=None,
                label=None,
                format=["Unofficial Release"],
            )
            return PageResult(
                items=[official, unofficial],
                page=call_count,
                total_items=20,
                total_pages=10,
            )

        self._monkeypatch.setattr("agent_discogs.pagination.fetch_page", _mock_fetch)
        result = CliRunner().invoke(cli, ["search", "test", "--limit", "3"])
        assert result.exit_code == 0
        assert call_count == 3  # needed 3 API calls to get 3 official items
        assert "Official 1" in result.output
        assert "Official 2" in result.output
        assert "Official 3" in result.output
        assert "Bootleg" not in result.output


class TestPagingBounds:
    """`--limit` must fit one Discogs page (1-100) and `--page` counts from 1.
    Anything else, or a search with nothing left to search for, fails before
    the first request."""

    @pytest.fixture(autouse=True)
    def _fake_api(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.api = _FakeAPI(monkeypatch, _empty_listing)

    @pytest.mark.parametrize(
        "args",
        [
            ["search", "Limit", "--limit", "0"],
            ["search", "Limit", "--limit", "-1"],
            ["search", "Limit", "--limit", "101"],
            ["search", "Limit", "--release-type", "all", "--limit", "0"],
            ["search", "Limit", "--release-type", "all", "--page", "0"],
            ["search", "--year", ""],
            ["search", "release", "--year", "", "--label", " "],
            ["search", " ", "--artist", ""],
            ["get", "releases", "@a1", "--limit", "0"],
            ["get", "releases", "@a1", "--page", "-1"],
            ["get", "versions", "@m1", "--limit", "101"],
            ["get", "versions", "@m1", "--page", "0"],
        ],
    )
    def test_rejected_before_any_request(self, args: list[str]) -> None:
        result = CliRunner().invoke(cli, [*args, "--json"])
        assert result.exit_code == 1
        assert json.loads(result.stdout)["error"]["code"] == "invalid_argument"
        assert self.api.sends == []

    def test_text_errors_name_the_bound(self) -> None:
        result = CliRunner().invoke(cli, ["search", "Limit", "--limit", "0"])
        assert result.exit_code == 1
        assert "--limit must be between 1 and 100" in result.stderr
        result = CliRunner().invoke(cli, ["get", "versions", "@m1", "--page", "0"])
        assert result.exit_code == 1
        assert "--page must be 1 or more" in result.stderr
        assert self.api.sends == []

    @pytest.mark.parametrize("limit", ["1", "100"])
    @pytest.mark.parametrize(
        ("args", "path"),
        [
            (["search", "Limit", "--release-type", "all"], "/database/search"),
            (["get", "versions", "@m1"], "/masters/1/versions"),
            (["get", "releases", "@a1"], "/artists/1/releases"),
        ],
    )
    def test_boundary_values_page_server_side(
        self, args: list[str], path: str, limit: str
    ) -> None:
        result = CliRunner().invoke(cli, [*args, "--limit", limit, "--page", "1"])
        assert result.exit_code == 0, result.output
        (listing,) = [params for sent, params in self.api.sends if sent == path]
        assert listing["per_page"] == limit
        assert listing["page"] == "1"

    def test_whitespace_filters_do_not_count_but_real_ones_do(self) -> None:
        result = CliRunner().invoke(
            cli, ["search", "release", "--year", "", "--catno", "INT-92346"]
        )
        assert result.exit_code == 0, result.output
        assert len(self.api.sends) == 1
        params = self.api.sends[0][1]
        assert params["catno"] == "INT-92346"
        assert "year" not in params


class TestSearchSort:
    """`search release --sort date_added [--desc]` asks Discogs for date order.
    Discogs has only been seen honoring it on release searches, so anything
    else fails before the first request."""

    @pytest.fixture(autouse=True)
    def _fake_api(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.api = _FakeAPI(monkeypatch, _empty_listing)

    @pytest.mark.parametrize(("desc", "order"), [(["--desc"], "desc"), ([], "asc")])
    @pytest.mark.parametrize("release_type", ["all", "official"])
    def test_sends_sort_and_order(
        self, desc: list[str], order: str, release_type: str
    ) -> None:
        result = CliRunner().invoke(
            cli,
            [
                "search",
                "release",
                "--artist",
                "Nine Inch Nails",
                "--release-type",
                release_type,
                "--sort",
                "date_added",
                *desc,
            ],
        )
        assert result.exit_code == 0, result.output
        assert len(self.api.sends) == 1
        params = self.api.sends[0][1]
        assert (params["sort"], params["sort_order"]) == ("date_added", order)

    def test_unsorted_search_sends_no_sort(self) -> None:
        result = CliRunner().invoke(cli, ["search", "release", "Closer"])
        assert result.exit_code == 0, result.output
        assert not {"sort", "sort_order"} & self.api.sends[0][1].keys()

    @pytest.mark.parametrize(
        "args",
        [
            ["search", "artist", "Nine Inch Nails", "--sort", "date_added"],
            ["search", "Nine Inch Nails", "--sort", "date_added"],
            ["search", "master", "Closer", "--sort", "date_added", "--desc"],
            ["search", "label", "Nothing", "--sort", "date_added"],
            ["search", "--catno", "INT-92346", "--sort", "date_added"],
            ["search", "release", "Closer", "--desc"],
            ["search", "Closer", "--desc"],
        ],
    )
    def test_rejected_before_any_request(self, args: list[str]) -> None:
        result = CliRunner().invoke(cli, [*args, "--json"])
        assert result.exit_code == 1
        assert json.loads(result.stdout)["error"]["code"] == "invalid_argument"
        assert self.api.sends == []

    def test_header_shows_the_applied_sort(self) -> None:
        result = CliRunner().invoke(
            cli, ["search", "release", "Closer", "--sort", "date_added", "--desc"]
        )
        header = result.stdout.splitlines()[0]
        assert "sort=date_added" in header
        assert "desc" in header


class TestUnofficialServerSide:
    """`--release-type unofficial` asks Discogs for `format=Unofficial Release`
    and pages server-side. `official`, and `unofficial` combined with
    `--format`, stay client-side scans."""

    @pytest.fixture(autouse=True)
    def _fake_api(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.api = _FakeAPI(monkeypatch, _Catalogue(613))

    @pytest.mark.parametrize("kind", [["release"], ["master"], []])
    def test_unofficial_is_one_exact_server_side_page(self, kind: list[str]) -> None:
        result = CliRunner().invoke(
            cli, ["search", *kind, "Downward", "--release-type", "unofficial"]
        )
        assert result.exit_code == 0, result.output
        assert len(self.api.sends) == 1
        params = self.api.sends[0][1]
        assert params["format"] == "Unofficial Release"
        assert params["per_page"] == "5"  # no overfetch
        assert "(page 1, 5 of 613 results)" in result.stdout  # exact, not ≤

    def test_unofficial_accepts_page(self) -> None:
        result = CliRunner().invoke(
            cli,
            [
                "search",
                "release",
                "Downward",
                "--release-type",
                "unofficial",
                "--page",
                "2",
            ],
        )
        assert result.exit_code == 0, result.output
        assert self.api.sends[0][1]["page"] == "2"
        assert "(page 2, 5 of 613 results)" in result.stdout

    def test_official_still_scans_client_side(self) -> None:
        result = CliRunner().invoke(cli, ["search", "release", "Downward"])
        assert result.exit_code == 0, result.output
        params = self.api.sends[0][1]
        assert "format" not in params
        assert params["per_page"] == "15"
        assert "of ≤613 results" in result.stdout

    def test_unofficial_with_format_stays_client_side(self) -> None:
        args = ["search", "release", "Downward", "--release-type", "unofficial"]
        result = CliRunner().invoke(cli, [*args, "--format", "Vinyl"])
        assert result.exit_code == 0, result.output
        params = self.api.sends[0][1]
        assert params["format"] == "Vinyl"
        assert params["per_page"] == "15"
        assert "of ≤613 results" in result.stdout
        # Rows 4 and 5 are official in the catalogue; the scan skips them.
        assert [i for i, _ in _ROW.findall(result.stdout)] == ["0", "1", "2", "3", "6"]

        sent = len(self.api.sends)
        result = CliRunner().invoke(
            cli, [*args, "--format", "Vinyl", "--page", "2", "--json"]
        )
        assert result.exit_code == 1
        assert json.loads(result.stdout)["error"]["code"] == "invalid_argument"
        assert len(self.api.sends) == sent


class TestGetCommand:
    @pytest.fixture(autouse=True)
    def _patch_get(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._monkeypatch = monkeypatch

    def _set_client(self, client: SimpleNamespace) -> None:
        self._monkeypatch.setattr(
            "agent_discogs.commands.get.get_client", lambda: client
        )

    def test_get_artist(self) -> None:
        artist = _fake(
            id=3857,
            name="Nine Inch Nails",
            profile="Industrial",
            urls=None,
            members=None,
        )
        self._set_client(_fake_client(artists_get=lambda _id: artist))
        result = CliRunner().invoke(cli, ["get", "artist", "@a3857"])
        assert result.exit_code == 0
        assert "Nine Inch Nails" in result.output

    def test_get_label(self) -> None:
        label = _fake(
            id=2919,
            name="Nothing Records",
            profile="Label",
            urls=None,
            sub_labels=None,
        )
        self._set_client(_fake_client(labels_get=lambda _id: label))
        result = CliRunner().invoke(cli, ["get", "label", "@l2919"])
        assert result.exit_code == 0
        assert "Nothing Records" in result.output

    def test_get_master(self) -> None:
        artist = _fake(name="NIN", join=None)
        master = _fake(
            id=4917,
            title="The Downward Spiral",
            year=1994,
            artists=[artist],
            genres=["Electronic"],
            styles=None,
            main_release=367113,
            num_for_sale=None,
            lowest_price=None,
            tracklist=None,
        )
        self._set_client(_fake_client(masters_get=lambda _id: master))
        result = CliRunner().invoke(cli, ["get", "master", "@m4917"])
        assert result.exit_code == 0
        assert "The Downward Spiral" in result.output

    def test_get_release(self) -> None:
        release = _fake(
            id=367113,
            title="The Downward Spiral",
            year=1994,
            artists=None,
            community=None,
            labels=None,
            formats=None,
            genres=None,
            styles=None,
            num_for_sale=None,
            lowest_price=None,
            master_id=None,
            tracklist=None,
        )
        self._set_client(_fake_client(releases_get=lambda _id: release))
        result = CliRunner().invoke(cli, ["get", "release", "@r367113"])
        assert result.exit_code == 0
        assert "The Downward Spiral" in result.output

    def test_get_price(self) -> None:
        suggestions = PriceSuggestions.model_validate(
            {"Mint (M)": {"currency": "EUR", "value": 100.0}}
        )
        stats = MarketplaceReleaseStats.model_validate(
            {"num_for_sale": 100, "lowest_price": {"currency": "GBP", "value": 5.0}}
        )
        release = _fake(
            id=367113,
            title="TDS",
            year=1994,
            artists=None,
            price_suggestions=_fake(get=lambda: suggestions),
            marketplace_stats=_fake(get=lambda: stats),
        )
        self._set_client(_fake_client(releases_get=lambda _id: release))
        result = CliRunner().invoke(cli, ["get", "price", "@r367113"])
        assert result.exit_code == 0
        assert "Price Guide:" in result.output
        assert "EUR 100.00" in result.output
        assert "Lowest: GBP 5.00" in result.output
        assert "$" not in result.output

    def test_get_credits(self) -> None:
        release = _fake(
            id=847868,
            title="The Downward Spiral",
            extra_artists=[
                _fake(id=20661, name="Flood", role="Producer [Production]", tracks="")
            ],
        )
        self._set_client(_fake_client(releases_get=lambda _id: release))
        result = CliRunner().invoke(cli, ["get", "credits", "@r847868"])
        assert result.exit_code == 0
        assert 'Credits: @r847868 "The Downward Spiral" (1)' in result.output
        assert "Producer [Production]: Flood [@a20661]" in result.output

    def test_get_identifiers_and_ids_alias(self) -> None:
        release = _fake(
            id=847868,
            title="The Downward Spiral",
            identifiers=[
                _fake(type="Barcode", value="765449234620", description="Scanned")
            ],
        )
        self._set_client(_fake_client(releases_get=lambda _id: release))
        full = CliRunner().invoke(cli, ["get", "identifiers", "@r847868"])
        alias = CliRunner().invoke(cli, ["get", "ids", "@r847868"])
        assert full.exit_code == 0
        assert "Barcode: 765449234620 (Scanned)" in full.output
        assert alias.output == full.output

    def test_get_credits_rejects_non_release_ref(self) -> None:
        self._set_client(_fake_client())
        result = CliRunner().invoke(cli, ["get", "credits", "@a3857"])
        assert result.exit_code == 1
        assert "@a3857 is an artist, not a release" in result.output
        assert "Use a release ref or ID with 'get credits'" in result.output

    def test_get_tracklist(self) -> None:
        track = _fake(
            position="A1", title="Mr. Self Destruct", duration="4:09", type=None
        )
        release = _fake(
            id=367113,
            title="TDS",
            year=1994,
            artists=None,
            tracklist=[track],
        )
        self._set_client(_fake_client(releases_get=lambda _id: release))
        result = CliRunner().invoke(cli, ["get", "tracklist", "@r367113"])
        assert result.exit_code == 0
        assert "A1. Mr. Self Destruct" in result.output

    def test_get_releases(self) -> None:
        artist = _listed(id=3857, name="Nine Inch Nails")
        self._set_client(_fake_client(artists_get=lambda _id: artist))

        rel = _fake(id=4917, type="master", title="TDS", year=1994, role="Main")
        self._monkeypatch.setattr(
            "agent_discogs.commands.get.fetch_page",
            lambda *_a, **_kw: PageResult(
                items=[rel], page=1, total_items=100, total_pages=20
            ),
        )

        result = CliRunner().invoke(cli, ["get", "releases", "@a3857"])
        assert result.exit_code == 0
        assert "Nine Inch Nails" in result.output
        assert "Next page:" in result.output

    def test_get_versions_master(self) -> None:
        master = _listed(id=4917, title="The Downward Spiral")
        self._set_client(_fake_client(masters_get=lambda _id: master))

        ver = _fake(
            id=367113,
            released="1994",
            country="US",
            label="Nothing",
            catalog_number="INT-92346",
            format="Vinyl",
        )
        self._monkeypatch.setattr(
            "agent_discogs.commands.get.fetch_page",
            lambda *_a, **_kw: PageResult(
                items=[ver], page=1, total_items=500, total_pages=100
            ),
        )

        result = CliRunner().invoke(cli, ["get", "versions", "@m4917"])
        assert result.exit_code == 0
        assert "Versions of" in result.output
        assert "Next page:" in result.output

    def test_get_versions_release_smart_resolution(self) -> None:
        """Release ref with master_id resolves to master versions."""
        release = _fake(id=367113, master_id=4917, title="The Downward Spiral")
        self._set_client(_fake_client(releases_get=lambda _id: release))

        ver = _fake(
            id=999,
            released="2020",
            country="UK",
            label="L",
            catalog_number="C",
            format="CD",
        )
        self._monkeypatch.setattr(
            "agent_discogs.commands.get.fetch_page",
            lambda *_a, **_kw: PageResult(
                items=[ver], page=1, total_items=1, total_pages=1
            ),
        )

        result = CliRunner().invoke(cli, ["get", "versions", "@r367113"])
        assert result.exit_code == 0
        assert "Versions of" in result.output

    def test_get_versions_release_no_master(self) -> None:
        """Release ref with no master_id produces error."""
        release = _fake(id=367113, master_id=None, title="Some Single")
        self._set_client(_fake_client(releases_get=lambda _id: release))

        result = CliRunner().invoke(cli, ["get", "versions", "@r367113"])
        assert result.exit_code == 1
        assert "no master" in result.output.lower()

    def test_get_type_mismatch(self) -> None:
        self._set_client(_fake_client())
        result = CliRunner().invoke(cli, ["get", "artist", "@r367113"])
        assert result.exit_code == 1
        assert "not an artist" in result.output.lower()

    def test_get_error_handling(self) -> None:
        def _raise(_id: int) -> None:
            raise RuntimeError("API down")

        self._set_client(_fake_client(artists_get=_raise))
        result = CliRunner().invoke(cli, ["get", "artist", "@a1"])
        assert result.exit_code == 1
        assert "error" in result.output.lower()

    def test_get_release_verbose(self) -> None:
        artist = _fake(id=3857, name="Nine Inch Nails", join=None)
        label = _fake(id=33244, name="Rhino Records (2)", catalog_number="R2 75933")
        track = _fake(
            position="1",
            title="Hamburger Lady",
            duration="4:12",
            type=None,
            artists=[_fake(id=12589, name="Throbbing Gristle", join=None)],
        )
        release = _fake(
            id=367113,
            title="The Downward Spiral",
            year=1994,
            artists=[artist],
            community=None,
            labels=[label],
            formats=None,
            genres=None,
            styles=None,
            num_for_sale=None,
            lowest_price=None,
            master_id=None,
            tracklist=[track],
            notes="Pressed at Sterling Sound.",
        )
        self._set_client(_fake_client(releases_get=lambda _id: release))
        result = CliRunner().invoke(cli, ["get", "release", "@r367113", "--verbose"])
        assert result.exit_code == 0
        assert "Notes: Pressed at Sterling Sound." in result.output
        assert "[@a3857]" in result.output
        assert "[@l33244]" in result.output
        assert "[@a12589]" in result.output

    def test_get_release_no_verbose_hides_notes(self) -> None:
        release = _fake(
            id=367113,
            title="The Downward Spiral",
            year=1994,
            artists=None,
            community=None,
            labels=None,
            formats=None,
            genres=None,
            styles=None,
            num_for_sale=None,
            lowest_price=None,
            master_id=None,
            tracklist=None,
            notes="Pressed at Sterling Sound.",
        )
        self._set_client(_fake_client(releases_get=lambda _id: release))
        result = CliRunner().invoke(cli, ["get", "release", "@r367113"])
        assert result.exit_code == 0
        assert "Notes:" not in result.output

    def test_get_release_compact_text_and_json(self) -> None:
        release = _fake_model(
            id=847868,
            title="The Downward Spiral",
            year=1994,
            artists=None,
            community=None,
            labels=None,
            formats=None,
            genres=None,
            styles=None,
            num_for_sale=None,
            lowest_price=None,
            master_id=None,
            tracklist=[
                _fake(
                    position="1", title="A", duration="4:30", type=None, artists=None
                ),
                _fake(
                    position="2", title="B", duration="4:24", type=None, artists=None
                ),
            ],
        )
        self._set_client(_fake_client(releases_get=lambda _id: release))
        text = CliRunner().invoke(cli, ["get", "release", "@r847868", "-c"])
        assert text.exit_code == 0
        assert "Tracks: 2 (8:54)" in text.output
        assert "Tracklist:" not in text.output

        data = json.loads(
            CliRunner()
            .invoke(cli, ["get", "release", "@r847868", "-c", "--json"])
            .output
        )
        assert data["tracks"] == "2 (8:54)"
        assert "tracklist" not in data

    def test_get_with_raw_id(self) -> None:
        release = _fake(
            id=367113,
            title="Test",
            year=None,
            artists=None,
            community=None,
            labels=None,
            formats=None,
            genres=None,
            styles=None,
            num_for_sale=None,
            lowest_price=None,
            master_id=None,
            tracklist=None,
        )
        self._set_client(_fake_client(releases_get=lambda _id: release))
        result = CliRunner().invoke(cli, ["get", "release", "367113"])
        assert result.exit_code == 0

    def test_get_releases_role_filter(self) -> None:
        """--role filters releases by credit role (case-insensitive substring)."""
        artist = _listed(id=3857, name="Nine Inch Nails")
        self._set_client(_fake_client(artists_get=lambda _id: artist))

        main_rel = _fake(id=100, type="master", title="TDS", year=1994, role="Main")
        remix_rel = _fake(
            id=200, type="master", title="Remix Album", year=1995, role="Remix"
        )
        compound_rel = _fake(
            id=300,
            type="master",
            title="Collab",
            year=1996,
            role="Producer, Written-By",
        )
        self._monkeypatch.setattr(
            "agent_discogs.pagination.fetch_page",
            lambda *_a, **_kw: PageResult(
                items=[main_rel, remix_rel, compound_rel],
                page=1,
                total_items=3,
                total_pages=1,
            ),
        )

        result = CliRunner().invoke(
            cli, ["get", "releases", "@a3857", "--role", "producer"]
        )
        assert result.exit_code == 0
        assert "Collab" in result.output
        assert "TDS" not in result.output
        assert "Remix Album" not in result.output

    def test_get_releases_role_no_match(self) -> None:
        """--role with no matching releases shows empty results."""
        artist = _listed(id=3857, name="Nine Inch Nails")
        self._set_client(_fake_client(artists_get=lambda _id: artist))

        rel = _fake(id=100, type="master", title="TDS", year=1994, role="Main")
        self._monkeypatch.setattr(
            "agent_discogs.pagination.fetch_page",
            lambda *_a, **_kw: PageResult(
                items=[rel], page=1, total_items=1, total_pages=1
            ),
        )

        result = CliRunner().invoke(
            cli, ["get", "releases", "@a3857", "--role", "DJ Mix"]
        )
        assert result.exit_code == 0
        assert "TDS" not in result.output

    def test_get_releases_role_header_marks_the_filtered_bound(self) -> None:
        """--role filters client-side, so the total is an upper bound."""
        artist = _listed(id=3857, name="Nine Inch Nails")
        self._set_client(_fake_client(artists_get=lambda _id: artist))

        rel = _fake(id=100, type="master", title="TDS", year=1994, role="Main")
        self._monkeypatch.setattr(
            "agent_discogs.pagination.fetch_page",
            lambda *_a, **_kw: PageResult(
                items=[rel], page=1, total_items=50, total_pages=10
            ),
        )

        result = CliRunner().invoke(
            cli, ["get", "releases", "@a3857", "--role", "Main"]
        )
        assert result.exit_code == 0
        assert "of ≤50 results" in result.output

    def _set_exploding_client(self) -> None:
        """Any resource access proves validation ran after I/O started."""

        def _boom(_id: int) -> None:
            raise AssertionError("API call made before flag validation")

        self._set_client(
            _fake_client(
                artists_get=_boom,
                labels_get=_boom,
                masters_get=_boom,
                releases_get=_boom,
            )
        )
        self._monkeypatch.setattr("agent_discogs.pagination.fetch_page", _boom)
        self._monkeypatch.setattr("agent_discogs.commands.get.fetch_page", _boom)

    def test_get_releases_role_rejects_page_before_io(self) -> None:
        self._set_exploding_client()
        result = CliRunner().invoke(
            cli, ["get", "releases", "@a3857", "--role", "Main", "--page", "2"]
        )
        assert result.exit_code == 1
        assert "--page is not available" in result.output

    @pytest.mark.parametrize(
        "argv",
        [
            ["get", "releases", "@a3857", "--after", "2:1.0"],
            ["get", "versions", "@m3719", "--after", "2:1.0"],
            ["get", "release", "@r847868", "--after", "2:1.0"],
            ["get", "artist", "@a3857", "--after", "2:1.0"],
        ],
    )
    def test_get_after_rejected_unless_role_filtered(self, argv: list[str]) -> None:
        self._set_exploding_client()
        result = CliRunner().invoke(cli, argv)
        assert result.exit_code == 1
        assert "--after only continues" in result.output

    def test_get_invalid_cursor_rejected_before_io(self) -> None:
        self._set_exploding_client()
        result = CliRunner().invoke(
            cli, ["get", "releases", "@a3857", "--role", "Main", "--after", "nope"]
        )
        assert result.exit_code == 1
        assert "Invalid --after cursor" in result.output

    def test_get_versions_with_filters(self) -> None:
        master = _listed(id=4917, title="TDS")
        self._set_client(_fake_client(masters_get=lambda _id: master))
        self._capture_fetch()

        result = CliRunner().invoke(
            cli,
            [
                "get",
                "versions",
                "@m4917",
                "--country",
                "US",
                "--format",
                "Vinyl",
                "--label",
                "Nothing",
            ],
        )
        assert result.exit_code == 0
        assert master.versions.list.calls == [
            {
                "country": "US",
                "format": "Vinyl",
                "label": "Nothing",
                "released": None,
                "page": 1,
                "per_page": 5,
            }
        ]

    def _capture_fetch(self) -> None:
        """Fake `fetch_page` to request its page from the listing it is given,
        which a fake listing records, and return an empty page."""

        def _fetch(listing: Any, *, page: int, per_page: int) -> PageResult:
            listing(page=page, per_page=per_page)
            return PageResult(items=[], page=1, total_items=0, total_pages=1)

        self._monkeypatch.setattr("agent_discogs.commands.get.fetch_page", _fetch)
        self._monkeypatch.setattr("agent_discogs.pagination.fetch_page", _fetch)

    def test_get_releases_for_a_label(self) -> None:
        label = _listed(id=647, name="Nothing Records")
        self._set_client(_fake_client(labels_get=lambda _id: label))
        rel = _fake_model(
            id=4401,
            title="Pretty Hate Machine",
            artist="Nine Inch Nails",
            year=0,
            catalog_number="0694903742",
            format="CD, Album",
        )

        def _fetch(listing: Any, *, page: int, per_page: int) -> PageResult:
            listing(page=page, per_page=per_page)
            return PageResult(items=[rel], page=1, total_items=2718, total_pages=544)

        self._monkeypatch.setattr("agent_discogs.commands.get.fetch_page", _fetch)
        result = CliRunner().invoke(cli, ["get", "releases", "@l647"])
        assert result.exit_code == 0
        assert label.releases.list.calls == [{"page": 1, "per_page": 5}]
        assert 'Releases on @l647 "Nothing Records" (page 1, 1 of 2,718 results)' in (
            result.output
        )
        assert (
            '@r4401 "Pretty Hate Machine" by Nine Inch Nails · 0694903742 · CD, Album'
            in result.output
        )

        data = json.loads(
            CliRunner().invoke(cli, ["get", "--json", "releases", "@l647"]).output
        )
        assert data["results"] == [
            {
                "ref": "@r4401",
                "title": "Pretty Hate Machine",
                "artist": "Nine Inch Nails",
                "catno": "0694903742",
                "format": "CD, Album",
            }
        ]  # year 0 is "unknown" on Discogs and is dropped

    def test_get_releases_sort_reaches_api(self) -> None:
        artist = _listed(id=3857, name="NIN")
        self._set_client(_fake_client(artists_get=lambda _id: artist))
        self._capture_fetch()
        result = CliRunner().invoke(
            cli, ["get", "releases", "@a3857", "--sort", "year", "--desc"]
        )
        assert result.exit_code == 0
        assert artist.releases.list.calls == [
            {"sort": "year", "sort_order": "desc", "page": 1, "per_page": 5}
        ]

    def test_get_releases_role_with_sort_passes_sort_to_scan(self) -> None:
        artist = _listed(id=3857, name="NIN")
        self._set_client(_fake_client(artists_get=lambda _id: artist))
        self._capture_fetch()
        result = CliRunner().invoke(
            cli, ["get", "releases", "@a3857", "--role", "Main", "--sort", "title"]
        )
        assert result.exit_code == 0
        # The client-side scan overfetches.
        assert artist.releases.list.calls == [
            {"sort": "title", "sort_order": "asc", "page": 1, "per_page": 15}
        ]

    def test_get_versions_year_and_sort_reach_api(self) -> None:
        master = _listed(id=3719, title="TDS")
        self._set_client(_fake_client(masters_get=lambda _id: master))
        self._capture_fetch()
        result = CliRunner().invoke(
            cli,
            ["get", "versions", "@m3719", "--year", "1994", "--sort", "released"],
        )
        assert result.exit_code == 0
        assert master.versions.list.calls == [
            {
                "country": None,
                "format": None,
                "label": None,
                "released": "1994",
                "sort": "released",
                "sort_order": "asc",
                "page": 1,
                "per_page": 5,
            }
        ]

    @pytest.mark.parametrize(
        ("argv", "message"),
        [
            (
                ["get", "releases", "@l647", "--role", "Main"],
                "Label catalogues cannot be role-filtered or sorted",
            ),
            (
                ["get", "releases", "@l647", "--sort", "year"],
                "Label catalogues cannot be role-filtered or sorted",
            ),
            (
                ["get", "versions", "@m3719", "--sort", "year"],
                "--sort 'year' is not valid for 'get versions'. Keys: released,",
            ),
            (
                ["get", "release", "@r847868", "--sort", "title"],
                "--sort does not apply to 'get release'.",
            ),
            (["get", "releases", "@a3857", "--desc"], "--desc needs --sort <key>."),
            (
                ["get", "releases", "@a3857", "--year", "1994"],
                "--year filters versions and label catalogues",
            ),
            (
                ["get", "release", "@r847868", "--year", "1994"],
                "--year does not apply to 'get release'.",
            ),
            (
                ["get", "releases", "@l647", "--year", "1999", "--page", "2"],
                "--page is not available while results are filtered client-side",
            ),
            (
                ["get", "releases", "@l647", "--after", "2:1.0"],
                "--after only continues a client-side filtered listing",
            ),
        ],
    )
    def test_sort_flag_errors_before_io(self, argv: list[str], message: str) -> None:
        self._set_exploding_client()
        result = CliRunner().invoke(cli, argv)
        assert result.exit_code == 1
        assert message in result.output

    @pytest.mark.parametrize(
        ("argv", "message"),
        [
            (
                ["get", "releases", "@a11", "--country", "FR", "--format", "Vinyl"],
                (
                    "--country, --format do not apply to 'get releases'. "
                    "'get releases' takes --after, --desc, --limit, --page, --role, "
                    "--sort, --year."
                ),
            ),
            (
                ["get", "releases", "@l647", "--label", "Nothing"],
                "--label does not apply to 'get releases'.",
            ),
            (
                ["get", "versions", "@m3719", "--role", "Main"],
                "--role does not apply to 'get versions'.",
            ),
            (
                ["get", "release", "@r847868", "--role", "Main"],
                "--role does not apply to 'get release'.",
            ),
            # An explicit --limit equal to the default is still explicit.
            (
                ["get", "artist", "@a3857", "--limit", "5"],
                (
                    "--limit does not apply to 'get artist'. "
                    "'get artist' takes only --json and --full."
                ),
            ),
            (
                ["get", "label", "@l647", "--page", "2"],
                "--page does not apply to 'get label'.",
            ),
            (
                ["get", "credits", "@r847868", "--desc"],
                "--desc does not apply to 'get credits'.",
            ),
            (
                ["get", "tracklist", "@r847868", "--limit", "10", "--page", "2"],
                "--limit, --page do not apply to 'get tracklist'.",
            ),
            (
                ["get", "ids", "@r847868", "--page", "1"],
                "--page does not apply to 'get identifiers'.",
            ),
            (
                ["get", "master", "@m3719", "--verbose"],
                "--verbose does not apply to 'get master'.",
            ),
            (
                ["get", "releases", "@a3857", "-c"],
                "--compact does not apply to 'get releases'.",
            ),
            (
                ["get", "versions", "@m3719", "-v", "--compact"],
                "--compact, --verbose do not apply to 'get versions'.",
            ),
            (
                ["get", "release", "@r847868", "--limit", "5"],
                (
                    "--limit does not apply to 'get release'. "
                    "'get release' takes --compact, --verbose."
                ),
            ),
            (
                ["get", "releases", "@a1", "@a2", "--role", "Main", "--after", "2:1.0"],
                "--after continues one listing, so it takes exactly one ref (2 given).",
            ),
            (
                [
                    "get",
                    "releases",
                    "@l1",
                    "@l2",
                    "--year",
                    "1999",
                    "--after",
                    "2:1.0",
                ],
                "--after continues one listing, so it takes exactly one ref (2 given).",
            ),
        ],
    )
    @pytest.mark.parametrize("json_output", [False, True], ids=["text", "json"])
    @pytest.mark.parametrize("mode", [[], ["--cached"], ["--fresh"]], ids=str)
    def test_inapplicable_flags_rejected_before_client(
        self, argv: list[str], message: str, json_output: bool, mode: list[str]
    ) -> None:
        built: list[str] = []
        self._monkeypatch.setattr(
            "agent_discogs.client.get_client", lambda: built.append("client")
        )
        self._monkeypatch.setattr(
            "agent_discogs.commands.get.get_client", lambda: built.append("command")
        )
        args = [*mode, *argv, *(["--json"] if json_output else [])]
        result = CliRunner().invoke(cli, args)
        assert result.exit_code == 1
        assert built == []  # rejected before any client exists: no request
        if json_output:
            error = json.loads(result.output)["error"]
            assert error["code"] == "invalid_argument"
            assert message in error["message"]
        else:
            assert message in result.output

    def test_get_releases_label_year_scans_client_side_with_cursor(self) -> None:
        label = _listed(id=647, name="Nothing Records")
        self._set_client(_fake_client(labels_get=lambda _id: label))

        def _fetch(listing: Any, *, page: int, per_page: int) -> PageResult:
            listing(page=page, per_page=per_page)
            api_page = page
            rows = [
                _fake_model(
                    id=api_page * 100 + i,
                    title=f"T{api_page}-{i}",
                    artist="A",
                    year=1999 if i % 3 == 0 else 2001,
                    catalog_number="",
                    format="CD",
                )
                for i in range(6)
            ]
            return PageResult(items=rows, page=api_page, total_items=60, total_pages=10)

        self._monkeypatch.setattr("agent_discogs.pagination.fetch_page", _fetch)
        result = CliRunner().invoke(
            cli, ["get", "releases", "@l647", "--year", "1999", "--limit", "2"]
        )
        assert result.exit_code == 0
        assert label.releases.list.calls == [{"page": 1, "per_page": 6}]
        assert 'Releases on @l647 "Nothing Records" (page 1, 2 of ≤60 results)' in (
            result.output
        )
        assert '@r100 "T1-0" by A 1999 · CD' in result.output
        assert '@r103 "T1-3" by A 1999 · CD' in result.output
        assert "2001" not in result.output

        data = json.loads(
            CliRunner()
            .invoke(cli, ["get", "--json", "releases", "@l647", "--year", "1999"])
            .output
        )
        assert data["pagination"]["filtered"] is True
        assert all(r["year"] == 1999 for r in data["results"])


class TestShortcutCommands:
    def test_tracks_shortcut(self, monkeypatch: pytest.MonkeyPatch) -> None:
        track = _fake(position="1", title="Track One", duration="3:00", type=None)
        release = _fake(
            id=123,
            title="Album",
            year=2020,
            artists=None,
            tracklist=[track],
        )
        monkeypatch.setattr(
            "agent_discogs.commands.get.get_client",
            lambda: _fake_client(releases_get=lambda _id: release),
        )
        result = CliRunner().invoke(cli, ["tracks", "@r123"])
        assert result.exit_code == 0
        assert "Track One" in result.output

    def test_price_shortcut(self, monkeypatch: pytest.MonkeyPatch) -> None:
        price = _fake(value=50.00)
        release = _fake(
            id=123,
            title="Album",
            year=2020,
            artists=None,
            price_suggestions=_fake(get=lambda: _fake(conditions={"Mint (M)": price})),
            marketplace_stats=_fake(
                get=lambda: _fake(num_for_sale=10, lowest_price=None)
            ),
        )
        monkeypatch.setattr(
            "agent_discogs.commands.get.get_client",
            lambda: _fake_client(releases_get=lambda _id: release),
        )
        result = CliRunner().invoke(cli, ["price", "@r123"])
        assert result.exit_code == 0
        assert "Price Guide:" in result.output


class TestMainEntry:
    def test_main_invokes_cli(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """main() delegates to cli()."""
        calls: list[object] = []
        monkeypatch.setattr("agent_discogs.cli", lambda *a, **kw: calls.append(1))
        main()
        assert len(calls) == 1

    def test_dunder_main(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """__main__.py runs main()."""
        calls: list[object] = []
        monkeypatch.setattr("agent_discogs.main", lambda: calls.append(1))

        dunder_main = importlib.import_module("agent_discogs.__main__")
        importlib.reload(dunder_main)
        assert len(calls) >= 1


class TestMultiRef:
    """`get`, `tracks`, and `price` accept several refs: one command, N blocks."""

    @pytest.fixture(autouse=True)
    def _patch_get(self, monkeypatch: pytest.MonkeyPatch) -> None:
        releases = {
            847868: _fake_model(
                id=847868,
                title="The Downward Spiral",
                year=1994,
                artists=None,
                community=None,
                labels=[
                    _fake(id=647, name="Nothing Records", catalog_number="92346-2")
                ],
                formats=None,
                genres=None,
                styles=None,
                num_for_sale=None,
                lowest_price=None,
                master_id=None,
                tracklist=[_fake(position="1", title="A", duration="1:00", type=None)],
            ),
            12453760: _fake_model(
                id=12453760,
                title="The Downward Spiral",
                year=1994,
                artists=None,
                community=None,
                labels=[
                    _fake(id=647, name="Nothing Records", catalog_number="7 92346-2")
                ],
                formats=None,
                genres=None,
                styles=None,
                num_for_sale=None,
                lowest_price=None,
                master_id=None,
                tracklist=[_fake(position="1", title="A", duration="1:00", type=None)],
            ),
        }
        self.calls: list[int] = []

        def _get(release_id: int) -> object:
            self.calls.append(release_id)
            if release_id not in releases:
                raise NotFoundError("nope", status_code=404, response_body={})
            return releases[release_id]

        monkeypatch.setattr(
            "agent_discogs.commands.get.get_client",
            lambda: _fake_client(releases_get=_get),
        )

    def test_text_blocks_in_order_separated_by_blank_line(self) -> None:
        result = CliRunner().invoke(
            cli, ["get", "release", "@r847868", "@r12453760", "-c"]
        )
        assert result.exit_code == 0
        assert self.calls == [847868, 12453760]
        first, second = result.output.strip().split("\n\n")
        assert first.startswith("@r847868 [release]")
        assert "Label: Nothing Records [@l647] (92346-2)" in first
        assert second.startswith("@r12453760 [release]")
        assert "Label: Nothing Records [@l647] (7 92346-2)" in second

    def test_failed_ref_is_an_ordered_inline_block(self) -> None:
        """Errors are stdout blocks in ref order, blank-separated like successes."""
        result = CliRunner().invoke(
            cli,
            ["get", "release", "@r847868", "@r1", "@r12453760", "-c"],
        )
        assert result.exit_code == 1
        assert self.calls == [847868, 1, 12453760]
        blocks = result.stdout.strip().split("\n\n")
        assert len(blocks) == 3
        assert blocks[0].startswith("@r847868 [release]")
        assert blocks[1] == (
            '✗ Release @r1 not found.\n  Try: agent-discogs search "<title>"'
        )
        assert blocks[2].startswith("@r12453760 [release]")
        assert result.stderr == ""

    def test_json_list_with_error_items(self) -> None:
        result = CliRunner().invoke(
            cli, ["get", "--json", "-c", "release", "@r847868", "@r1"]
        )
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert data[0]["ref"] == "@r847868"
        assert data[0]["tracks"] == "1 (1:00)"
        assert data[1] == {
            "ref": "@r1",
            "error": {
                "code": "not_found",
                "message": "Release @r1 not found.",
                "hint": 'Try: agent-discogs search "<title>"',
                "status": 404,
            },
        }

    def test_single_ref_json_shape_is_unchanged(self) -> None:
        result = CliRunner().invoke(cli, ["get", "--json", "release", "@r847868"])
        assert isinstance(json.loads(result.output), dict)

        result = CliRunner().invoke(cli, ["get", "--json", "release", "@r1"])
        assert result.exit_code == 1
        assert set(json.loads(result.output)) == {"error"}

    def test_shortcuts_take_several_refs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        result = CliRunner().invoke(cli, ["tracks", "@r847868", "@r12453760"])
        assert result.exit_code == 0
        assert result.output.count("Tracklist: ") == 2

        def _release(release_id: int) -> object:
            if release_id == 1:
                raise NotFoundError("nope", status_code=404, response_body={})
            return _fake(
                id=release_id,
                title=f"R{release_id}",
                artists=None,
                year=None,
                price_suggestions=_fake(
                    get=lambda: _fake(
                        conditions={
                            "Mint (M)": _fake(value=float(release_id), currency="USD")
                        }
                    )
                ),
                marketplace_stats=_fake(
                    get=lambda: _fake(num_for_sale=1, lowest_price=None)
                ),
            )

        monkeypatch.setattr(
            "agent_discogs.commands.get.get_client",
            lambda: _fake_client(releases_get=_release),
        )
        result = CliRunner().invoke(cli, ["price", "@r10", "@r1", "@r20"])
        assert result.exit_code == 1
        out = result.stdout
        # Price guides contain their own blank lines, so assert order by
        # position: guide 10, then the inline error, then guide 20.
        markers = [
            out.index('Price Guide: @r10 "R10" by Unknown Artist'),
            out.index("USD 10.00"),
            out.index("\n\n✗ Price @r1 not found.\n  Try: agent-discogs search"),
            out.index('\n\nPrice Guide: @r20 "R20" by Unknown Artist'),
            out.index("USD 20.00"),
        ]
        assert markers == sorted(markers)
        assert result.stderr == ""

        data = json.loads(
            CliRunner().invoke(cli, ["price", "--json", "@r10", "@r1"]).output
        )
        assert data[0]["suggestions"] == {
            "Mint (M)": {"value": 10.0, "currency": "USD"}
        }
        assert data[1]["error"]["code"] == "not_found"

    def test_ref_cap(self) -> None:
        refs = [f"@r{n}" for n in range(11)]
        result = CliRunner().invoke(cli, ["get", "release", *refs])
        assert result.exit_code == 1
        assert "At most 10 refs per command (11 given)" in result.output
        assert self.calls == []

    def test_no_ref_is_a_usage_error(self) -> None:
        result = CliRunner().invoke(cli, ["get", "release"])
        assert result.exit_code == 2
        assert "Missing argument" in result.output


class TestExceptionHandling:
    def test_abort_propagates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _raise_abort(
            *_a: object,
            **_kw: object,
        ) -> None:
            raise click.exceptions.Abort

        monkeypatch.setattr("agent_discogs.commands.get._dispatch", _raise_abort)
        result = CliRunner().invoke(cli, ["get", "artist", "@a1"])
        assert result.exit_code != 0

    def test_keyboard_interrupt(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _raise(_id: int) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr(
            "agent_discogs.commands.get.get_client",
            lambda: _fake_client(artists_get=_raise),
        )
        result = CliRunner().invoke(cli, ["get", "artist", "@a1"])
        assert result.exit_code == 130

    def test_generic_exception_in_invoke(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Exception escaping a command with no boundary of its own -> text error.

        `get`/`search` catch everything (incl. client creation) so `--json`
        callers get an envelope; this last resort only sees the rest.
        """

        def _raise() -> str:
            raise RuntimeError("kaboom")

        monkeypatch.setattr("agent_discogs.commands.status.auth_mode", _raise)
        result = CliRunner().invoke(cli, ["status"])
        assert result.exit_code == 1
        first = result.stderr.splitlines()[0]
        assert first.startswith("✗ ")
        assert "RuntimeError" in first
        assert "kaboom" in first

    def test_broken_stdout_pipe_exits_quietly(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`agent-discogs ... | head -1`: the reader closed the pipe, which is
        not an error to report. click's EPIPE handling exits 1 silently."""

        class _ClosedPipe(io.StringIO):
            def write(self, _s: str) -> int:
                raise BrokenPipeError(errno.EPIPE, "Broken pipe")

        stderr = io.StringIO()
        monkeypatch.setattr(sys, "stdout", _ClosedPipe())
        monkeypatch.setattr(sys, "stderr", stderr)
        with pytest.raises(SystemExit) as exc:
            cli.main(["status"], prog_name="agent-discogs")
        assert exc.value.code == 1
        assert stderr.getvalue() == ""

    def test_client_failure_is_inside_the_json_boundary(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _raise() -> None:
            raise RuntimeError("client setup failed")

        monkeypatch.setattr("agent_discogs.commands.get.get_client", _raise)
        result = CliRunner().invoke(cli, ["get", "--json", "artist", "@a1"])
        assert result.exit_code == 1
        error = json.loads(result.output)["error"]
        assert error["code"] == "unexpected"
        assert "RuntimeError" in error["message"]
        assert "client setup failed" in error["message"]

        monkeypatch.setattr("agent_discogs.commands.search.get_client", _raise)
        result = CliRunner().invoke(cli, ["search", "--json", "x"])
        assert json.loads(result.output)["error"]["code"] == "unexpected"


def _fake_model(**kwargs: object) -> SimpleNamespace:
    """Fake SDK model with model_dump() support (any `mode`)."""
    ns = SimpleNamespace(**kwargs)
    ns.model_dump = lambda **_: dict(kwargs)
    return ns


def _via_json(data: object) -> object:
    """What a `--full` dump of a fake looks like after `default=str`."""
    return json.loads(json.dumps(data, default=str))


def _mock_api(
    monkeypatch: pytest.MonkeyPatch, module: str, routes: dict[str, object]
) -> None:
    """Point `module`'s `get_client` at a real SDK client whose transport
    answers from `routes`, so the command sees the SDK's own models instead of
    fakes. A route maps a URL path to a JSON body, or to `(status, body)` with
    a text or JSON body; any other path is a 404. No retries, so a 5xx fails
    at once."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        route = routes.get(request.url.path)
        if route is None:
            return httpx2.Response(404, json={"message": "Resource not found."})
        status, body = route if isinstance(route, tuple) else (200, route)
        if isinstance(body, str):
            return httpx2.Response(status, text=body)
        return httpx2.Response(status, json=body)

    client = Discogs(
        max_retries=0,
        http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
    )
    monkeypatch.setattr(f"agent_discogs.commands.{module}.get_client", lambda: client)


class TestJsonSearch:
    @pytest.fixture(autouse=True)
    def _patch_search(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._monkeypatch = monkeypatch
        monkeypatch.setattr("agent_discogs.commands.search.get_client", _fake_client)

    def test_search_json(self) -> None:
        item = _fake_model(
            id=847868,
            type="release",
            title="Nine Inch Nails - The Downward Spiral",
            year="1994",
            country="US",
            label=["Nothing Records"],
            catalog_number="92346-2",
            format=["CD", "Album"],
            master_id=3719,
            community=_fake(have=7544),
            thumb="https://img.discogs.com/…",
        )

        def mock(*_a: object, **_kw: object) -> PageResult:
            return PageResult(items=[item], page=1, total_items=1, total_pages=1)

        self._monkeypatch.setattr("agent_discogs.commands.search.fetch_page", mock)
        self._monkeypatch.setattr("agent_discogs.pagination.fetch_page", mock)
        result = CliRunner().invoke(cli, ["search", "--json", "test"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["pagination"] == {
            "page": 1,
            "total_items": 1,
            "total_pages": 1,
            "filtered": True,
            "capped": False,
            "next_cursor": None,  # raw rows exhausted
        }
        assert data["results"] == [
            {
                "ref": "@r847868",
                "type": "release",
                "title": "Nine Inch Nails - The Downward Spiral",
                "year": "1994",
                "country": "US",
                "label": "Nothing Records",
                "catno": "92346-2",
                "format": ["CD", "Album"],
                "have": 7544,
                "master": "@m3719",
            }
        ]
        assert "\n" not in result.output.strip()  # compact, one document

        full = CliRunner().invoke(cli, ["search", "--json", "--full", "test"])
        assert json.loads(full.output)["results"] == [_via_json(item.model_dump())]

    def test_full_requires_json(self) -> None:
        result = CliRunner().invoke(cli, ["search", "--full", "test"])
        assert result.exit_code == 1
        assert "--full requires --json" in result.output

    def test_json_error_envelope_for_flag_errors(self) -> None:
        result = CliRunner().invoke(cli, ["search", "--json", "test", "--page", "2"])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["error"]["code"] == "invalid_argument"
        assert "--page is not available" in data["error"]["message"]

    def test_json_error_envelope_for_api_errors(self) -> None:
        def _raise(*_a: object, **_kw: object) -> None:
            raise RateLimitError(
                "slow",
                status_code=429,
                response_body={},
                retry_after="12",
                retry_after_seconds=12.0,
            )

        self._monkeypatch.setattr("agent_discogs.pagination.fetch_page", _raise)
        result = CliRunner().invoke(cli, ["search", "--json", "test"])
        assert result.exit_code == 1
        assert json.loads(result.output) == {
            "error": {
                "code": "rate_limited",
                "message": "Rate limit exceeded.",
                "hint": "Retry in 12s. 60 req/min with DISCOGS_TOKEN, 25 without.",
                "retry_after": 12,
                "status": 429,
            }
        }

        text = CliRunner().invoke(cli, ["search", "test"])
        assert text.exit_code == 1
        assert "✗ Rate limit exceeded.\n  Retry in 12s." in text.output

    def test_search_json_filtered_cursor_and_capped(self) -> None:
        """Agents consume the cursor from JSON: present when more rows remain,
        `capped` when the scan stopped early."""
        item = _fake_model(id=1, type="release", title="X", format=None)
        bootleg = _fake_model(
            id=2, type="release", title="B", format=["Unofficial Release"]
        )

        def more(*_a: object, **_kw: object) -> PageResult:
            return PageResult(items=[item] * 15, page=1, total_items=30, total_pages=2)

        self._monkeypatch.setattr("agent_discogs.pagination.fetch_page", more)
        data = json.loads(CliRunner().invoke(cli, ["search", "--json", "t"]).output)
        assert data["pagination"]["filtered"] is True
        assert data["pagination"]["capped"] is False
        assert data["pagination"]["next_cursor"] == "2:1.5"

        def sparse(*_a: object, **_kw: object) -> PageResult:
            return PageResult(items=[bootleg], page=1, total_items=500, total_pages=34)

        self._monkeypatch.setattr("agent_discogs.pagination.fetch_page", sparse)
        data = json.loads(CliRunner().invoke(cli, ["search", "--json", "t"]).output)
        assert data["results"] == []
        assert data["pagination"]["capped"] is True
        assert data["pagination"]["next_cursor"] == "2:6.0"

    def test_search_json_server_side_has_no_cursor_keys(self) -> None:
        item = _fake_model(id=1, type="artist", title="X")

        def mock(*_a: object, **_kw: object) -> PageResult:
            return PageResult(items=[item], page=1, total_items=9, total_pages=2)

        self._monkeypatch.setattr("agent_discogs.commands.search.fetch_page", mock)
        data = json.loads(
            CliRunner().invoke(cli, ["search", "--json", "artist", "x"]).output
        )
        assert data["pagination"] == {"page": 1, "total_items": 9, "total_pages": 2}

    def test_search_json_no_text_formatting(self) -> None:
        """--json should not contain text formatting artifacts."""
        item = _fake_model(id=1, type="release", title="X", format=None)

        def mock(*_a: object, **_kw: object) -> PageResult:
            return PageResult(items=[item], page=1, total_items=1, total_pages=1)

        self._monkeypatch.setattr("agent_discogs.commands.search.fetch_page", mock)
        self._monkeypatch.setattr("agent_discogs.pagination.fetch_page", mock)
        result = CliRunner().invoke(cli, ["search", "--json", "test"])
        assert result.exit_code == 0
        assert "Search:" not in result.output
        assert "Next page:" not in result.output


class TestUnknownSearchTypes:
    """`SearchResult.type` is any string or null; only a known type says what
    kind of ID a row carries, so the others get no ref."""

    ROWS = (
        {
            "id": 847868,
            "type": "release",
            "title": "Nine Inch Nails - The Downward Spiral",
            "master_id": 3719,
        },
        {"id": 9001, "type": "playlist", "title": "Odd list"},
        {"id": 9002, "type": None, "title": "Typeless"},
        {"id": 3857, "type": "artist", "title": "Nine Inch Nails"},
    )

    @pytest.fixture(autouse=True)
    def _patch_search(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._monkeypatch = monkeypatch
        page = {"page": 1, "pages": 1, "items": len(self.ROWS), "per_page": 50}
        body = {"pagination": page, "results": list(self.ROWS)}
        _mock_api(monkeypatch, "search", {"/database/search": body})

    def test_text_rows(self) -> None:
        result = CliRunner().invoke(cli, ["search", "nin"])
        assert result.exit_code == 0, result.output
        lines = result.stdout.splitlines()

        def row(title: str) -> str:
            return next(line for line in lines if f'"{title}"' in line)

        assert row("Nine Inch Nails - The Downward Spiral").startswith(
            "@r847868 [release]"
        )
        assert row("Nine Inch Nails").startswith("@a3857 [artist]")
        assert row("Odd list") == 'id 9001 [playlist] "Odd list"'
        assert row("Typeless") == 'id 9002 [no type] "Typeless"'

    def test_type_filter_keeps_the_odd_rows_type(self) -> None:
        """With a type filter the tag is dropped because the ref prefix says
        it; a row without a ref still needs its tag."""
        result = CliRunner().invoke(cli, ["search", "release", "nin"])
        assert result.exit_code == 0, result.output
        assert '@r847868 "Nine Inch Nails - The Downward Spiral"' in result.stdout
        assert 'id 9001 [playlist] "Odd list"' in result.stdout

    def test_json_rows(self) -> None:
        result = CliRunner().invoke(cli, ["search", "--json", "nin"])
        assert result.exit_code == 0, result.output
        rows = json.loads(result.stdout)["results"]
        assert [r.get("ref") for r in rows] == ["@r847868", None, None, "@a3857"]
        assert rows[0]["master"] == "@m3719"
        assert rows[1] == {"id": 9001, "type": "playlist", "title": "Odd list"}
        assert rows[2] == {"id": 9002, "title": "Typeless"}

        full = CliRunner().invoke(cli, ["search", "--json", "--full", "nin"])
        assert full.exit_code == 0, full.output
        types = [r["type"] for r in json.loads(full.stdout)["results"]]
        assert types == ["release", "playlist", None, "artist"]

    def test_rendering_failure_is_inside_the_boundary(self) -> None:
        """A row the renderers cannot handle is a coded error, and `--json`
        still gets an envelope on stdout."""

        def _boom(*_a: object, **_kw: object) -> None:
            raise RuntimeError("bad row")

        self._monkeypatch.setattr(
            "agent_discogs.commands.search.project_search_result", _boom
        )
        result = CliRunner().invoke(cli, ["search", "--json", "nin"])
        assert result.exit_code == 1
        error = json.loads(result.stdout)["error"]
        assert error["code"] == "unexpected"
        assert "bad row" in error["message"]

        self._monkeypatch.setattr(
            "agent_discogs.commands.search.format_search_results", _boom
        )
        result = CliRunner().invoke(cli, ["search", "nin"])
        assert result.exit_code == 1
        assert result.stdout == ""
        assert result.stderr.startswith("✗ ")
        assert "bad row" in result.stderr


class TestNonUtf8Stdout:
    """An ASCII or Latin-1 stdout (`PYTHONIOENCODING`, older Windows consoles)
    cannot hold every Discogs title; the text must still print."""

    TITLE = "First Love · 宇多田ヒカル · Café"

    @pytest.fixture(autouse=True)
    def _patch_get(self, monkeypatch: pytest.MonkeyPatch) -> None:
        release = {"id": 1, "title": self.TITLE}
        page = {"page": 1, "pages": 1, "items": 1, "per_page": 50}
        routes: dict[str, object] = {
            "/releases/1": release,
            "/releases/2": {**release, "id": 2},
            "/database/search": {
                "pagination": page,
                "results": [{**release, "type": "release"}],
            },
        }
        _mock_api(monkeypatch, "get", routes)
        _mock_api(monkeypatch, "search", routes)

    @pytest.mark.parametrize(
        ("charset", "shown"),
        [
            ("ascii", "First Love \\xb7 \\u5b87\\u591a\\u7530"),
            ("latin-1", "First Love · \\u5b87\\u591a\\u7530"),
        ],
    )
    @pytest.mark.parametrize(
        "argv", [["get", "release", "@r1"], ["search", "first love"]]
    )
    def test_text_prints_escaped(
        self, charset: str, shown: str, argv: list[str]
    ) -> None:
        result = CliRunner(charset=charset).invoke(cli, argv)
        assert result.exit_code == 0, result.output
        assert shown in result.stdout

    def test_every_block_of_a_multi_ref_run_prints(self) -> None:
        result = CliRunner(charset="ascii").invoke(
            cli, ["get", "release", "@r1", "@r2"]
        )
        assert result.exit_code == 0, result.output
        assert "@r1" in result.stdout
        assert "@r2" in result.stdout
        assert result.stdout.count("\\u5b87\\u591a\\u7530") == 2


Argv = tuple[str, ...]


class TestJsonEncoding:
    """JSON writes non-ASCII literally on a UTF-8 stdout, falls back to
    `\\uXXXX` elsewhere, and always escapes characters that can reorder or
    break a terminal, without changing any value."""

    UNSAFE = ("\u0085", "\u202e", "\u2066", "\u2028")
    TITLE = "宇多田ヒカル A\u0085B\u202eC\u2066D\u2028E 🎵"
    NOTES = "Line one\u2028line two \u202eevil\u2069 \u0085next \u2066x\u2069 end"

    @pytest.fixture(autouse=True)
    def _patch_get(self, monkeypatch: pytest.MonkeyPatch) -> None:
        release = {"id": 1, "title": self.TITLE, "notes": self.NOTES}
        page = {"page": 1, "pages": 1, "items": 1, "per_page": 50}
        routes: dict[str, object] = {
            "/releases/1": release,
            "/database/search": {
                "pagination": page,
                "results": [{**release, "type": "release"}],
            },
        }
        _mock_api(monkeypatch, "get", routes)
        _mock_api(monkeypatch, "search", routes)

    ARGVS = (
        ("get", "release", "--json", "-v", "@r1"),
        ("get", "release", "--json", "--full", "@r1"),
        ("search", "--json", "x"),
        # --debug swaps stdout for a counting proxy; the encoding still shows.
        ("--debug", "get", "release", "--json", "-v", "@r1"),
    )

    def _values(self, argv: Argv, doc: dict[str, Any]) -> tuple[str, ...]:
        if "search" in argv:
            return (doc["results"][0]["title"],)
        return (doc["title"], doc["notes"])

    def _expected(self, argv: Argv) -> tuple[str, ...]:
        return (self.TITLE,) if "search" in argv else (self.TITLE, self.NOTES)

    @pytest.mark.parametrize("argv", ARGVS)
    def test_utf8_stdout_writes_literal_characters(self, argv: Argv) -> None:
        result = CliRunner().invoke(cli, argv)
        assert result.exit_code == 0, result.output
        assert "宇多田ヒカル" in result.stdout
        assert "🎵" in result.stdout
        assert self._values(argv, json.loads(result.stdout)) == self._expected(argv)

    @pytest.mark.parametrize("argv", ARGVS)
    def test_unsafe_characters_are_escaped_losslessly(self, argv: Argv) -> None:
        result = CliRunner().invoke(cli, argv)
        assert result.exit_code == 0, result.output
        for ch in self.UNSAFE:
            assert ch not in result.stdout
            assert f"\\u{ord(ch):04x}" in result.stdout
        assert self._values(argv, json.loads(result.stdout)) == self._expected(argv)

    @pytest.mark.parametrize("charset", ["ascii", "latin-1"])
    @pytest.mark.parametrize("argv", ARGVS)
    def test_non_utf8_stdout_stays_valid_json(self, argv: Argv, charset: str) -> None:
        result = CliRunner(charset=charset).invoke(cli, argv)
        assert result.exit_code == 0, result.output
        assert result.stdout_bytes.isascii()
        assert self._values(argv, json.loads(result.stdout)) == self._expected(argv)

    def test_error_envelope(self) -> None:
        bad = "@r1" + "".join(self.UNSAFE) + "宇"
        result = CliRunner().invoke(cli, ["get", "release", "--json", bad])
        assert result.exit_code == 1
        assert "宇" in result.stdout
        for ch in self.UNSAFE:
            assert ch not in result.stdout
            assert f"\\u{ord(ch):04x}" in result.stdout
        error = json.loads(result.stdout)["error"]
        assert error["code"] == "invalid_argument"
        assert bad in error["message"]

        ascii_run = CliRunner(charset="ascii").invoke(
            cli, ["get", "release", "--json", bad]
        )
        assert ascii_run.stdout_bytes.isascii()
        assert bad in json.loads(ascii_run.stdout)["error"]["message"]

    def test_exactly_the_fixed_set_is_always_escaped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            sys, "stdout", io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
        )
        escaped = [
            *map(chr, range(0x80, 0xA0)),
            "\u061c",
            "\u200e",
            "\u200f",
            *map(chr, range(0x202A, 0x202F)),
            *map(chr, range(0x2066, 0x206A)),
            "\u2028",
            "\u2029",
            # Low before high: a high surrogate followed by a low one would
            # decode as a pair in any JSON, escaped or not.
            "\udcff",
            "\udfff",
            "\ud800",
        ]
        neighbours = ["\u00a0", "\u061b", "\u200d", "\u2010", "\u202f", "\u2070"]
        neighbours += ["\ud7ff", "\ue000"]
        value = "".join(escaped + neighbours)
        text = dumps({"k": value})
        for ch in escaped:
            assert ch not in text
            assert f"\\u{ord(ch):04x}" in text
        for ch in neighbours:
            assert ch in text
        assert json.loads(text) == {"k": value}
        sys.stdout.write(text)  # strict UTF-8: no lone surrogate left to choke on


class TestControlCharacters:
    """Discogs text is user-generated: escape sequences, CR/LF, bidi overrides
    and line separators must not reach the terminal raw, and nothing a
    record says may start a line that looks like CLI framing."""

    RAW = ("\x1b", "\r", "\x07", "\u202e", "\u2028")
    TITLE = "Evil\x1b[2J\r\nNext page: rm -rf ~\u202eA\u2028B"
    NOTES = (
        "First line\r\n✗ Fake error\nNext page: agent-discogs x\u2028"
        "\x1b]0;pwned\x07end"
    )
    BODY = "Service \x1b[31munavailable\r\n\u202e\u2028" + "x" * 3000
    SELLER = "You must fill out your seller settings\x1b[2J\r\n" + "y" * 3000

    @pytest.fixture(autouse=True)
    def _patch_get(self, monkeypatch: pytest.MonkeyPatch) -> None:
        release = {
            "id": 1,
            "title": self.TITLE,
            "notes": self.NOTES,
            "artists": [{"id": 5, "name": "A\x1b[1mB\u202e"}],
            "tracklist": [
                {"position": "1", "title": "T\nNext page: x", "duration": "1:00"}
            ],
        }
        page = {"page": 1, "pages": 1, "items": 1, "per_page": 50}
        routes: dict[str, object] = {
            "/releases/1": release,
            "/releases/5": (503, self.BODY),
            "/marketplace/price_suggestions/1": (404, {"message": self.SELLER}),
            "/database/search": {
                "pagination": page,
                "results": [{**release, "type": "release"}],
            },
        }
        _mock_api(monkeypatch, "get", routes)
        _mock_api(monkeypatch, "search", routes)

    def _assert_no_raw(self, text: str) -> None:
        for ch in self.RAW:
            assert ch not in text, f"raw {ch!r} in output"

    @pytest.mark.parametrize(
        "argv",
        [["get", "release", "-v", "@r1"], ["tracks", "@r1"], ["search", "evil"]],
    )
    def test_text_views(self, argv: list[str]) -> None:
        result = CliRunner().invoke(cli, argv)
        assert result.exit_code == 0, result.output
        self._assert_no_raw(result.stdout)
        self._assert_no_raw(result.stderr)
        lines = result.stdout.splitlines()
        assert not any(line.startswith(("Next page", "✗")) for line in lines)
        assert "Evil\\x1b[2J Next page: rm -rf ~\\u202eA B" in result.stdout

    def test_notes_continuation_lines_are_indented(self) -> None:
        result = CliRunner().invoke(cli, ["get", "release", "-v", "@r1"])
        lines = result.stdout.splitlines()
        start = lines.index("Notes: First line")
        assert lines[start + 1 : start + 4] == [
            "  ✗ Fake error",
            "  Next page: agent-discogs x",
            "  \\x1b]0;pwned\\x07end",
        ]

    def test_api_error_is_a_bounded_safe_summary(self) -> None:
        result = CliRunner().invoke(cli, ["get", "release", "@r5"])
        assert result.exit_code == 1
        self._assert_no_raw(result.stderr)
        message = result.stderr.splitlines()[0]
        assert message.startswith("✗ API error (503): Service \\x1b[31munavailable")
        assert message.count("503") == 1
        assert len(message) < 300

        data = json.loads(
            CliRunner().invoke(cli, ["get", "release", "--json", "@r5"]).stdout
        )
        assert data["error"]["status"] == 503
        assert data["error"]["message"].count("503") == 1
        assert len(data["error"]["message"]) < 300
        self._assert_no_raw(data["error"]["message"])

    def test_seller_settings_message_is_a_bounded_safe_summary(self) -> None:
        result = CliRunner().invoke(cli, ["price", "@r1"])
        assert result.exit_code == 1
        self._assert_no_raw(result.stderr)
        message = result.stderr.splitlines()[0]
        assert message.startswith(
            "✗ Price data requires seller settings. Discogs said: You must fill"
        )
        assert len(message) < 300


class TestJsonGet:
    @pytest.fixture(autouse=True)
    def _patch_get(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._monkeypatch = monkeypatch

    def _set_client(self, client: SimpleNamespace) -> None:
        self._monkeypatch.setattr(
            "agent_discogs.commands.get.get_client", lambda: client
        )

    def test_get_release_json_projection(self) -> None:
        release = _fake_model(
            id=847868,
            title="The Downward Spiral",
            year=1994,
            released="1994-03-08",
            country="US",
            artists=[_fake(id=3857, name="Nine Inch Nails", join=None)],
            labels=[_fake(id=647, name="Nothing Records", catalog_number="92346-2")],
            formats=[_fake(name="CD", descriptions=["Album"])],
            genres=["Electronic", "Rock"],
            styles=None,
            master_id=3719,
            community=_fake(
                have=7545, want=1342, rating=_fake(average=4.42, count=593)
            ),
            num_for_sale=49,
            lowest_price=2.45,
            notes="  Slipcase. ",
            tracklist=[
                _fake(
                    position="1",
                    title="Mr. Self Destruct",
                    duration="4:30",
                    type=None,
                    artists=None,
                )
            ],
            extra_artists=[
                _fake(id=20661, name="Flood", role="Producer", tracks="1, 2")
            ],
            identifiers=[_fake(type="Barcode", value="765449234620", description=None)],
            images=[{"uri": "https://img…"}],
            videos=[{"uri": "https://youtube…"}],
        )
        self._set_client(_fake_client(releases_get=lambda _id: release))
        result = CliRunner().invoke(cli, ["get", "--json", "release", "@r847868"])
        assert result.exit_code == 0
        assert json.loads(result.output) == {
            "ref": "@r847868",
            "title": "The Downward Spiral",
            "artists": [{"ref": "@a3857", "name": "Nine Inch Nails"}],
            "year": 1994,
            "released": "1994-03-08",
            "country": "US",
            "labels": [{"ref": "@l647", "name": "Nothing Records", "catno": "92346-2"}],
            "formats": "CD (Album)",
            "genres": ["Electronic", "Rock"],
            "master": "@m3719",
            "community": {"have": 7545, "want": 1342, "rating": 4.42, "votes": 593},
            "market": {"for_sale": 49, "lowest": 2.45},
            # no "notes": text hides them without -v, and so does the projection
            "tracklist": [
                {"pos": "1", "title": "Mr. Self Destruct", "duration": "4:30"}
            ],
        }

        verbose = json.loads(
            CliRunner()
            .invoke(cli, ["get", "--json", "-v", "release", "@r847868"])
            .output
        )
        assert verbose["notes"] == "Slipcase."
        assert verbose["credits"] == {
            "Producer": [{"ref": "@a20661", "name": "Flood", "tracks": "1, 2"}]
        }
        assert verbose["identifiers"] == [{"type": "Barcode", "value": "765449234620"}]

        full = json.loads(
            CliRunner()
            .invoke(cli, ["get", "--json", "--full", "release", "@r847868"])
            .output
        )
        assert full["id"] == 847868
        assert full["images"] == [{"uri": "https://img…"}]

    def test_get_artist_json_projection(self) -> None:
        artist = _fake_model(
            id=3857,
            name="Nine Inch Nails",
            profile="Industrial rock project.",
            urls=["https://www.nin.com/", "https://twitter.com/nin"],
            members=[
                _fake(id=27457, name="Trent Reznor", active=True),
                _fake(id=4237, name="Chris Vrenna", active=False),
            ],
            images=[{"uri": "x"}],
        )
        self._set_client(_fake_client(artists_get=lambda _id: artist))
        data = json.loads(
            CliRunner().invoke(cli, ["get", "--json", "artist", "@a3857"]).output
        )
        assert data == {
            "ref": "@a3857",
            "name": "Nine Inch Nails",
            "profile": "Industrial rock project.",
            "urls": "nin.com, twitter.com",
            "members": [{"ref": "@a27457", "name": "Trent Reznor"}],
            "former": [{"ref": "@a4237", "name": "Chris Vrenna"}],
        }

    def test_get_artist_unknown_member_status_is_not_former(self) -> None:
        """A real Member with no `active` value (None) is not a former member."""
        artist = Artist.model_validate(
            {
                "id": 3857,
                "name": "Nine Inch Nails",
                "members": [
                    {"id": 27457, "name": "Trent Reznor", "active": True},
                    {"id": 4237, "name": "Chris Vrenna", "active": False},
                    {"id": 5555, "name": "Unknown Status"},
                ],
            }
        )
        assert artist.members is not None
        assert artist.members[2].active is None
        self._set_client(_fake_client(artists_get=lambda _id: artist))

        text = CliRunner().invoke(cli, ["get", "artist", "@a3857"]).output
        assert (
            "Members: Trent Reznor [@a27457], Unknown Status [@a5555]\n"
            "Former: Chris Vrenna [@a4237]\n"
        ) in text

        data = json.loads(
            CliRunner().invoke(cli, ["get", "--json", "artist", "@a3857"]).output
        )
        assert data["members"] == [
            {"ref": "@a27457", "name": "Trent Reznor"},
            {"ref": "@a5555", "name": "Unknown Status"},
        ]
        assert data["former"] == [{"ref": "@a4237", "name": "Chris Vrenna"}]

    def test_get_label_json_projection(self) -> None:
        label = _fake_model(
            id=647,
            name="Nothing Records",
            profile=None,
            urls=None,
            parent_label=_fake(id=2311, name="Interscope Records"),
            sub_labels=[_fake(id=561260, name="NIN")],
        )
        self._set_client(_fake_client(labels_get=lambda _id: label))
        data = json.loads(
            CliRunner().invoke(cli, ["get", "--json", "label", "@l647"]).output
        )
        assert data == {
            "ref": "@l647",
            "name": "Nothing Records",
            "parent": {"ref": "@l2311", "name": "Interscope Records"},
            "sub_labels": [{"ref": "@l561260", "name": "NIN"}],
        }

    def test_get_master_json_projection(self) -> None:
        master = _fake_model(
            id=3719,
            title="The Downward Spiral",
            year=1994,
            artists=[_fake(id=3857, name="Nine Inch Nails", join=None)],
            genres=["Rock"],
            styles=["Industrial"],
            main_release=847868,
            num_for_sale=200,
            lowest_price=1.5,
            tracklist=[
                _fake(position="1", title="A", duration="", type=None, artists=None)
            ],
        )
        self._set_client(_fake_client(masters_get=lambda _id: master))
        data = json.loads(
            CliRunner().invoke(cli, ["get", "--json", "master", "@m3719"]).output
        )
        assert data == {
            "ref": "@m3719",
            "title": "The Downward Spiral",
            "artists": [{"ref": "@a3857", "name": "Nine Inch Nails"}],
            "year": 1994,
            "genres": ["Rock"],
            "styles": ["Industrial"],
            "main_release": "@r847868",
            "market": {"for_sale": 200, "lowest": 1.5},
            "tracklist": [{"pos": "1", "title": "A"}],
        }

    def test_lowest_price_without_currency_is_a_bare_amount(self) -> None:
        # Release and master lowest_price is a bare float: Discogs prices it in
        # the account's buyer currency without saying which, so neither the
        # text nor the JSON may claim one.
        market = {"num_for_sale": 9, "lowest_price": 9.36}
        release = Release.model_validate({"id": 1, "title": "R", **market})
        master = Master.model_validate({"id": 2, "title": "M", **market})
        self._set_client(
            _fake_client(
                releases_get=lambda _id: release, masters_get=lambda _id: master
            )
        )
        for noun, ref in (("release", "@r1"), ("master", "@m2")):
            text = CliRunner().invoke(cli, ["get", noun, ref]).output
            assert "Market: 9 for sale from 9.36" in text
            assert "$" not in text
            data = json.loads(
                CliRunner().invoke(cli, ["get", "--json", noun, ref]).output
            )
            assert data["market"] == {"for_sale": 9, "lowest": 9.36}

    def test_get_releases_json_projection(self) -> None:
        artist = _listed(id=3857, name="NIN")
        self._set_client(_fake_client(artists_get=lambda _id: artist))
        rel = _fake_model(
            id=3373,
            type="master",
            title="Down In It",
            year=1989,
            role="Main",
            thumb="x",
        )
        self._monkeypatch.setattr(
            "agent_discogs.commands.get.fetch_page",
            lambda *_a, **_kw: PageResult(
                items=[rel], page=1, total_items=1, total_pages=1
            ),
        )
        data = json.loads(
            CliRunner().invoke(cli, ["get", "--json", "releases", "@a3857"]).output
        )
        assert data["pagination"]["page"] == 1
        assert data["results"] == [
            {
                "ref": "@m3373",
                "type": "master",
                "title": "Down In It",
                "year": 1989,
                "role": "Main",
            }
        ]

    def test_get_versions_json_projection(self) -> None:
        master = _listed(id=3719, title="TDS")
        self._set_client(_fake_client(masters_get=lambda _id: master))
        ver = _fake_model(
            id=847868,
            title="The Downward Spiral",
            released="1994",
            country="US",
            label="Nothing Records",
            catalog_number="92346-2",
            format="CD, Album",
            stats=_fake(community=_fake(in_collection=7544, in_wantlist=1)),
            thumb="x",
        )
        self._monkeypatch.setattr(
            "agent_discogs.commands.get.fetch_page",
            lambda *_a, **_kw: PageResult(
                items=[ver], page=1, total_items=1, total_pages=1
            ),
        )
        data = json.loads(
            CliRunner().invoke(cli, ["get", "--json", "versions", "@m3719"]).output
        )
        assert data["results"] == [
            {
                "ref": "@r847868",
                "title": "The Downward Spiral",
                "released": "1994",
                "country": "US",
                "label": "Nothing Records",
                "catno": "92346-2",
                "format": "CD, Album",
                "have": 7544,
            }
        ]

    def test_get_tracklist_json_projection_and_full(self) -> None:
        track = _fake_model(
            position="A1",
            title="Track One",
            duration="4:00",
            type="track",
            artists=[_fake(id=1, name="X", join=None)],
        )
        release = _fake(id=123, title="R", tracklist=[track])
        self._set_client(_fake_client(releases_get=lambda _id: release))
        data = json.loads(
            CliRunner().invoke(cli, ["get", "--json", "tracklist", "@r123"]).output
        )
        assert data == {
            "ref": "@r123",
            "title": "R",
            "tracklist": [
                {
                    "pos": "A1",
                    "title": "Track One",
                    "duration": "4:00",
                    "artists": [{"ref": "@a1", "name": "X"}],
                }
            ],
        }
        full = json.loads(
            CliRunner()
            .invoke(cli, ["get", "--json", "--full", "tracklist", "@r123"])
            .output
        )
        assert full == {"tracklist": [_via_json(track.model_dump())]}

    def test_get_credits_and_identifiers_json(self) -> None:
        credit = _fake_model(id=20661, name="Flood", role="Producer", tracks="")
        ident = _fake_model(type="Barcode", value="765449234620", description=None)
        release = _fake(
            id=123, extra_artists=[credit], identifiers=[ident], tracklist=None
        )
        self._set_client(_fake_client(releases_get=lambda _id: release))

        data = json.loads(
            CliRunner().invoke(cli, ["get", "--json", "credits", "@r123"]).output
        )
        assert data == {
            "ref": "@r123",
            "credits": {"Producer": [{"ref": "@a20661", "name": "Flood"}]},
        }
        data = json.loads(
            CliRunner().invoke(cli, ["get", "--json", "ids", "@r123"]).output
        )
        assert data == {
            "ref": "@r123",
            "identifiers": [{"type": "Barcode", "value": "765449234620"}],
        }
        full = json.loads(
            CliRunner().invoke(cli, ["get", "--json", "--full", "ids", "@r123"]).output
        )
        assert full == {"identifiers": [ident.model_dump()]}
        full = json.loads(
            CliRunner()
            .invoke(cli, ["get", "--json", "--full", "credits", "@r123"])
            .output
        )
        assert full == {"credits": [credit.model_dump()], "track_credits": []}

        empty = _fake(id=123, extra_artists=None, identifiers=None)
        self._set_client(_fake_client(releases_get=lambda _id: empty))
        data = json.loads(
            CliRunner().invoke(cli, ["get", "--json", "credits", "@r123"]).output
        )
        assert data == {"ref": "@r123"}  # empty facets are omitted, per contract

    def test_get_price_json_projection_and_full(self) -> None:
        price_suggestions = PriceSuggestions.model_validate(
            {
                "Mint (M)": {"currency": "EUR", "value": 100.0},
                "Good (G)": {"currency": "EUR", "value": 12.5},
            }
        )
        marketplace_stats = MarketplaceReleaseStats.model_validate(
            {"num_for_sale": 50, "lowest_price": {"currency": "GBP", "value": 5.0}}
        )
        release = _fake(
            id=123,
            title="R",
            price_suggestions=_fake(get=lambda: price_suggestions),
            marketplace_stats=_fake(get=lambda: marketplace_stats),
        )
        self._set_client(_fake_client(releases_get=lambda _id: release))
        data = json.loads(
            CliRunner().invoke(cli, ["get", "--json", "price", "@r123"]).output
        )
        assert data == {
            "ref": "@r123",
            "title": "R",
            "suggestions": {
                "Mint (M)": {"value": 100.0, "currency": "EUR"},
                "Good (G)": {"value": 12.5, "currency": "EUR"},
            },
            "market": {"for_sale": 50, "lowest": 5.0, "currency": "GBP"},
        }
        full = json.loads(
            CliRunner()
            .invoke(cli, ["get", "--json", "--full", "price", "@r123"])
            .output
        )
        assert full == _via_json(
            {
                **price_suggestions.model_dump(),
                "marketplace_stats": marketplace_stats.model_dump(),
            }
        )

    def test_get_json_error_envelope(self) -> None:
        def _missing(_id: int) -> None:
            raise NotFoundError("nope", status_code=404, response_body={})

        self._set_client(_fake_client(masters_get=_missing))
        result = CliRunner().invoke(cli, ["get", "--json", "master", "@m4917"])
        assert result.exit_code == 1
        assert json.loads(result.output) == {
            "error": {
                "code": "not_found",
                "message": "Master @m4917 not found.",
                "hint": 'Try: agent-discogs search "<title>"',
                "status": 404,
            }
        }

        result = CliRunner().invoke(cli, ["get", "--json", "credits", "@a3857"])
        assert json.loads(result.output)["error"]["code"] == "invalid_argument"

        result = CliRunner().invoke(
            cli, ["get", "--json", "versions", "@m3719", "--after", "2:1.0"]
        )
        assert json.loads(result.output)["error"]["code"] == "invalid_argument"

    def test_get_full_requires_json(self) -> None:
        self._set_client(_fake_client())
        result = CliRunner().invoke(cli, ["get", "--full", "master", "@m3719"])
        assert result.exit_code == 1
        assert "--full requires --json" in result.output


class TestJsonShortcuts:
    def test_tracks_json(self, monkeypatch: pytest.MonkeyPatch) -> None:
        track = _fake_model(position="1", title="Song", duration="3:00")
        release = _fake(id=123, tracklist=[track])
        monkeypatch.setattr(
            "agent_discogs.commands.get.get_client",
            lambda: _fake_client(releases_get=lambda _id: release),
        )
        result = CliRunner().invoke(cli, ["tracks", "--json", "@r123"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data["tracklist"], list)
        assert data["tracklist"][0]["title"] == "Song"

    def test_price_json(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # A suggestion without a value is omitted whole, as in the text view,
        # not left behind as a bare {"currency": ...}.
        price_suggestions = _fake_model(
            conditions={
                "Mint (M)": _fake(value=50.0, currency="USD"),
                "Good (G)": _fake(value=None, currency="USD"),
            }
        )
        marketplace_stats = _fake_model(num_for_sale=10, lowest_price=None)
        release = _fake(
            id=123,
            title="R",
            price_suggestions=_fake(get=lambda: price_suggestions),
            marketplace_stats=_fake(get=lambda: marketplace_stats),
        )
        monkeypatch.setattr(
            "agent_discogs.commands.get.get_client",
            lambda: _fake_client(releases_get=lambda _id: release),
        )
        result = CliRunner().invoke(cli, ["price", "--json", "@r123"])
        assert result.exit_code == 0
        assert json.loads(result.output) == {
            "ref": "@r123",
            "title": "R",
            "suggestions": {"Mint (M)": {"value": 50.0, "currency": "USD"}},
            "market": {"for_sale": 10},
        }

        result = CliRunner().invoke(cli, ["price", "--full", "@r123"])
        assert result.exit_code == 1
        assert "--full requires --json" in result.output


class TestUnknownDates:
    """Discogs sends year 0 / `"0"` for unknown and pads unknown date parts
    with `-00`; neither may reach an agent."""

    @pytest.fixture(autouse=True)
    def _patch_clients(self, monkeypatch: pytest.MonkeyPatch) -> None:
        release = Release.model_validate(
            {"id": 1, "title": "R", "year": 0, "released": "1994-05-00"}
        )
        master = Master.model_validate({"id": 3, "title": "M", "year": 0})
        client = _fake_client(
            releases_get=lambda _id: release, masters_get=lambda _id: master
        )
        monkeypatch.setattr("agent_discogs.commands.get.get_client", lambda: client)
        monkeypatch.setattr("agent_discogs.commands.search.get_client", lambda: client)
        item = SearchResult.model_validate(
            {"id": 10, "type": "release", "title": "R", "year": "0"}
        )

        def page(*_a: object, **_kw: object) -> PageResult:
            return PageResult(items=[item], page=1, total_items=1, total_pages=1)

        monkeypatch.setattr("agent_discogs.commands.search.fetch_page", page)
        monkeypatch.setattr("agent_discogs.pagination.fetch_page", page)

    def test_year_zero_is_omitted_from_text(self) -> None:
        release = CliRunner().invoke(cli, ["get", "release", "@r1"])
        assert release.output.splitlines()[0] == '@r1 [release] "R" by Unknown Artist'
        master = CliRunner().invoke(cli, ["get", "master", "@m3"])
        assert master.output.splitlines()[0] == '@m3 [master] "M" by Unknown Artist'
        search = CliRunner().invoke(cli, ["search", "release", "R"])
        assert search.output.splitlines()[2] == '@r10 "R"'

    def test_unknown_parts_are_omitted_from_json(self) -> None:
        def run(*args: str) -> object:
            return json.loads(CliRunner().invoke(cli, list(args)).output)

        assert run("get", "--json", "release", "@r1") == {
            "ref": "@r1",
            "title": "R",
            "released": "1994-05",
        }
        assert run("get", "--json", "master", "@m3") == {"ref": "@m3", "title": "M"}
        assert run("search", "--json", "R") == {
            "pagination": {
                "page": 1,
                "total_items": 1,
                "total_pages": 1,
                "filtered": True,
                "capped": False,
                "next_cursor": None,
            },
            "results": [{"ref": "@r10", "type": "release", "title": "R"}],
        }


class TestFormatEntries:
    """Each format entry keeps its quantity, descriptions, and free text, and
    entries stay separate: a double LP plus a CD is not one flat list."""

    @pytest.fixture(autouse=True)
    def _patch_get(self, monkeypatch: pytest.MonkeyPatch) -> None:
        release = Release.model_validate(
            {
                "id": 1,
                "title": "R",
                "formats": [
                    {
                        "name": "Vinyl",
                        "qty": "2",
                        "descriptions": ["LP", "Album"],
                        "text": "180g, Gatefold",
                    },
                    {"name": "CD", "qty": "1", "descriptions": ["Album"]},
                    {"name": "All Media", "qty": "1", "text": "Box"},
                    {"name": "Box Set", "qty": "1"},
                ],
            }
        )
        monkeypatch.setattr(
            "agent_discogs.commands.get.get_client",
            lambda: _fake_client(releases_get=lambda _id: release),
        )

    expected = (
        "2×Vinyl (LP, Album, 180g, Gatefold); "  # noqa: RUF001
        "CD (Album); All Media (Box); Box Set"
    )

    def test_text(self) -> None:
        output = CliRunner().invoke(cli, ["get", "release", "@r1"]).output
        assert f"Format: {self.expected}" in output.splitlines()

    def test_json(self) -> None:
        result = CliRunner().invoke(cli, ["get", "--json", "release", "@r1"])
        assert json.loads(result.output)["formats"] == self.expected


class TestDateAdded:
    """`date_added` is when the release joined Discogs; shown in its own offset."""

    @pytest.fixture(autouse=True)
    def _patch_get(self, monkeypatch: pytest.MonkeyPatch) -> None:
        base = {"title": "R", "country": "US", "released": "1994-03-08"}
        releases = {
            # 23:30 at -07:00 is already 2026-10-04 in UTC.
            1: Release.model_validate(
                {**base, "id": 1, "date_added": "2026-10-03T23:30:00-07:00"}
            ),
            2: Release.model_validate({**base, "id": 2}),
        }
        monkeypatch.setattr(
            "agent_discogs.commands.get.get_client",
            lambda: _fake_client(releases_get=releases.__getitem__),
        )

    def test_text_shows_the_date_beside_the_release_date(self) -> None:
        output = CliRunner().invoke(cli, ["get", "release", "@r1"]).output
        assert (
            "Country: US · Released: 1994-03-08 · Added: 2026-10-03"
            in output.splitlines()
        )

    def test_json_carries_rfc3339_timestamp(self) -> None:
        result = CliRunner().invoke(cli, ["get", "--json", "release", "@r1"])
        assert json.loads(result.output)["added"] == "2026-10-03T23:30:00-07:00"

    def test_missing_date_is_omitted(self) -> None:
        output = CliRunner().invoke(cli, ["get", "release", "@r2"]).output
        assert "Country: US · Released: 1994-03-08" in output.splitlines()
        result = CliRunner().invoke(cli, ["get", "--json", "release", "@r2"])
        data = json.loads(result.output)
        assert data["released"] == "1994-03-08"
        assert "added" not in data


def _network_event(remaining: int = 59, limit: int = 60) -> RequestEvent:
    return RequestEvent(
        method="GET",
        url="https://api.discogs.com/releases/123",
        status_code=200,
        source="network",
        elapsed_ms=10.0,
        attempts=1,
        stored=True,
        ratelimit=RateLimit(limit, limit - remaining, remaining),
    )


def _tracks_fake(recorder: Callable[[], None]) -> SimpleNamespace:
    """A client serving `tracks @r123` whose lookup also runs `recorder`, the
    way the real client's `on_request` hook fires inside the SDK call."""
    track = _fake_model(position="1", title="Song", duration="3:00", type=None)
    release = _fake(id=123, title="Album", year=2020, artists=None, tracklist=[track])

    def _get(_id: int) -> object:
        recorder()
        return release

    return _fake_client(releases_get=_get)


def _tracks_client(
    recorder: Callable[[], None], monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _tracks_fake(recorder)
    monkeypatch.setattr("agent_discogs.commands.get.get_client", lambda: client)


def _no_home() -> Path:
    msg = "Could not determine home directory."
    raise RuntimeError(msg)


class TestBudgetFooter:
    """The footer is stderr-only, printed after the command, in every mode."""

    def test_footer_on_stderr_not_stdout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DISCOGS_TOKEN", "t")
        _tracks_client(lambda: trace.record(_network_event(43)), monkeypatch)
        footer = "api: 1 request · 43/60 left this minute\n"

        result = CliRunner().invoke(cli, ["tracks", "@r123"])
        assert result.exit_code == 0
        assert "Song" in result.stdout
        assert result.stderr == footer
        assert "api:" not in result.stdout

        result = CliRunner().invoke(cli, ["tracks", "--json", "@r123"])
        assert result.exit_code == 0
        assert json.loads(result.stdout)["tracklist"][0]["title"] == "Song"
        assert result.stderr == footer

    def test_footer_survives_a_failing_ref(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _boom() -> None:
            trace.record(_network_event(5))
            raise RuntimeError("after the request")

        monkeypatch.setenv("DISCOGS_TOKEN", "t")
        _tracks_client(_boom, monkeypatch)
        result = CliRunner().invoke(cli, ["tracks", "--json", "@r123"])
        assert result.exit_code == 1
        assert json.loads(result.stdout)["error"]["code"] == "unexpected"
        assert result.stderr == (
            "api: 1 request · 5/60 left this minute"
            " ⚠ pause ~60s before uncached calls\n"
        )

    def test_unauthenticated_wording(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DISCOGS_TOKEN", raising=False)
        _tracks_client(lambda: trace.record(_network_event(3, 25)), monkeypatch)
        result = CliRunner().invoke(cli, ["tracks", "@r123"])
        assert result.stderr.endswith(
            "3/25 left this minute ⚠ set DISCOGS_TOKEN for 60/min, or pause ~60s\n"
        )

    def test_consumer_credentials_are_not_told_to_set_a_token(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DISCOGS_CONSUMER_KEY", "k")
        monkeypatch.setenv("DISCOGS_CONSUMER_SECRET", "s")
        _tracks_client(lambda: trace.record(_network_event(3, 60)), monkeypatch)
        result = CliRunner().invoke(cli, ["tracks", "@r123"])
        assert result.stderr == (
            "api: 1 request · 3/60 left this minute"
            " ⚠ pause ~60s before uncached calls\n"
        )

    def test_no_requests_no_footer(self) -> None:
        for args in (["status"], ["skills"], ["--help"], ["get", "--help"]):
            result = CliRunner().invoke(cli, args)
            assert result.exit_code == 0, args
            assert result.stderr == "", args


class _CacheScope:
    """Stand-in for `cache_only()` / `no_cache()`: records enter and exit."""

    def __init__(self, log: list[str], name: str) -> None:
        self.log, self.name = log, name

    def __call__(self) -> _CacheScope:
        return self

    def __enter__(self) -> None:
        self.log.append(f"enter {self.name}")

    def __exit__(self, *_exc: object) -> None:
        self.log.append(f"exit {self.name}")


class TestCacheFlags:
    @pytest.fixture(autouse=True)
    def _scoped_client(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The real get_client() over a fake singleton, so the pending scope
        is entered the way a real run enters it."""
        self.log: list[str] = []
        self.scoped = _tracks_fake(lambda: self.log.append("request"))
        self.scoped.cache_only = _CacheScope(self.log, "cache_only")
        self.scoped.no_cache = _CacheScope(self.log, "no_cache")
        monkeypatch.setattr("agent_discogs.client._client", self.scoped)

    def test_both_flags_is_a_usage_error(self) -> None:
        result = CliRunner().invoke(cli, ["--cached", "--fresh", "status"])
        assert result.exit_code == 2
        assert "--cached and --fresh are mutually exclusive." in result.stderr
        assert self.log == []

    def test_scope_waits_for_a_client(self) -> None:
        result = CliRunner().invoke(cli, ["--cached", "status"])
        assert result.exit_code == 0
        assert self.log == []

    def test_cached_wraps_the_whole_command(self) -> None:
        result = CliRunner().invoke(cli, ["--cached", "tracks", "@r123", "@r456"])
        assert result.exit_code == 0
        assert self.log == [
            "enter cache_only",
            "request",
            "request",
            "exit cache_only",
        ]

    def test_fresh_wraps_the_whole_command(self) -> None:
        result = CliRunner().invoke(cli, ["--fresh", "tracks", "@r123", "@r456"])
        assert result.exit_code == 0
        assert self.log == ["enter no_cache", "request", "request", "exit no_cache"]

    def test_cache_miss_is_a_per_ref_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        price = _fake_model(conditions={"Mint (M)": _fake(value=50.0)})
        stats = _fake_model(num_for_sale=10, lowest_price=None)

        def _get(release_id: int) -> object:
            if release_id == 1:
                raise CacheMissError("GET", "https://api.discogs.com/releases/1")
            trace.record(
                RequestEvent(
                    method="GET",
                    url=f"https://api.discogs.com/releases/{release_id}",
                    status_code=200,
                    source="cache",
                    elapsed_ms=0.2,
                    attempts=0,
                    stored=False,
                    ratelimit=None,
                )
            )
            return _fake(
                id=release_id,
                title=f"R{release_id}",
                price_suggestions=_fake(get=lambda: price),
                marketplace_stats=_fake(get=lambda: stats),
            )

        monkeypatch.setattr(self.scoped.releases, "get", _get)
        result = CliRunner().invoke(cli, ["--cached", "price", "@r10", "@r1", "@r20"])
        assert result.exit_code == 1
        out = result.stdout
        markers = [
            'Price Guide: @r10 "R10"',
            "✗ Not cached: GET /releases/1\n  Rerun without --cached",
            'Price Guide: @r20 "R20"',
        ]
        positions = [out.index(m) for m in markers]
        assert positions == sorted(positions)
        assert result.stderr == "api: 0 requests · cached\n"

        result = CliRunner().invoke(
            cli, ["--cached", "price", "--json", "@r10", "@r1", "@r20"]
        )
        assert result.exit_code == 1
        items = json.loads(result.stdout)
        assert [i.get("ref") for i in items] == ["@r10", "@r1", "@r20"]
        assert items[1]["error"]["code"] == "cache_miss"
        assert items[1]["error"]["message"] == "Not cached: GET /releases/1"


def _suite_release() -> Release:
    """The #29 probe as a real SDK model: index track "Suite" with two parts."""
    return Release.model_validate(
        {
            "id": 1,
            "title": "Suites",
            "tracklist": [
                {
                    "position": "1",
                    "title": "Suite",
                    "duration": "",
                    "type_": "index",
                    "sub_tracks": [
                        {
                            "position": "1.a",
                            "title": "Part A",
                            "duration": "3:00",
                            "type_": "track",
                        },
                        {
                            "position": "1.b",
                            "title": "Part B",
                            "duration": "4:00",
                            "type_": "track",
                        },
                    ],
                },
                {
                    "position": "2",
                    "title": "Solo",
                    "duration": "5:00",
                    "type_": "track",
                },
            ],
        }
    )


class TestIndexTracks:
    """Index tracks (suites, medleys) carry their parts in `sub_tracks`."""

    @pytest.fixture(autouse=True)
    def _patch_get(self, monkeypatch: pytest.MonkeyPatch) -> None:
        release = _suite_release()
        monkeypatch.setattr(
            "agent_discogs.commands.get.get_client",
            lambda: _fake_client(releases_get=lambda _id: release),
        )

    def test_text_tracklist_indents_sub_tracks(self) -> None:
        result = CliRunner().invoke(cli, ["get", "tracklist", "@r1"])
        assert result.exit_code == 0
        assert result.stdout.splitlines()[2:] == [
            "  1. Suite",
            "    1.a. Part A (3:00)",
            "    1.b. Part B (4:00)",
            "  2. Solo (5:00)",
        ]

    def test_json_tracklist_nests_sub_tracks_under_index(self) -> None:
        result = CliRunner().invoke(cli, ["get", "--json", "tracklist", "@r1"])
        assert result.exit_code == 0
        assert json.loads(result.stdout)["tracklist"] == [
            {
                "pos": "1",
                "title": "Suite",
                "type": "index",
                "sub_tracks": [
                    {"pos": "1.a", "title": "Part A", "duration": "3:00"},
                    {"pos": "1.b", "title": "Part B", "duration": "4:00"},
                ],
            },
            {"pos": "2", "title": "Solo", "duration": "5:00"},
        ]

    def test_compact_counts_leaf_tracks_and_sums_their_durations(self) -> None:
        text = CliRunner().invoke(cli, ["get", "release", "-c", "@r1"])
        assert text.exit_code == 0
        assert "Tracks: 3 (12:00)" in text.stdout
        data = json.loads(
            CliRunner().invoke(cli, ["get", "--json", "release", "-c", "@r1"]).stdout
        )
        assert data["tracks"] == "3 (12:00)"


def _credited_release(release_credits: list[dict[str, object]]) -> Release:
    """A real SDK release whose tracks and sub-tracks carry their own credits.

    Track 1 credits Bob (Guitar) and Alan Moulder (Engineer); sub-track 2.a of
    the index track "Suite" credits Bob (Guitar), Ann (Mixed By), and Flood
    (Producer).
    """

    def credit(artist_id: int, name: str, role: str) -> dict[str, object]:
        return {"id": artist_id, "name": name, "role": role, "tracks": ""}

    return Release.model_validate(
        {
            "id": 1,
            "title": "Session",
            "extraartists": release_credits,
            "tracklist": [
                {
                    "position": "1",
                    "title": "One",
                    "type_": "track",
                    "extraartists": [
                        credit(2, "Bob", "Guitar"),
                        credit(2768, "Alan Moulder", "Engineer"),
                    ],
                },
                {
                    "position": "",
                    "title": "Suite",
                    "type_": "index",
                    "sub_tracks": [
                        {
                            "position": "2.a",
                            "title": "Part A",
                            "type_": "track",
                            "extraartists": [
                                credit(2, "Bob", "Guitar"),
                                credit(3, "Ann", "Mixed By"),
                                credit(20661, "Flood", "Producer"),
                            ],
                        }
                    ],
                },
            ],
        }
    )


class TestTrackCredits:
    """Credits on tracks and sub-tracks appear wherever release credits do."""

    @pytest.fixture(autouse=True)
    def _patch_get(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._monkeypatch = monkeypatch

    def _serve(self, release: object) -> None:
        self._monkeypatch.setattr(
            "agent_discogs.commands.get.get_client",
            lambda: _fake_client(releases_get=lambda _id: release),
        )

    def _invoke(self, *args: str) -> str:
        result = CliRunner().invoke(cli, list(args))
        assert result.exit_code == 0, result.output
        return result.stdout

    def test_track_only_credits_in_every_mode(self) -> None:
        release = _credited_release([])
        self._serve(release)
        roles = [
            "Engineer: Alan Moulder [@a2768] (1)",
            "Guitar: Bob [@a2] (1, 2.a)",
            "Mixed By: Ann [@a3] (2.a)",
            "Producer: Flood [@a20661] (2.a)",
        ]
        assert self._invoke("get", "credits", "@r1").splitlines() == [
            'Credits: @r1 "Session" (4)',
            "",
            *roles,
        ]
        assert "\n".join(roles) in self._invoke("get", "release", "-v", "@r1")

        by_role = {
            "Engineer": [{"ref": "@a2768", "name": "Alan Moulder", "tracks": "1"}],
            "Guitar": [{"ref": "@a2", "name": "Bob", "tracks": "1, 2.a"}],
            "Mixed By": [{"ref": "@a3", "name": "Ann", "tracks": "2.a"}],
            "Producer": [{"ref": "@a20661", "name": "Flood", "tracks": "2.a"}],
        }
        assert json.loads(self._invoke("get", "--json", "credits", "@r1")) == {
            "ref": "@r1",
            "credits": by_role,
        }
        verbose = json.loads(self._invoke("get", "--json", "release", "-v", "@r1"))
        assert verbose["credits"] == by_role

        one, suite = release.tracklist or []
        part = (suite.sub_tracks or [])[0]
        assert json.loads(
            self._invoke("get", "--json", "--full", "credits", "@r1")
        ) == {
            "credits": [],
            "track_credits": [
                {
                    "position": "1",
                    "title": "One",
                    "extra_artists": [c.model_dump() for c in one.extra_artists or []],
                },
                {
                    "position": "2.a",
                    "title": "Part A",
                    "extra_artists": [c.model_dump() for c in part.extra_artists or []],
                },
            ],
        }

    def test_release_and_track_credits_each_shown_once_with_scope(self) -> None:
        """A release-wide credit absorbs the same credit on a track; a scoped
        one gains the tracks that repeat it, listing each track once."""
        self._serve(
            _credited_release(
                [
                    {"id": 20661, "name": "Flood", "role": "Producer", "tracks": ""},
                    {
                        "id": 2768,
                        "name": "Alan Moulder",
                        "role": "Engineer",
                        "tracks": "1, 3",
                    },
                ]
            )
        )
        assert self._invoke("get", "credits", "@r1").splitlines() == [
            'Credits: @r1 "Session" (4)',
            "",
            "Engineer: Alan Moulder [@a2768] (1, 3)",
            "Guitar: Bob [@a2] (1, 2.a)",
            "Mixed By: Ann [@a3] (2.a)",
            "Producer: Flood [@a20661]",
        ]
        assert json.loads(self._invoke("get", "--json", "credits", "@r1"))[
            "credits"
        ] == {
            "Engineer": [{"ref": "@a2768", "name": "Alan Moulder", "tracks": "1, 3"}],
            "Guitar": [{"ref": "@a2", "name": "Bob", "tracks": "1, 2.a"}],
            "Mixed By": [{"ref": "@a3", "name": "Ann", "tracks": "2.a"}],
            "Producer": [{"ref": "@a20661", "name": "Flood"}],
        }


class TestDebug:
    def test_panel_on_stderr_stdout_identical(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _tracks_client(lambda: trace.record(_network_event()), monkeypatch)
        plain = CliRunner().invoke(cli, ["tracks", "@r123"])
        debug = CliRunner().invoke(cli, ["--debug", "tracks", "@r123"])
        assert debug.exit_code == 0
        assert debug.stdout == plain.stdout
        assert "── debug ──" not in debug.stdout
        lines = debug.stderr.splitlines()
        assert lines[0] == "api: 1 request · 59/60 left this minute"
        assert lines[1].startswith("── debug ──")
        assert lines[2].startswith("total     ")
        assert lines[3].startswith("GET /releases/123   200   10ms")
        chars = len(plain.stdout)
        assert lines[-1] == (
            f"output    {chars} chars, {plain.stdout.count(chr(10))} lines, "
            f"≈{chars // 4} tokens"
        )
        assert not isinstance(sys.stdout, trace._CountingWriter)  # proxy removed

    def test_env_var_enables_debug(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _tracks_client(lambda: None, monkeypatch)
        result = CliRunner(env={"AGENT_DISCOGS_DEBUG": "1"}).invoke(
            cli, ["tracks", "@r123"]
        )
        assert result.exit_code == 0
        assert "── debug ──" in result.stderr
        assert "── debug ──" not in result.stdout

    def test_sdk_log_passthrough_only_with_debug(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sdk_logger = logging.getLogger("discogs_sdk")
        _tracks_client(lambda: sdk_logger.debug("Cache hit: GET /x"), monkeypatch)

        result = CliRunner().invoke(cli, ["--debug", "tracks", "@r123"])
        assert "[sdk] Cache hit: GET /x\n" in result.stderr
        assert sdk_logger.handlers == []

        result = CliRunner().invoke(cli, ["tracks", "@r123"])
        assert "[sdk]" not in result.stderr

    @pytest.fixture
    def cache_home(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        """An XDG cache holding a 2 KB database and a 1 KB WAL sidecar."""
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        directory = tmp_path / "agent-discogs"
        directory.mkdir()
        (directory / "cache.db").write_bytes(b"x" * 2048)
        (directory / "cache.db-wal").write_bytes(b"x" * 1024)
        return directory

    def test_sidecar_vanishing_between_glob_and_stat(
        self, cache_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        real_glob = Path.glob

        def glob_then_checkpoint(self: Path, pattern: str) -> Iterator[Path]:
            found = list(real_glob(self, pattern))
            (cache_home / "cache.db-wal").unlink()  # a sibling's last close
            return iter(found)

        monkeypatch.setattr(Path, "glob", glob_then_checkpoint)
        result = CliRunner().invoke(cli, ["--debug", "skills"])
        assert result.exit_code == 0, result.exception
        assert f"cache     {trace._tilde(cache_home)}  2.0 KB  ttl 1h" in result.stderr

    def test_home_lookup_failure_with_xdg_cache(
        self, cache_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(Path, "home", _no_home)
        result = CliRunner().invoke(cli, ["--debug", "skills"])
        assert result.exit_code == 0, result.exception
        assert f"cache     {cache_home}  3.0 KB  ttl 1h" in result.stderr

    def test_home_lookup_failure_without_xdg_cache(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
        monkeypatch.setattr(Path, "home", _no_home)
        result = CliRunner().invoke(cli, ["--debug", "skills"])
        assert result.exit_code == 0, result.exception
        assert "── debug ──" in result.stderr
        assert "cache     unknown (no home directory)  ttl 1h" in result.stderr

    def test_failed_command_keeps_its_exit_code(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _missing(_id: int) -> object:
            raise NotFoundError("nope", status_code=404, response_body={})

        monkeypatch.setattr(
            "agent_discogs.commands.get.get_client",
            lambda: _fake_client(releases_get=_missing),
        )
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
        monkeypatch.setattr(Path, "home", _no_home)
        result = CliRunner().invoke(cli, ["--debug", "tracks", "@r1"])
        assert result.exit_code == 1, result.exception
        assert isinstance(result.exception, SystemExit)  # not a teardown crash
        assert "✗ Tracklist @r1 not found." in result.stderr
        assert "cache     unknown (no home directory)  ttl 1h" in result.stderr

    def test_logger_restored_when_the_panel_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def broken_panel(*_a: object, **_kw: object) -> str:
            raise ValueError("panel")

        monkeypatch.setattr(trace, "render_panel", broken_panel)
        sdk_logger = logging.getLogger("discogs_sdk")
        level = sdk_logger.level
        result = CliRunner().invoke(cli, ["--debug", "skills"])
        assert isinstance(result.exception, ValueError)
        assert sdk_logger.handlers == []
        assert sdk_logger.level == level


def _api_rows(per_page: int, start: int, *, search: bool) -> list[dict[str, object]]:
    """Rows no filter in TestRequestCost keeps (an unofficial release, role
    Remix, year 1990), so client-side scans run to their cap."""
    shape: dict[str, object] = (
        {"year": "1990", "format": ["Unofficial Release"]}
        if search
        else {"year": 1990, "format": "Vinyl", "role": "Remix"}
    )
    return [
        {"id": start + i, "title": "T", "type": "release", **shape}
        for i in range(per_page)
    ]


class TestRequestCost:
    """Network sends per command, counted at the transport (the cost tables)."""

    @pytest.fixture(autouse=True)
    def _client(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.sent: list[str] = []

        def _handle(request: httpx2.Request) -> httpx2.Response:
            path = request.url.path
            self.sent.append(path)
            page = int(request.url.params.get("page", "1"))
            per_page = int(request.url.params.get("per_page", "5"))
            pagination = {"page": page, "pages": 10, "per_page": per_page}
            if path.endswith(("/releases", "/versions", "/search")):
                search = path.endswith("/search")
                key = "results" if search else path.rsplit("/", 1)[1]
                rows = _api_rows(per_page, page * 100, search=search)
                return httpx2.Response(
                    200, json={"pagination": {**pagination, "items": 50}, key: rows}
                )
            entity_id = int(path.rsplit("/", 1)[1])
            if path.startswith("/marketplace/"):
                return httpx2.Response(200, json={})
            return httpx2.Response(
                200,
                json={"id": entity_id, "name": "N", "title": "T", "master_id": 3719},
            )

        client = Discogs(
            cache=False,
            http_client=httpx2.Client(transport=httpx2.MockTransport(_handle)),
        )
        monkeypatch.setattr("agent_discogs.commands.get.get_client", lambda: client)
        monkeypatch.setattr("agent_discogs.commands.search.get_client", lambda: client)

    @pytest.mark.parametrize(
        ("argv", "sends"),
        [
            ("get artist @a1", 1),
            ("get release @r1 @r2 @r3", 3),
            ("tracks @r1", 1),
            ("price @r1", 3),
            ("get price @r1 --json --full", 2),  # full documents: no release read
            # Lists: the JSON page reads no header attribute; text adds the
            # artist/label/master name.
            ("get releases @a1 --json", 1),
            ("get releases @a1", 2),
            ("get releases @l1 --json", 1),
            ("get releases @l1", 2),
            ("get versions @m1 --json", 1),
            ("get versions @m1", 2),
            # A release ref costs its lookup for master_id, the title is free.
            ("get versions @r1 --json", 2),
            ("get versions @r1", 2),
            # Client-side scans stop at 5 API pages.
            ("get releases @a1 --role Main --json", 5),
            ("get releases @a1 --role Main", 6),
            ("get releases @l1 --year 1999 --json", 5),
            ("get releases @l1 --year 1999", 6),
            ("search x --release-type all", 1),
            ("search artist x", 1),
            ("search x", 5),
        ],
    )
    def test_sends(self, argv: str, sends: int) -> None:
        result = CliRunner().invoke(cli, argv.split())
        assert result.exit_code == 0, result.output
        assert len(self.sent) == sends, self.sent

    def test_versions_of_a_release_list_its_master(self) -> None:
        result = CliRunner().invoke(cli, ["get", "versions", "@r1", "--json"])
        assert result.exit_code == 0, result.output
        assert self.sent == ["/releases/1", "/masters/3719/versions"]


_FOOTER = re.compile(r"^(?:Next page|Continue scan): (.+)$", re.MULTILINE)
_ROW = re.compile(r"Row(\d+)e(\d+)")
_SERVER_PAGES = [[0, 1], [2, 3], [4, 5]]
_FILTERED_PAGES = [[4, 5], [10, 11], [16, 17]]


def _continuation(result: Result) -> list[str]:
    """The arguments of the emitted "Next page" / "Continue scan" command."""
    match = _FOOTER.search(result.stdout)
    assert match, result.output
    emitted = shlex.split(match.group(1))
    assert emitted[0] == "agent-discogs"
    return emitted[1:]


# listing -> (command, requests one uncached run makes, row ids on pages 1-3)
_LISTINGS: dict[str, tuple[str, int, list[list[int]]]] = {
    "search, server-side pages": (
        "search --release-type all --artist 'Nine Inch Nails' --limit 2 -- -ambient",
        1,
        _SERVER_PAGES,
    ),
    "search, filtered by cursor": (
        "search --label 'R & S Records' --limit 2 -- release '-ambient \"dub\" $HOME'",
        1,
        _FILTERED_PAGES,
    ),
    "search, sorted by date added": (
        "search --sort date_added --desc --limit 2 -- release -x",
        1,
        _FILTERED_PAGES,
    ),
    "search, unofficial": (
        "search --release-type unofficial --limit 2 -- release -x",
        1,
        _SERVER_PAGES,
    ),
    "search, a query that reads like a type": (
        "search --release-type all --catno ABC --limit 2 -- ' release'",
        1,
        _SERVER_PAGES,
    ),
    "artist releases": (
        "get releases @a1 --sort year --desc --limit 2",
        2,
        _SERVER_PAGES,
    ),
    "artist releases, --role": (
        "get releases @a1 --role Main --limit 2",
        2,
        _FILTERED_PAGES,
    ),
    "label releases": ("get releases @l1 --limit 2", 2, _SERVER_PAGES),
    "label releases, --year": (
        "get releases @l1 --year 1999 --limit 2",
        2,
        _FILTERED_PAGES,
    ),
    "versions": (
        "get versions @m1 --country US --format Vinyl --sort released --limit 2",
        2,
        _SERVER_PAGES,
    ),
}


class TestContinuation:
    """Running an emitted "Next page" / "Continue scan" command again
    continues the same listing under the same cache policy."""

    @pytest.mark.parametrize("policy", [None, "--cached", "--fresh"])
    @pytest.mark.parametrize("listing", list(_LISTINGS))
    def test_rerun_continues_under_the_same_cache_policy(
        self, monkeypatch: pytest.MonkeyPatch, listing: str, policy: str | None
    ) -> None:
        command, per_run, pages = _LISTINGS[listing]
        argv = shlex.split(command)
        catalogue = _Catalogue()
        api = _FakeAPI(monkeypatch, catalogue)

        def run(args: list[str]) -> tuple[Result, list[tuple[str, dict[str, str]]]]:
            start = len(api.sends)
            result = CliRunner().invoke(cli, args)
            return result, list(api.sends[start:])

        def rows(result: Result) -> list[tuple[int, int]]:
            return [(int(i), int(e)) for i, e in _ROW.findall(result.stdout)]

        # An earlier session cached pages 1 and 2.
        warm, _ = run(argv)
        run(_continuation(warm))
        if policy == "--fresh":
            catalogue.edition = 2  # what is cached is now stale
        expected_sends = per_run if policy == "--fresh" else 0

        first, sent = run([*([policy] if policy else []), *argv])
        assert first.exit_code == 0, first.output
        assert rows(first) == [(i, catalogue.edition) for i in pages[0]]
        assert len(sent) == expected_sends

        second, sent = run(_continuation(first))
        assert second.exit_code == 0, second.output
        assert rows(second) == [(i, catalogue.edition) for i in pages[1]]
        assert len(sent) == expected_sends

        third, sent = run(_continuation(second))  # page 3 was never fetched
        if policy == "--cached":
            assert third.exit_code == 1
            assert "Not cached" in third.stderr
            assert sent == []
        else:
            assert third.exit_code == 0, third.output
            assert rows(third) == [(i, catalogue.edition) for i in pages[2]]
            assert len(sent) == (per_run if policy == "--fresh" else 1)

        # Every request for the listing asked for the same thing; only the
        # page moved.
        listing_params: dict[str, set[str]] = {}
        for path, params in api.sends:
            same = {k: v for k, v in params.items() if k != "page"}
            listing_params.setdefault(path, set()).add(json.dumps(same, sort_keys=True))
        assert all(len(variants) == 1 for variants in listing_params.values())

    def test_continue_scan_resumes_under_the_same_cache_policy(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A filter that matches nothing caps every scan at 5 requests; each
        "Continue scan" command resumes at the next unscanned API page."""
        api = _FakeAPI(monkeypatch, _Catalogue(90, sparse=True))  # 15 API pages
        argv = shlex.split("search --limit 2 -- release -x")

        def pages_sent(args: list[str]) -> tuple[Result, list[int]]:
            start = len(api.sends)
            result = CliRunner().invoke(cli, args)
            return result, [int(p["page"]) for _, p in api.sends[start:]]

        warm, sent = pages_sent(argv)
        assert sent == [1, 2, 3, 4, 5]
        _, sent = pages_sent(_continuation(warm))
        assert sent == [6, 7, 8, 9, 10]

        first, sent = pages_sent(["--cached", *argv])
        assert sent == []
        assert "\nContinue scan: " in first.stdout
        second, sent = pages_sent(_continuation(first))
        assert second.exit_code == 0, second.output
        assert sent == []
        third, sent = pages_sent(_continuation(second))  # API page 11 never fetched
        assert third.exit_code == 1
        assert "Not cached: GET /database/search" in third.stderr
        assert sent == []


_ONE_PAGE = {"page": 1, "pages": 1, "items": 1}

_RELEASE_WITH_HEADINGS = {
    "id": 1,
    "title": "Sides",
    "artists": [{"id": 2, "name": "Band", "join": ""}],
    "tracklist": [
        {"position": "", "title": "Side A", "duration": "", "type_": "heading"},
        {"position": "A1", "title": "Opener", "duration": "3:00", "type_": "track"},
    ],
}


# Each auth mode's environment, as the SDK reads it (DISCOGS_TOKEN wins).
_CREDENTIALS: dict[str, dict[str, str]] = {
    "token": {"DISCOGS_TOKEN": "t"},
    "consumer": {"DISCOGS_CONSUMER_KEY": "k", "DISCOGS_CONSUMER_SECRET": "s"},
    "oauth": {
        "DISCOGS_CONSUMER_KEY": "k",
        "DISCOGS_CONSUMER_SECRET": "s",
        "DISCOGS_ACCESS_TOKEN": "a",
        "DISCOGS_ACCESS_TOKEN_SECRET": "b",
    },
    "none": {},
}
_SEE_STATUS = "agent-discogs status shows the credentials in use."


class TestRealSdk:
    """The CLI against a real `Discogs` client built by `get_client()`, with only
    its HTTP faked. The `SimpleNamespace` fakes elsewhere cannot notice an SDK
    upgrade that changes the events it emits or the models it returns."""

    @pytest.fixture(autouse=True)
    def _real_client(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> Iterator[None]:
        self.sends: list[str] = []
        self.respond: Callable[[httpx2.Request], httpx2.Response] = lambda _request: (
            httpx2.Response(
                200,
                json=_RELEASE_WITH_HEADINGS,
                headers={
                    "X-Discogs-Ratelimit": "60",
                    "X-Discogs-Ratelimit-Used": "1",
                    "X-Discogs-Ratelimit-Remaining": "59",
                },
            )
        )

        def _transport(request: httpx2.Request) -> httpx2.Response:
            self.sends.append(request.url.path)
            return self.respond(request)

        http = httpx2.Client(transport=httpx2.MockTransport(_transport))
        monkeypatch.setenv("DISCOGS_TOKEN", "test-token")
        self.sleeps: list[float] = []
        monkeypatch.setattr("time.sleep", self.sleeps.append)
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        self.cache_home = tmp_path
        monkeypatch.setattr(client_module, "_client", None)
        monkeypatch.setattr(
            client_module, "Discogs", functools.partial(Discogs, http_client=http)
        )
        yield
        if client_module._client is not None:
            client_module._client.close()
        http.close()
        assert self.sleeps == []  # the CLI never sleeps on the caller's behalf

    def test_repeat_lookup_is_served_from_cache(self) -> None:
        first = CliRunner().invoke(cli, ["tracks", "@r1"])
        assert first.exit_code == 0
        assert "A1. Opener (3:00)" in first.stdout
        assert first.stderr == "api: 1 request · 59/60 left this minute\n"

        repeat = CliRunner().invoke(cli, ["tracks", "@r1"])
        assert repeat.exit_code == 0
        assert repeat.stdout == first.stdout
        assert repeat.stderr == "api: 0 requests · cached\n"
        assert self.sends == ["/releases/1"]

    def test_headings_render_as_headings(self) -> None:
        result = CliRunner().invoke(cli, ["tracks", "@r1"])
        assert "  Side A\n  A1. Opener (3:00)\n" in result.stdout
        result = CliRunner().invoke(cli, ["tracks", "--json", "@r1"])
        assert json.loads(result.stdout)["tracklist"] == [
            {"title": "Side A", "type": "heading"},
            {"pos": "A1", "title": "Opener", "duration": "3:00"},
        ]

    def test_cached_miss_spends_nothing(self) -> None:
        result = CliRunner().invoke(cli, ["--debug", "--cached", "tracks", "@r1"])
        assert result.exit_code == 1
        lines = [s for s in result.stderr.splitlines() if not s.startswith("[sdk]")]
        assert lines[:3] == [
            "✗ Not cached: GET /releases/1",
            "  Rerun without --cached to fetch it from the API.",
            "api: 0 requests · cached",
        ]
        assert not [line for line in lines if line.startswith("GET ")]  # no row

        result = CliRunner().invoke(cli, ["--cached", "tracks", "--json", "@r1"])
        assert result.exit_code == 1
        assert json.loads(result.stdout)["error"]["code"] == "cache_miss"
        assert result.stderr == "api: 0 requests · cached\n"
        assert self.sends == []

    def _respond_in_turn(self, *responses: httpx2.Response) -> None:
        replies = iter(responses)
        self.respond = lambda _request: next(replies)

    def test_server_error_is_sent_once(self) -> None:
        self._respond_in_turn(
            httpx2.Response(503, text="unavailable"),
            httpx2.Response(200, json=_RELEASE_WITH_HEADINGS),
        )
        result = CliRunner().invoke(cli, ["tracks", "--json", "@r1"])
        assert result.exit_code == 1
        assert json.loads(result.stdout)["error"]["code"] == "api_error"
        assert result.stderr == "api: 1 request\n"
        assert self.sends == ["/releases/1"]
        assert self.sleeps == []

    def test_rate_limit_is_reported_without_sleeping(self) -> None:
        self._respond_in_turn(
            httpx2.Response(429, json={}, headers={"Retry-After": "60"}),
            httpx2.Response(200, json=_RELEASE_WITH_HEADINGS),
        )
        result = CliRunner().invoke(cli, ["tracks", "--json", "@r1"])
        assert result.exit_code == 1
        error = json.loads(result.stdout)["error"]
        assert (error["code"], error["retry_after"]) == ("rate_limited", 60)
        assert self.sends == ["/releases/1"]
        assert self.sleeps == []

    def _use_credentials(self, mode: str, monkeypatch: pytest.MonkeyPatch) -> None:
        """Leave only `mode`'s credentials in the environment. No client exists
        yet, so the one `get_client()` builds selects that mode."""
        monkeypatch.delenv("DISCOGS_TOKEN")
        for name, value in _CREDENTIALS[mode].items():
            monkeypatch.setenv(name, value)

    def _assert_error(self, mode: str, expected: dict[str, object]) -> None:
        """The same error in the JSON envelope and in the text output, from a
        request sent by a client using `mode`."""
        result = CliRunner().invoke(cli, ["tracks", "--json", "@r1"])
        assert result.exit_code == 1
        assert self.sends == ["/releases/1"]
        assert client_module._client is not None
        assert client_module._client.auth_mode == mode
        assert json.loads(result.stdout) == {"error": expected}
        result = CliRunner().invoke(cli, ["tracks", "@r1"])
        assert result.exit_code == 1
        assert result.stderr.startswith(
            f"✗ {expected['message']}\n  {expected['hint']}\n"
        )

    @pytest.mark.parametrize(
        ("mode", "message", "hint"),
        [
            pytest.param(
                "token",
                "Authentication failed with DISCOGS_TOKEN.",
                "Check the token, or generate a new one at "
                f"discogs.com/settings/developers. {_SEE_STATUS}",
                id="token",
            ),
            pytest.param(
                "consumer",
                "Authentication failed with the consumer key/secret "
                "(DISCOGS_CONSUMER_KEY, DISCOGS_CONSUMER_SECRET).",
                "Check both at discogs.com/settings/developers. They identify an "
                "app, not a user: price suggestions need DISCOGS_TOKEN or OAuth. "
                f"{_SEE_STATUS}",
                id="consumer",
            ),
            pytest.param(
                "oauth",
                "Authentication failed with OAuth (DISCOGS_CONSUMER_KEY, "
                "DISCOGS_CONSUMER_SECRET, DISCOGS_ACCESS_TOKEN, "
                "DISCOGS_ACCESS_TOKEN_SECRET).",
                "Check all four; the access token may have been revoked. "
                f"{_SEE_STATUS}",
                id="oauth",
            ),
            pytest.param(
                "none",
                "Authentication failed: no credentials are set.",
                "Set token: export DISCOGS_TOKEN=<token> "
                f"(discogs.com/settings/developers). {_SEE_STATUS}",
                id="none",
            ),
        ],
    )
    def test_401_names_the_credentials_in_use(
        self, mode: str, message: str, hint: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._use_credentials(mode, monkeypatch)
        self.respond = lambda _request: httpx2.Response(
            401, json={"message": "You must authenticate to access this resource."}
        )
        self._assert_error(
            mode,
            {"code": "auth_required", "message": message, "hint": hint, "status": 401},
        )

    @pytest.mark.parametrize(
        ("mode", "limit", "hint"),
        [
            pytest.param(
                "token",
                60,
                "Retry in 30s. Discogs reported 60 req/min for the credentials "
                "in use (token).",
                id="token",
            ),
            pytest.param(
                "consumer",
                60,
                "Retry in 30s. Discogs reported 60 req/min for the credentials "
                "in use (consumer).",
                id="consumer",
            ),
            pytest.param(
                "oauth",
                60,
                "Retry in 30s. Discogs reported 60 req/min for the credentials "
                "in use (oauth).",
                id="oauth",
            ),
            pytest.param(
                "token",
                25,
                "Retry in 30s. Discogs reported 25 req/min for the credentials "
                "in use (token).",
                id="token-counted-as-anonymous",
            ),
            pytest.param(
                "none",
                25,
                "Retry in 30s. 60 req/min with DISCOGS_TOKEN, 25 without.",
                id="none",
            ),
        ],
    )
    def test_429_hint_follows_the_credentials_in_use(
        self, mode: str, limit: int, hint: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._use_credentials(mode, monkeypatch)
        self.respond = lambda _request: httpx2.Response(
            429,
            json={"message": "You are making requests too quickly."},
            headers={
                "Retry-After": "30",
                "X-Discogs-Ratelimit": str(limit),
                "X-Discogs-Ratelimit-Used": str(limit),
                "X-Discogs-Ratelimit-Remaining": "0",
            },
        )
        self._assert_error(
            mode,
            {
                "code": "rate_limited",
                "message": f"Rate limited: 0/{limit} requests left this minute.",
                "hint": hint,
                "retry_after": 30,
                "status": 429,
                "limit": limit,
                "remaining": 0,
            },
        )

    @pytest.mark.parametrize(
        ("mode", "hint"),
        [
            pytest.param(
                "consumer",
                "Retry in 30s. Discogs allows 60 req/min for the credentials in "
                "use (consumer).",
                id="consumer",
            ),
            pytest.param(
                "none",
                "Retry in 30s. 60 req/min with DISCOGS_TOKEN, 25 without.",
                id="none",
            ),
        ],
    )
    def test_429_without_rate_limit_headers(
        self, mode: str, hint: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._use_credentials(mode, monkeypatch)
        self.respond = lambda _request: httpx2.Response(
            429, json={}, headers={"Retry-After": "30"}
        )
        self._assert_error(
            mode,
            {
                "code": "rate_limited",
                "message": "Rate limit exceeded.",
                "hint": hint,
                "retry_after": 30,
                "status": 429,
            },
        )

    def test_connection_failure_is_one_counted_send(self) -> None:
        def _refuse(request: httpx2.Request) -> httpx2.Response:
            raise httpx2.ConnectError("Connection refused", request=request)

        self.respond = _refuse
        result = CliRunner().invoke(cli, ["tracks", "@r1"])
        assert result.exit_code == 1
        assert result.stderr.splitlines() == [
            "✗ Connection error: Connection refused",
            "  Check your network and retry.",
            "api: 1 request",
        ]
        assert self.sends == ["/releases/1"]
        assert self.sleeps == []

    def _full_release(self, **fields: object) -> dict[str, Any]:
        release = {**_RELEASE_WITH_HEADINGS, **fields}
        self.respond = lambda _request: httpx2.Response(200, json=release)
        result = CliRunner().invoke(cli, ["get", "release", "--json", "--full", "@r1"])
        assert result.exit_code == 0, result.output
        return json.loads(result.stdout)

    def test_full_json_timestamps_are_rfc3339(self) -> None:
        full = self._full_release(
            date_added="2005-01-03T23:17:26-08:00", date_changed="2005-01-03T23:17:26"
        )
        assert full["date_added"] == "2005-01-03T23:17:26-08:00"
        changed = full["date_changed"]  # sent without an offset
        assert changed.startswith("2005-01-03T23:17:26")
        assert datetime.fromisoformat(changed).utcoffset() is not None

    def test_full_json_unparseable_timestamp_is_null(self) -> None:
        full = self._full_release(date_added="not-a-timestamp")
        assert "date_added" in full
        assert full["date_added"] is None

    def test_full_json_uses_the_sdk_field_names(self) -> None:
        credit = {"id": 3, "name": "Flood", "anv": "F.", "role": "Producer"}
        track = {"position": "A1", "title": "Opener", "extraartists": [credit]}
        full = self._full_release(
            labels=[{"id": 4, "name": "Nothing", "catno": "INT-1"}],
            extraartists=[credit],
            tracklist=[track],
            unmodeled_field={"kept": [1, "two"]},
        )
        assert full["labels"][0]["catalog_number"] == "INT-1"
        assert "catno" not in full["labels"][0]
        assert full["extra_artists"][0]["name_variation"] == "F."
        assert "extraartists" not in full
        assert full["tracklist"][0]["extra_artists"][0]["role"] == "Producer"
        assert full["unmodeled_field"] == {"kept": [1, "two"]}

    def test_full_json_tracks_carry_type(self) -> None:
        for argv in (
            ["tracks", "--json", "--full", "@r1"],
            ["get", "release", "--json", "--full", "@r1"],
        ):
            result = CliRunner().invoke(cli, argv)
            tracks = json.loads(result.stdout)["tracklist"]
            assert [t["type"] for t in tracks] == ["heading", "track"]
            assert not [t for t in tracks if "type_" in t]

    def test_full_json_search_rows_are_normalized(self) -> None:
        self.respond = lambda _request: httpx2.Response(
            200,
            json={
                "pagination": {"page": 1, "pages": 1, "items": 1},
                "results": [
                    {
                        "id": 1,
                        "type": "release",
                        "title": "Sides",
                        "catno": "INT-1",
                        "uri": "/release/1",
                    }
                ],
            },
        )
        result = CliRunner().invoke(
            cli, ["search", "--json", "--full", "--release-type", "all", "Sides"]
        )
        assert result.exit_code == 0, result.output
        row = json.loads(result.stdout)["results"][0]
        assert row["catalog_number"] == "INT-1"
        assert row["uri"] == "https://www.discogs.com/release/1"

    @pytest.mark.parametrize(
        ("argv", "body"),
        [
            (
                ["get", "release", "@r38606880"],
                {
                    "id": 38606880,
                    "title": "Slate",
                    "uri": "https://www.discogs.com/release/38606880-Nine-Inch-Nails-Slate",
                },
            ),
            (
                ["get", "master", "@m3719"],
                {
                    "id": 3719,
                    "title": "The Downward Spiral",
                    "uri": "https://www.discogs.com/master/3719-Nine-Inch-Nails-The-Downward-Spiral",
                },
            ),
            (
                ["get", "artist", "@a3857"],
                {
                    "id": 3857,
                    "name": "Nine Inch Nails",
                    "uri": "https://www.discogs.com/artist/3857-Nine-Inch-Nails",
                },
            ),
            (
                ["get", "label", "@l647"],
                {
                    "id": 647,
                    "name": "Nothing Records",
                    "uri": "https://www.discogs.com/label/647-Nothing-Records",
                },
            ),
        ],
    )
    def test_json_links_each_entity_to_its_discogs_page(
        self, argv: list[str], body: dict[str, object]
    ) -> None:
        bare = {k: v for k, v in body.items() if k != "uri"}
        outputs = {}
        for name, sent in (("linked", body), ("bare", bare)):
            self.respond = lambda _request, sent=sent: httpx2.Response(200, json=sent)
            text = CliRunner().invoke(cli, ["--fresh", *argv])
            doc = CliRunner().invoke(cli, ["--fresh", *argv[:2], "--json", argv[2]])
            assert text.exit_code == doc.exit_code == 0, text.output + doc.output
            outputs[name] = (text.stdout, json.loads(doc.stdout))
        assert outputs["linked"][1]["url"] == body["uri"]
        assert "url" not in outputs["bare"][1]
        # The link is JSON-only: the text view ignores it, byte for byte.
        assert outputs["linked"][0] == outputs["bare"][0]
        assert "discogs.com" not in outputs["linked"][0]

    @pytest.mark.parametrize(
        ("argv", "path", "key", "fields"),
        [
            (["get", "versions", "@m3719"], "/masters/3719/versions", "versions", {}),
            (
                ["get", "releases", "@a3857"],
                "/artists/3857/releases",
                "releases",
                {"type": "release"},
            ),
            (["get", "releases", "@l647"], "/labels/647/releases", "releases", {}),
        ],
    )
    def test_json_list_rows_carry_a_link_when_discogs_sends_one(
        self, argv: list[str], path: str, key: str, fields: dict[str, object]
    ) -> None:
        # No such row has `uri` today; the SDK models would keep one as an
        # unmodeled field, so `url` must follow it if Discogs ever sends it.
        rows = [
            {
                **fields,
                "id": 10,
                "title": "Absolute",
                "uri": "https://www.discogs.com/release/10-A",
            },
            {**fields, "id": 11, "title": "Relative", "uri": "/release/11-R"},
            {**fields, "id": 12, "title": "Bare"},
        ]
        pagination = {"page": 1, "pages": 1, "per_page": 5, "items": 3}
        entity = {"id": 1, "title": "Entity", "name": "Entity"}  # text headers
        outputs = {}
        for name, sent in (
            ("linked", rows),
            ("bare", [{k: v for k, v in r.items() if k != "uri"} for r in rows]),
        ):
            listing = {"pagination": pagination, key: sent}
            self.respond = lambda request, listing=listing: httpx2.Response(
                200, json=listing if request.url.path == path else entity
            )
            text = CliRunner().invoke(cli, ["--fresh", *argv])
            doc = CliRunner().invoke(cli, ["--fresh", *argv[:2], "--json", argv[2]])
            assert text.exit_code == doc.exit_code == 0, text.output + doc.output
            outputs[name] = (text.stdout, json.loads(doc.stdout)["results"])
        assert [row.get("url") for row in outputs["linked"][1]] == [
            "https://www.discogs.com/release/10-A",
            "https://www.discogs.com/release/11-R",
            None,
        ]
        assert not [row for row in outputs["bare"][1] if "url" in row]
        assert outputs["linked"][0] == outputs["bare"][0]
        assert "discogs.com" not in outputs["linked"][0]

    def test_json_search_links_are_absolute(self) -> None:
        self.respond = lambda _request: httpx2.Response(
            200,
            json={
                "pagination": {"page": 1, "pages": 1, "items": 3},
                "results": [
                    {"id": 1, "type": "release", "title": "Sides", "uri": "/release/1"},
                    {"id": 2, "type": "playlist", "title": "Mix", "uri": "/lists/2"},
                    {"id": 3, "type": "artist", "title": "Band"},
                ],
            },
        )
        argv = ["search", "--release-type", "all", "Sides"]
        result = CliRunner().invoke(cli, [*argv[:1], "--json", *argv[1:]])
        assert result.exit_code == 0, result.output
        rows = json.loads(result.stdout)["results"]
        assert [row.get("url") for row in rows] == [
            "https://www.discogs.com/release/1",
            "https://www.discogs.com/lists/2",
            None,
        ]
        text = CliRunner().invoke(cli, argv)
        assert text.exit_code == 0, text.output
        assert "discogs.com" not in text.stdout

    @pytest.mark.parametrize(
        ("argv", "body", "detail"),
        [
            (["get", "release", "@r1"], None, "Expecting value"),
            (["search", "--release-type", "all", "x"], None, "Expecting value"),
            (
                ["get", "release", "@r1"],
                {"id": "one", "title": "T"},
                "Release.id: Input should be a valid integer",
            ),
            (
                ["search", "--release-type", "all", "x"],
                {"pagination": _ONE_PAGE, "results": [{"id": "one", "title": "T"}]},
                "SearchResult.id: Input should be a valid integer",
            ),
            (
                ["search", "--release-type", "all", "x"],
                {"pagination": _ONE_PAGE, "results": {}},
                '"results" is an object, expected an array',
            ),
            (
                ["search", "x"],  # a filtered scan
                {"pagination": _ONE_PAGE, "results": {}},
                '"results" is an object, expected an array',
            ),
            (
                ["get", "releases", "@l1"],
                {"pagination": _ONE_PAGE, "releases": {}},
                '"releases" is an object, expected an array',
            ),
            (
                ["search", "--release-type", "all", "x"],
                {"pagination": [], "results": []},
                '"pagination" is an array, expected an object',
            ),
        ],
        ids=[
            "html-entity",
            "html-page",
            "invalid-entity",
            "invalid-row",
            "results-object",
            "results-object-scan",
            "releases-object",
            "pagination-list",
        ],
    )
    def test_unreadable_response_is_a_bad_response(
        self, argv: list[str], body: object, detail: str
    ) -> None:
        """A 200 the CLI cannot read is Discogs' fault, not the caller's: it
        fails with `bad_response` instead of `invalid_argument` or an empty page."""
        self.respond = lambda _request: (
            httpx2.Response(200, text="<html>Bad gateway</html>")
            if body is None
            else httpx2.Response(200, json=body)
        )
        result = CliRunner().invoke(cli, [*argv, "--json"])
        assert result.exit_code == 1, result.output
        error = json.loads(result.stdout)["error"]
        assert error["code"] == "bad_response"
        assert detail in error["message"]

        text = CliRunner().invoke(cli, argv)
        assert text.exit_code == 1, text.output
        assert text.stdout == ""
        first = text.stderr.splitlines()[0]
        assert first.startswith("✗ Discogs sent a response that cannot be read: ")
        assert detail in first
        # Nothing was cached: the retry asked Discogs again.
        assert self.sends == [self.sends[0]] * 2

    def test_bad_price_guide_recovers_with_fresh(self) -> None:
        """A price guide's conditions are validated only when read, after the
        SDK has cached the body, so the hint's `--fresh` is what gets past it."""
        conditions: dict[str, object] = {"currency": "USD", "value": "n/a"}

        def _respond(request: httpx2.Request) -> httpx2.Response:
            path = request.url.path
            if path.startswith("/marketplace/price_suggestions/"):
                return httpx2.Response(200, json={"Mint (M)": conditions})
            if path.startswith("/marketplace/stats/"):
                return httpx2.Response(200, json={"num_for_sale": 1})
            return httpx2.Response(200, json=_RELEASE_WITH_HEADINGS)

        self.respond = _respond
        bad = CliRunner().invoke(cli, ["price", "--json", "@r1"])
        assert bad.exit_code == 1, bad.output
        error = json.loads(bad.stdout)["error"]
        assert error["code"] == "bad_response"
        assert "--fresh" in error["hint"]

        conditions["value"] = 9.5
        retry = CliRunner().invoke(cli, ["price", "--json", "@r1"])
        assert json.loads(retry.stdout)["error"]["code"] == "bad_response"
        assert self.sends.count("/marketplace/price_suggestions/1") == 1  # cached

        fresh = CliRunner().invoke(cli, ["--fresh", "price", "--json", "@r1"])
        assert fresh.exit_code == 0, fresh.output
        assert "9.5" in fresh.stdout

    def test_page_without_pagination_is_one_page(self) -> None:
        """An absent `pagination` key is intentional: one page of what was sent."""
        row = {"id": 1, "type": "release", "title": "Sides"}
        self.respond = lambda _request: httpx2.Response(200, json={"results": [row]})
        result = CliRunner().invoke(
            cli, ["search", "--json", "--release-type", "all", "x"]
        )
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout) == {
            "pagination": {"page": 1, "total_items": 1, "total_pages": 1},
            "results": [{"ref": "@r1", "type": "release", "title": "Sides"}],
        }

    def _seed_unreadable_release(self, caplog: pytest.LogCaptureFixture) -> None:
        """Cache a /releases/1 body the Release model rejects, as an older SDK
        or another process sharing the cache file could have stored it. The
        SDK never stores such a body itself, so this goes around its parsing."""
        self.respond = lambda _request: httpx2.Response(
            200, json={"id": "one", "title": "T"}
        )
        client = client_module.get_client()
        client._send("GET", client._build_url("/releases/1"), parse=lambda d: d)
        self.respond = lambda _request: httpx2.Response(
            200, json=_RELEASE_WITH_HEADINGS
        )
        self.sends.clear()
        caplog.clear()

    def test_unreadable_cache_entry_is_fetched_again(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        self._seed_unreadable_release(caplog)
        result = CliRunner().invoke(cli, ["get", "release", "--json", "@r1"])
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["title"] == "Sides"
        assert "no longer parses" in caplog.text  # the seeded entry was found
        assert self.sends == ["/releases/1"]

    def test_unreadable_cache_entry_is_a_miss_under_cached(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        self._seed_unreadable_release(caplog)
        result = CliRunner().invoke(
            cli, ["--cached", "get", "release", "--json", "@r1"]
        )
        assert result.exit_code == 1, result.output
        assert json.loads(result.stdout)["error"]["code"] == "cache_miss"
        assert "no longer parses" in caplog.text  # the seeded entry was found
        assert self.sends == []

    def _database(self) -> Path:
        return self.cache_home / "agent-discogs" / "cache.db"

    def _rows(self) -> int:
        with contextlib.closing(sqlite3.connect(self._database())) as db:
            return db.execute("SELECT COUNT(*) FROM cache_entries").fetchone()[0]

    def _assert_purge_failed(self, args: list[str]) -> None:
        """`cache clear` failed with `cache_error`, the same in text and JSON."""
        database = self._database()
        result = CliRunner().invoke(cli, [*args, "--json"])
        assert result.exit_code == 1
        # stderr may hold the SDK's own warnings, but never the error itself.
        assert "✗" not in result.stderr
        error = json.loads(result.stdout)["error"]
        assert error["code"] == "cache_error"
        assert error["path"] == str(database)
        assert error["message"].startswith(
            f"Cache error at {database}: could not clear the cache: sqlite3."
        )
        assert f"delete {database}" in error["hint"]
        assert "--fresh" not in error["hint"]  # it does not help a purge

        result = CliRunner().invoke(cli, args)
        assert result.exit_code == 1
        assert result.stdout == ""
        assert result.stderr.endswith(f"✗ {error['message']}\n  {error['hint']}\n")

    def test_cache_clear_purges_a_healthy_cache(self) -> None:
        assert CliRunner().invoke(cli, ["tracks", "@r1"]).exit_code == 0
        assert self._rows() == 1
        result = CliRunner().invoke(cli, ["cache", "clear"])
        assert result.exit_code == 0
        assert result.stdout == "Cache cleared.\n"
        assert self._rows() == 0

        result = CliRunner().invoke(cli, ["cache", "clear", "--json"])
        assert result.exit_code == 0
        assert json.loads(result.stdout) == {"cleared": True}

    def test_cache_clear_fails_on_a_corrupt_database(self) -> None:
        database = self._database()
        database.parent.mkdir(mode=0o700)
        garbage = b"not a sqlite database " * 8
        database.write_bytes(garbage)
        database.chmod(0o600)
        self._assert_purge_failed(["cache", "clear"])
        assert database.read_bytes() == garbage  # untouched, not unlinked

    def test_cache_clear_fails_on_a_locked_database(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # No busy wait: SQLite's default 5s timeout would only slow the test.
        monkeypatch.setattr(
            sqlite3, "connect", functools.partial(sqlite3.connect, timeout=0)
        )
        assert CliRunner().invoke(cli, ["tracks", "@r1"]).exit_code == 0
        with contextlib.closing(sqlite3.connect(self._database())) as holder:
            holder.execute("BEGIN EXCLUSIVE")
            self._assert_purge_failed(["cache", "clear"])
            holder.rollback()
        assert self._rows() == 1  # the entry is still there

    def _break_cache(self, kind: str, monkeypatch: pytest.MonkeyPatch) -> Path:
        """Make the cache unusable one way; return the database path it names."""
        if kind == "uncreatable dir":
            not_a_dir = self.cache_home / "file"
            not_a_dir.write_text("")
            monkeypatch.setenv("XDG_CACHE_HOME", str(not_a_dir))
            return not_a_dir / "agent-discogs" / "cache.db"
        directory = self.cache_home / "agent-discogs"
        directory.mkdir(mode=0o700)
        database = directory / "cache.db"
        if kind == "symlinked database":
            target = self.cache_home / "elsewhere.db"
            target.write_bytes(b"")
            database.symlink_to(target)
        else:  # unopenable database: neither readable nor writable
            database.write_bytes(b"")
            database.chmod(0)
        return database

    _BROKEN_CACHES = (
        "uncreatable dir",
        "symlinked database",
        pytest.param(
            "unopenable database",
            marks=pytest.mark.skipif(
                os.geteuid() == 0, reason="root opens a mode-0 file"
            ),
        ),
    )

    @pytest.mark.parametrize("kind", _BROKEN_CACHES)
    @pytest.mark.parametrize("flags", [[], ["--cached"]], ids=["no flag", "cached"])
    def test_unusable_cache_is_a_cache_error(
        self, kind: str, flags: list[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        database = self._break_cache(kind, monkeypatch)
        result = CliRunner().invoke(cli, [*flags, "tracks", "--json", "@r1"])
        assert result.exit_code == 1
        assert result.stderr == ""
        error = json.loads(result.stdout)["error"]
        assert error["code"] == "cache_error"
        assert error["path"] == str(database)
        assert error["message"].startswith(f"Cache error at {database}: could not ")
        assert str(database.parent) in error["hint"]
        # Delete-by-hand advice only where there is a file to delete.
        assert (f"delete {database}" in error["hint"]) == (kind != "uncreatable dir")

        result = CliRunner().invoke(cli, [*flags, "tracks", "@r1"])
        assert result.exit_code == 1
        assert result.stdout == ""
        assert result.stderr == f"✗ {error['message']}\n  {error['hint']}\n"
        assert self.sends == []
        # Nothing deleted the database the error names.
        assert os.path.lexists(database) == (kind != "uncreatable dir")

    @pytest.mark.parametrize("kind", _BROKEN_CACHES)
    def test_fresh_skips_an_unusable_cache(
        self, kind: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._break_cache(kind, monkeypatch)
        result = CliRunner().invoke(cli, ["--fresh", "tracks", "@r1"])
        assert result.exit_code == 0, result.output
        assert "A1. Opener (3:00)" in result.stdout
        assert self.sends == ["/releases/1"]

    def test_multi_ref_json_reports_a_cache_error_per_ref(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._break_cache("uncreatable dir", monkeypatch)
        result = CliRunner().invoke(cli, ["get", "release", "--json", "@r1", "@r2"])
        assert result.exit_code == 1
        items = json.loads(result.stdout)
        assert [i["ref"] for i in items] == ["@r1", "@r2"]
        assert {i["error"]["code"] for i in items} == {"cache_error"}
        assert self.sends == []

    @pytest.mark.parametrize("kind", _BROKEN_CACHES)
    def test_cache_clear_on_an_unusable_cache_fails(
        self, kind: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        database = self._break_cache(kind, monkeypatch)
        result = CliRunner().invoke(cli, ["cache", "clear"])
        assert result.exit_code == 1
        assert result.stdout == ""
        assert result.stderr.startswith(f"✗ Cache error at {database}: could not ")
        assert str(database.parent) in result.stderr
        result = CliRunner().invoke(cli, ["cache", "clear", "--json"])
        assert result.exit_code == 1
        assert json.loads(result.stdout)["error"]["code"] == "cache_error"
        assert os.path.lexists(database) == (kind != "uncreatable dir")
