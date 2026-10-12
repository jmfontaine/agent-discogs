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
import runpy
import shlex
import sqlite3
import sys
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta, timezone
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
from tests.conftest import (
    RealSdk,
    ServePage,
    UseClient,
    fake,
    fake_client,
    fake_model,
    json_error,
    listed,
    text_error,
)


class _FakeAPI:
    """The real SDK client, with its in-memory cache and real `cache_only()` /
    `no_cache()`, over a mock transport: `sends` lists every request that
    reached the network as `(path, query params)`. `respond(path, params)`
    returns the JSON body, or an `httpx2.Response` to send as is. Like the
    CLI's own client, it never retries."""

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
            max_retries=0,
        )
        monkeypatch.setattr("agent_discogs.client._client", client)

    def _handle(self, request: httpx2.Request) -> httpx2.Response:
        params = dict(request.url.params)
        self.sends.append((request.url.path, params))
        body = self._respond(request.url.path, params)
        if isinstance(body, httpx2.Response):
            return body
        return httpx2.Response(200, json=body)


_LISTING_KEYS = {"search": "results", "releases": "releases", "versions": "versions"}


def _empty_listing(path: str, params: dict[str, str]) -> object:
    """Every listing is an empty page; every entity is `{"id": 1, ...}`."""
    key = _LISTING_KEYS.get(path.rsplit("/", 1)[-1])
    if key is None:
        return {"id": 1, "name": "Entity", "title": "Entity"}
    page = {"page": int(params.get("page", 1)), "pages": 1, "items": 0}
    return {"pagination": page, key: []}


class _Catalogue:
    """`rows` rows (default 300) behind every listing, titled `Row<id>e<edition>`.
    Only the last two rows of each API page pass the client-side filters
    (official format, role Main, year 1999), so a `--limit 2` filtered page
    fills on the last row of each 100-row scan page and every continuation
    needs a new request; with `sparse`, no row passes. Bumping `edition`
    stands in for Discogs changing data after a page was cached."""

    def __init__(self, rows: int = 300, *, sparse: bool = False) -> None:
        self.rows, self.sparse = rows, sparse
        self.edition = 1

    def __call__(self, path: str, params: dict[str, str]) -> object:
        key = _LISTING_KEYS.get(path.rsplit("/", 1)[-1])
        if key is None:
            return {"id": 1, "name": "Entity", "title": "Entity"}
        page, per = int(params["page"]), int(params["per_page"])
        rows = []
        for i in range((page - 1) * per, min(page * per, self.rows)):
            match = not self.sparse and i % per >= per - 2
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
            lambda: fake(clear_cache=lambda: None),
        )
        result = CliRunner().invoke(cli, ["cache", "clear"])
        assert result.exit_code == 0
        assert "Cache cleared" in result.stdout

    def test_cache_help(self) -> None:
        result = CliRunner().invoke(cli, ["cache", "--help"])
        assert result.exit_code == 0
        assert "clear" in result.stdout


class TestCLIBasics:
    def test_version(self) -> None:
        result = CliRunner().invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert "agent-discogs" in result.stdout
        expected = importlib.metadata.version("agent-discogs")
        assert expected in result.stdout

    def test_help(self) -> None:
        result = CliRunner().invoke(cli, ["--help"])
        assert result.exit_code == 0
        # Commands section
        assert "search [type] <query>" in result.stdout
        assert "get <noun> <ref>" in result.stdout
        assert "tracks <ref>" in result.stdout
        assert "price <ref>" in result.stdout
        assert "cache clear" in result.stdout
        assert "status" in result.stdout
        # Aliases
        assert "aliases: find, query" in result.stdout
        assert "aliases: fetch, show" in result.stdout
        # Search types and get nouns
        assert "Search Types:" in result.stdout
        assert "Get Nouns:" in result.stdout
        # Ref system explanation
        assert "@a3857 (artist)" in result.stdout
        assert "@r847868 (release)" in result.stdout
        # Environment
        assert "DISCOGS_TOKEN" in result.stdout
        # Examples
        assert "Examples:" in result.stdout
        assert 'agent-discogs search "The Downward Spiral"' in result.stdout

    def test_no_args_shows_help(self) -> None:
        result = CliRunner().invoke(cli, [])
        assert result.exit_code == 0
        assert "agent-discogs - token-efficient Discogs CLI" in result.stdout
        assert "Commands:" in result.stdout
        assert "Refs:" in result.stdout
        assert "Examples:" in result.stdout

    def test_search_help(self) -> None:
        result = CliRunner().invoke(cli, ["search", "--help"])
        assert result.exit_code == 0
        assert "--year" in result.stdout
        assert "--genre" in result.stdout
        assert "--limit" in result.stdout

    def test_get_help(self) -> None:
        result = CliRunner().invoke(cli, ["get", "--help"])
        assert result.exit_code == 0
        assert "release" in result.stdout
        assert "artist" in result.stdout
        assert "tracklist" in result.stdout
        assert "price" in result.stdout

    def test_unknown_command(self) -> None:
        result = CliRunner().invoke(cli, ["nonexistent"])
        assert result.exit_code == 2
        assert result.stdout == ""
        assert result.stderr.endswith("\n\nError: No such command 'nonexistent'.\n")


class TestStatusCommand:
    def test_status(self) -> None:
        result = CliRunner().invoke(cli, ["status"])
        assert result.exit_code == 0
        version = importlib.metadata.version("agent-discogs")
        assert f"agent-discogs v{version}" in result.stdout
        assert "Auth:" in result.stdout
        assert "Cache:" in result.stdout

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
        assert "--year" in result.stdout
        assert result.stdout.startswith("Usage: cli search ")  # canonical name

    def test_fetch_alias(self) -> None:
        result = CliRunner().invoke(cli, ["fetch", "--help"])
        assert result.exit_code == 0
        assert "release" in result.stdout
        assert result.stdout.startswith("Usage: cli get ")

    def test_query_alias(self) -> None:
        result = CliRunner().invoke(cli, ["query", "--help"])
        assert result.exit_code == 0
        assert "--year" in result.stdout
        assert result.stdout.startswith("Usage: cli search ")

    def test_show_alias(self) -> None:
        result = CliRunner().invoke(cli, ["show", "--help"])
        assert result.exit_code == 0
        assert "release" in result.stdout
        assert result.stdout.startswith("Usage: cli get ")


class TestSearchRequest:
    """Each search argument reaches Discogs as its query parameter."""

    @pytest.fixture(autouse=True)
    def _fake_api(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.api = _FakeAPI(monkeypatch, _empty_listing)

    def _sent(self, *args: str) -> dict[str, str]:
        """The params of the one request `search <args>` sends, server-side."""
        result = CliRunner().invoke(cli, ["search", *args, "--release-type", "all"])
        assert result.exit_code == 0, result.output
        ((path, params),) = self.api.sends
        assert path == "/database/search"
        return params

    @pytest.mark.parametrize(
        ("flag", "param"),
        [
            ("--artist", "artist"),
            ("--barcode", "barcode"),
            ("--catno", "catno"),
            ("--country", "country"),
            ("--format", "format"),
            ("--genre", "genre"),
            ("--label", "label"),
            ("--style", "style"),
            ("--year", "year"),
        ],
    )
    def test_each_filter_is_its_own_parameter(self, flag: str, param: str) -> None:
        assert self._sent("Spiral", flag, "Value") == {
            "q": "Spiral",
            param: "Value",
            "page": "1",
            "per_page": "5",
        }

    @pytest.mark.parametrize("kind", ["release", "master", "artist", "label"])
    def test_type_prefix_is_the_type_parameter(self, kind: str) -> None:
        assert self._sent(kind, "Nine", "Inch", "Nails") == {
            "q": "Nine Inch Nails",
            "type": kind,
            "page": "1",
            "per_page": "5",
        }

    def test_every_filter_at_once_without_a_query(self) -> None:
        sent = self._sent(
            "release",
            *("--artist", "Nine Inch Nails", "--barcode", "765449234620"),
            *("--catno", "INT-92346", "--country", "US", "--format", "CD"),
            *("--genre", "Rock", "--label", "Nothing", "--style", "Industrial"),
            *("--year", "1994", "--limit", "10"),
        )
        assert sent == {
            "type": "release",
            "artist": "Nine Inch Nails",
            "barcode": "765449234620",
            "catno": "INT-92346",
            "country": "US",
            "format": "CD",
            "genre": "Rock",
            "label": "Nothing",
            "style": "Industrial",
            "year": "1994",
            "page": "1",
            "per_page": "10",
        }


class TestSearchCommand:
    @pytest.fixture(autouse=True)
    def _patch_search(
        self,
        monkeypatch: pytest.MonkeyPatch,
        use_client: UseClient,
        serve_page: ServePage,
    ) -> None:
        self._monkeypatch = monkeypatch
        use_client(fake_client())
        self._serve_page = serve_page

    def test_basic_search(self) -> None:
        result_item = SearchResult(
            id=367113,
            type="release",
            title="The Downward Spiral",
            year="1994",
            label=["Nothing Records"],
            format=["Vinyl"],
        )
        self._serve_page(
            PageResult(items=[result_item], page=1, total_items=1, total_pages=1)
        )

        result = CliRunner().invoke(cli, ["search", "downward spiral"])
        assert result.exit_code == 0
        assert result.stdout == (
            'Search: all "downward spiral" (page 1, 1 of ≤1 results)\n'
            "\n"
            '@r367113 [release] "The Downward Spiral" 1994 · Nothing Records · Vinyl\n'
        )

    def test_search_with_type_prefix(self) -> None:
        result_item = SearchResult(id=3857, type="artist", title="Nine Inch Nails")
        self._serve_page(
            PageResult(items=[result_item], page=1, total_items=1, total_pages=1)
        )

        result = CliRunner().invoke(cli, ["search", "artist", "Nine Inch Nails"])
        assert result.exit_code == 0
        assert result.stdout.splitlines() == [
            'Search: artist "Nine Inch Nails" (page 1, 1 of 1 results)',
            "",
            '@a3857 "Nine Inch Nails"',  # typed search: no per-row [type]
        ]

    def test_search_empty_query_after_type_no_filters(self) -> None:
        result = CliRunner().invoke(cli, ["search", "release"])
        assert text_error(result) == "✗ No search query or filters provided.\n"

    def test_search_no_args_no_filters(self) -> None:
        result = CliRunner().invoke(cli, ["search"])
        assert text_error(result) == "✗ No search query or filters provided.\n"

        result = CliRunner().invoke(cli, ["search", "--json"])
        assert json_error(result) == {
            "code": "invalid_argument",
            "message": "No search query or filters provided.",
        }

    def test_search_error(self) -> None:
        def _raise(*_a: object, **_kw: object) -> None:
            raise RuntimeError("connection failed")

        self._monkeypatch.setattr("agent_discogs.pagination.fetch_page", _raise)
        result = CliRunner().invoke(cli, ["search", "test query"])
        assert text_error(result) == (
            "✗ Unexpected error: RuntimeError: connection failed\n"
        )

    def test_search_header_marks_the_filtered_bound(self) -> None:
        """Default (official) search filters client-side; the total is a bound."""
        result_item = SearchResult(id=1, type="release", title="Test")
        self._serve_page(
            PageResult(items=[result_item], page=1, total_items=50, total_pages=10)
        )
        result = CliRunner().invoke(cli, ["search", "test"])
        assert result.exit_code == 0
        assert "of ≤50 results" in result.stdout

    def _forbid_fetch(self) -> None:
        def _boom(*_a: object, **_kw: object) -> None:
            raise AssertionError("API call made before flag validation")

        self._monkeypatch.setattr("agent_discogs.commands.search.fetch_page", _boom)
        self._monkeypatch.setattr("agent_discogs.pagination.fetch_page", _boom)

    def test_search_page_rejected_on_filtered_path(self) -> None:
        self._forbid_fetch()
        result = CliRunner().invoke(cli, ["search", "test", "--page", "2"])
        error = text_error(result)
        assert error.startswith("✗ --page is not available")
        assert "--release-type all" in error

    def test_search_after_rejected_on_server_side_path(self) -> None:
        self._forbid_fetch()
        result = CliRunner().invoke(cli, ["search", "artist", "test", "--after", "2:0"])
        assert text_error(result).startswith("✗ --after only continues")

    def test_search_invalid_cursor_rejected_before_request(self) -> None:
        self._forbid_fetch()
        result = CliRunner().invoke(cli, ["search", "test", "--after", "garbage"])
        assert text_error(result).startswith("✗ Invalid --after cursor")

    def test_search_server_side_pagination(self) -> None:
        """--release-type all pages server-side with --page and no ≤ bound."""
        self._serve_page(PageResult(items=[], page=3, total_items=15, total_pages=3))
        result = CliRunner().invoke(
            cli, ["search", "test", "--release-type", "all", "--page", "3"]
        )
        assert result.exit_code == 0
        assert "(page 3, 0 of 15 results)" in result.stdout
        assert "Next page:" not in result.stdout

    def test_search_sparse_scan_reads_to_the_end(self) -> None:
        """Sparse filter: no cap, so every API page is read and a page with
        no match ends the listing without a footer."""
        bootleg = SearchResult(
            id=1, type="release", title="Boot", format=["Unofficial Release"]
        )
        self._serve_page(
            PageResult(items=[bootleg], page=1, total_items=500, total_pages=34)
        )
        result = CliRunner().invoke(cli, ["search", "test"])
        assert result.exit_code == 0
        assert "(page 1, 0 of ≤500 results)\n" in result.stdout
        assert "Continue scan" not in result.stdout
        assert "Next page" not in result.stdout

    def test_release_type_official_filters_unofficial(self) -> None:
        """Default --release-type official excludes unofficial releases."""
        official = SearchResult(
            id=1, type="release", title="Official", format=["Vinyl"]
        )
        unofficial = SearchResult(
            id=2,
            type="release",
            title="Bootleg",
            format=["Vinyl", "Unofficial Release"],
        )
        self._serve_page(
            PageResult(
                items=[official, unofficial], page=1, total_items=2, total_pages=1
            )
        )
        result = CliRunner().invoke(cli, ["search", "test"])
        assert result.exit_code == 0
        assert "Official" in result.stdout
        assert "Bootleg" not in result.stdout

    def test_release_type_all_shows_everything(self) -> None:
        official = SearchResult(
            id=1, type="release", title="Official", format=["Vinyl"]
        )
        unofficial = SearchResult(
            id=2,
            type="release",
            title="Bootleg",
            format=["Vinyl", "Unofficial Release"],
        )
        self._serve_page(
            PageResult(
                items=[official, unofficial], page=1, total_items=2, total_pages=1
            )
        )
        result = CliRunner().invoke(cli, ["search", "test", "--release-type", "all"])
        assert result.exit_code == 0
        assert "Official" in result.stdout
        assert "Bootleg" in result.stdout

    def test_release_type_header_echo_only_when_it_applies(self) -> None:
        """Header shows release-type when non-default on release/master searches,
        never on artist/label searches where the option is a no-op."""
        item = SearchResult(id=1, type="release", title="Test", format=["Vinyl"])
        self._serve_page(PageResult(items=[item], page=1, total_items=1, total_pages=1))
        result = CliRunner().invoke(cli, ["search", "test", "--release-type", "all"])
        assert 'Search: all "test" release-type=all' in result.stdout

        result = CliRunner().invoke(cli, ["search", "release", "test"])
        assert "release-type" not in result.stdout.split("\n")[0]

        artist = SearchResult(id=1, type="artist", title="Test")
        self._serve_page(
            PageResult(items=[artist], page=1, total_items=1, total_pages=1)
        )
        result = CliRunner().invoke(
            cli, ["search", "artist", "test", "--release-type", "unofficial"]
        )
        assert result.exit_code == 0
        assert "release-type" not in result.stdout.split("\n")[0]

    def test_artist_search_skips_release_filter(self) -> None:
        """Artist type search uses direct fetch, not a filtered scan."""
        pages: list[tuple[int, int]] = []

        def _capture_fetch(_listing: object, *, page: int, per_page: int) -> PageResult:
            pages.append((page, per_page))
            item = SearchResult(id=1, type="artist", title="Test")
            return PageResult(items=[item], page=1, total_items=1, total_pages=1)

        self._monkeypatch.setattr(
            "agent_discogs.commands.search.fetch_page", _capture_fetch
        )
        result = CliRunner().invoke(cli, ["search", "artist", "test"])
        assert result.exit_code == 0
        # Direct path uses per_page=limit (5), not a scan's full 100-row page
        assert pages == [(1, 5)]

    def test_filtered_search_continues_from_cursor(self) -> None:
        """--after resumes at the API page and offset encoded in the cursor."""
        seen: list[tuple[int, int]] = []

        def _mock_fetch(_listing: object, *, page: int, per_page: int) -> PageResult:
            seen.append((page, per_page))
            items = [
                SearchResult(
                    id=page * 10 + i,
                    type="release",
                    title=f"Official {page}-{i}",
                    format=["Vinyl"],
                )
                for i in range(9)
            ]
            return PageResult(items=items, page=page, total_items=30, total_pages=4)

        self._monkeypatch.setattr("agent_discogs.pagination.fetch_page", _mock_fetch)
        result = CliRunner().invoke(
            cli, ["search", "test", "--limit", "3", "--after", "2:3"]
        )
        assert result.exit_code == 0
        assert seen == [(1, 100)]
        assert "(page 2, 3 of ≤30 results)" in result.stdout
        assert "Official 1-3" in result.stdout
        assert "Official 1-5" in result.stdout
        assert "Official 1-2" not in result.stdout

    def test_filtered_search_multi_page(self) -> None:
        """Filtering consumes multiple API pages to fill the limit."""
        call_count = 0

        def _mock_fetch(*_a: object, **_kw: object) -> PageResult:
            nonlocal call_count
            call_count += 1
            # Each API page has 1 official + 1 unofficial
            official = SearchResult(
                id=call_count * 10,
                type="release",
                title=f"Official {call_count}",
                format=["Vinyl"],
            )
            unofficial = SearchResult(
                id=call_count * 10 + 1,
                type="release",
                title=f"Bootleg {call_count}",
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
        assert "Official 1" in result.stdout
        assert "Official 2" in result.stdout
        assert "Official 3" in result.stdout
        assert "Bootleg" not in result.stdout


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
        assert params["per_page"] == "5"  # one server-side page, no scan
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
        assert params["per_page"] == "100"
        assert "of ≤613 results" in result.stdout

    def test_unofficial_with_format_stays_client_side(self) -> None:
        args = ["search", "release", "Downward", "--release-type", "unofficial"]
        result = CliRunner().invoke(cli, [*args, "--format", "Vinyl", "--limit", "99"])
        assert result.exit_code == 0, result.output
        params = self.api.sends[0][1]
        assert params["format"] == "Vinyl"
        assert params["per_page"] == "100"
        assert "of ≤613 results" in result.stdout
        # Rows 98 and 99 are official in the catalogue; the scan skips them.
        ids = [int(i) for i, _ in _ROW.findall(result.stdout)]
        assert ids == [*range(98), 100]

        sent = len(self.api.sends)
        result = CliRunner().invoke(
            cli, [*args, "--format", "Vinyl", "--page", "2", "--json"]
        )
        assert result.exit_code == 1
        assert json.loads(result.stdout)["error"]["code"] == "invalid_argument"
        assert len(self.api.sends) == sent


class TestVersionsRequest:
    """Each `get versions` filter and sort reaches Discogs as its query
    parameter, on the master's versions listing."""

    @pytest.fixture(autouse=True)
    def _fake_api(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.api = _FakeAPI(monkeypatch, _empty_listing)

    def _sent(self, *args: str) -> dict[str, str]:
        """The params of the one versions request `get versions @m4917 <args>`
        sends; the text header's title lookup is the other request."""
        result = CliRunner().invoke(cli, ["get", "versions", "@m4917", *args])
        assert result.exit_code == 0, result.output
        (params,) = [p for path, p in self.api.sends if path.endswith("/versions")]
        assert {path for path, _ in self.api.sends} == {
            "/masters/4917/versions",
            "/masters/4917",
        }
        return params

    @pytest.mark.parametrize(
        ("flag", "param"),
        [
            ("--country", "country"),
            ("--format", "format"),
            ("--label", "label"),
            ("--year", "released"),
        ],
    )
    def test_each_filter_is_its_own_parameter(self, flag: str, param: str) -> None:
        assert self._sent(flag, "Value") == {
            param: "Value",
            "page": "1",
            "per_page": "5",
        }

    @pytest.mark.parametrize(("desc", "order"), [(["--desc"], "desc"), ([], "asc")])
    def test_sort_and_order(self, desc: list[str], order: str) -> None:
        assert self._sent("--sort", "released", *desc) == {
            "sort": "released",
            "sort_order": order,
            "page": "1",
            "per_page": "5",
        }

    def test_get_versions_with_filters(self) -> None:
        args = ["--country", "US", "--format", "Vinyl", "--label", "Nothing"]
        result = CliRunner().invoke(cli, ["get", "versions", "@m4917", *args])
        assert result.exit_code == 0, result.output
        assert result.stdout == (
            'Versions of @m4917 "Entity" (page 1, 0 of 0 versions)\n\n'
        )
        assert self.api.sends == [
            (
                "/masters/4917/versions",
                {
                    "country": "US",
                    "format": "Vinyl",
                    "label": "Nothing",
                    "page": "1",
                    "per_page": "5",
                },
            ),
            ("/masters/4917", {}),
        ]


class TestGetCommand:
    @pytest.fixture(autouse=True)
    def _patch_get(
        self,
        monkeypatch: pytest.MonkeyPatch,
        use_client: UseClient,
        serve_page: ServePage,
    ) -> None:
        self._monkeypatch = monkeypatch
        self._set_client = use_client
        self._serve_page = serve_page

    def test_get_artist(self) -> None:
        artist = fake(
            id=3857,
            name="Nine Inch Nails",
            profile="Industrial",
            urls=None,
            members=None,
        )
        self._set_client(fake_client(artists_get=lambda _id: artist))
        result = CliRunner().invoke(cli, ["get", "artist", "@a3857"])
        assert result.exit_code == 0
        assert "Nine Inch Nails" in result.stdout

    def test_get_label(self) -> None:
        label = fake(
            id=2919,
            name="Nothing Records",
            profile="Label",
            urls=None,
            sub_labels=None,
        )
        self._set_client(fake_client(labels_get=lambda _id: label))
        result = CliRunner().invoke(cli, ["get", "label", "@l2919"])
        assert result.exit_code == 0
        assert "Nothing Records" in result.stdout

    def test_get_master(self) -> None:
        artist = fake(name="NIN", join=None)
        master = fake(
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
        self._set_client(fake_client(masters_get=lambda _id: master))
        result = CliRunner().invoke(cli, ["get", "master", "@m4917"])
        assert result.exit_code == 0
        assert "The Downward Spiral" in result.stdout

    def test_get_release(self) -> None:
        release = fake(
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
        self._set_client(fake_client(releases_get=lambda _id: release))
        result = CliRunner().invoke(cli, ["get", "release", "@r367113"])
        assert result.exit_code == 0
        assert "The Downward Spiral" in result.stdout

    def test_get_price(self) -> None:
        suggestions = PriceSuggestions.model_validate(
            {"Mint (M)": {"currency": "EUR", "value": 100.0}}
        )
        stats = MarketplaceReleaseStats.model_validate(
            {"num_for_sale": 100, "lowest_price": {"currency": "GBP", "value": 5.0}}
        )
        release = fake(
            id=367113,
            title="TDS",
            year=1994,
            artists=None,
            price_suggestions=fake(get=lambda: suggestions),
            marketplace_stats=fake(get=lambda: stats),
        )
        self._set_client(fake_client(releases_get=lambda _id: release))
        result = CliRunner().invoke(cli, ["get", "price", "@r367113"])
        assert result.exit_code == 0
        assert "Price Guide:" in result.stdout
        assert "EUR 100.00" in result.stdout
        assert "Lowest: GBP 5.00" in result.stdout
        assert "$" not in result.stdout

    def test_get_credits(self) -> None:
        release = fake(
            id=847868,
            title="The Downward Spiral",
            extra_artists=[
                fake(id=20661, name="Flood", role="Producer [Production]", tracks="")
            ],
        )
        self._set_client(fake_client(releases_get=lambda _id: release))
        result = CliRunner().invoke(cli, ["get", "credits", "@r847868"])
        assert result.exit_code == 0
        assert 'Credits: @r847868 "The Downward Spiral" (1)' in result.stdout
        assert "Producer [Production]: Flood [@a20661]" in result.stdout

    def test_get_identifiers_and_ids_alias(self) -> None:
        release = fake(
            id=847868,
            title="The Downward Spiral",
            identifiers=[
                fake(type="Barcode", value="765449234620", description="Scanned")
            ],
        )
        self._set_client(fake_client(releases_get=lambda _id: release))
        full = CliRunner().invoke(cli, ["get", "identifiers", "@r847868"])
        alias = CliRunner().invoke(cli, ["get", "ids", "@r847868"])
        assert full.exit_code == 0
        assert "Barcode: 765449234620 (Scanned)" in full.stdout
        assert alias.stdout == full.stdout

    def test_get_credits_rejects_non_release_ref(self) -> None:
        self._set_client(fake_client())
        result = CliRunner().invoke(cli, ["get", "credits", "@a3857"])
        assert text_error(result) == (
            "✗ @a3857 is an artist, not a release. "
            "Use a release ref or ID with 'get credits'.\n"
        )

    def test_get_tracklist(self) -> None:
        track = fake(
            position="A1", title="Mr. Self Destruct", duration="4:09", type=None
        )
        release = fake(
            id=367113,
            title="TDS",
            year=1994,
            artists=None,
            tracklist=[track],
        )
        self._set_client(fake_client(releases_get=lambda _id: release))
        result = CliRunner().invoke(cli, ["get", "tracklist", "@r367113"])
        assert result.exit_code == 0
        assert "A1. Mr. Self Destruct" in result.stdout

    def test_get_releases(self) -> None:
        artist = listed(id=3857, name="Nine Inch Nails")
        self._set_client(fake_client(artists_get=lambda _id: artist))

        rel = fake(id=4917, type="master", title="TDS", year=1994, role="Main")
        self._serve_page(
            PageResult(items=[rel], page=1, total_items=100, total_pages=20)
        )

        result = CliRunner().invoke(cli, ["get", "releases", "@a3857"])
        assert result.exit_code == 0
        assert "Nine Inch Nails" in result.stdout
        assert "Next page:" in result.stdout

    def test_get_versions_master(self) -> None:
        master = listed(id=4917, title="The Downward Spiral")
        self._set_client(fake_client(masters_get=lambda _id: master))

        ver = fake(
            id=367113,
            released="1994",
            country="US",
            label="Nothing",
            catalog_number="INT-92346",
            format="Vinyl",
        )
        self._serve_page(
            PageResult(items=[ver], page=1, total_items=500, total_pages=100)
        )

        result = CliRunner().invoke(cli, ["get", "versions", "@m4917"])
        assert result.exit_code == 0
        assert "Versions of" in result.stdout
        assert "Next page:" in result.stdout

    def test_get_versions_release_smart_resolution(self) -> None:
        """Release ref with master_id resolves to master versions."""
        release = fake(id=367113, master_id=4917, title="The Downward Spiral")
        self._set_client(fake_client(releases_get=lambda _id: release))

        ver = fake(
            id=999,
            released="2020",
            country="UK",
            label="L",
            catalog_number="C",
            format="CD",
        )
        self._serve_page(PageResult(items=[ver], page=1, total_items=1, total_pages=1))

        result = CliRunner().invoke(cli, ["get", "versions", "@r367113"])
        assert result.exit_code == 0
        assert "Versions of" in result.stdout

    def test_get_versions_release_no_master(self) -> None:
        """Release ref with no master_id produces error."""
        release = fake(id=367113, master_id=None, title="Some Single")
        self._set_client(fake_client(releases_get=lambda _id: release))

        result = CliRunner().invoke(cli, ["get", "versions", "@r367113"])
        assert text_error(result).startswith("✗ @r367113 is a release with no master. ")

    def test_get_type_mismatch(self) -> None:
        self._set_client(fake_client())
        result = CliRunner().invoke(cli, ["get", "artist", "@r367113"])
        assert text_error(result) == (
            "✗ @r367113 is a release, not an artist. "
            "Use an artist ref or ID with 'get artist'.\n"
        )

    def test_get_error_handling(self) -> None:
        def _raise(_id: int) -> None:
            raise RuntimeError("API down")

        self._set_client(fake_client(artists_get=_raise))
        result = CliRunner().invoke(cli, ["get", "artist", "@a1"])
        assert text_error(result) == "✗ Unexpected error: RuntimeError: API down\n"

    def test_get_release_verbose(self) -> None:
        artist = fake(id=3857, name="Nine Inch Nails", join=None)
        label = fake(id=33244, name="Rhino Records (2)", catalog_number="R2 75933")
        track = fake(
            position="1",
            title="Hamburger Lady",
            duration="4:12",
            type=None,
            artists=[fake(id=12589, name="Throbbing Gristle", join=None)],
        )
        release = fake(
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
        self._set_client(fake_client(releases_get=lambda _id: release))
        result = CliRunner().invoke(cli, ["get", "release", "@r367113", "--verbose"])
        assert result.exit_code == 0
        assert "Notes: Pressed at Sterling Sound." in result.stdout
        assert "[@a3857]" in result.stdout
        assert "[@l33244]" in result.stdout
        assert "[@a12589]" in result.stdout

    def test_get_release_no_verbose_hides_notes(self) -> None:
        release = fake(
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
        self._set_client(fake_client(releases_get=lambda _id: release))
        result = CliRunner().invoke(cli, ["get", "release", "@r367113"])
        assert result.exit_code == 0
        assert "Notes:" not in result.stdout

    def test_get_release_compact_text_and_json(self) -> None:
        release = fake_model(
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
                fake(position="1", title="A", duration="4:30", type=None, artists=None),
                fake(position="2", title="B", duration="4:24", type=None, artists=None),
            ],
        )
        self._set_client(fake_client(releases_get=lambda _id: release))
        text = CliRunner().invoke(cli, ["get", "release", "@r847868", "-c"])
        assert text.exit_code == 0
        assert "Tracks: 2 (8:54)" in text.stdout
        assert "Tracklist:" not in text.stdout

        data = json.loads(
            CliRunner()
            .invoke(cli, ["get", "release", "@r847868", "-c", "--json"])
            .stdout
        )
        assert data["tracks"] == "2 (8:54)"
        assert "tracklist" not in data

    def test_get_with_raw_id(self) -> None:
        release = fake(
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
        requested: list[int] = []
        self._set_client(
            fake_client(releases_get=lambda id_: requested.append(id_) or release)
        )
        raw = CliRunner().invoke(cli, ["get", "release", "367113"])
        ref = CliRunner().invoke(cli, ["get", "release", "@r367113"])
        assert raw.exit_code == 0
        assert requested == [367113, 367113]
        assert raw.stdout == ref.stdout
        assert raw.stdout.startswith('@r367113 [release] "Test"')

    def test_get_releases_role_filter(self) -> None:
        """--role filters releases by credit role (case-insensitive substring)."""
        artist = listed(id=3857, name="Nine Inch Nails")
        self._set_client(fake_client(artists_get=lambda _id: artist))

        main_rel = fake(id=100, type="master", title="TDS", year=1994, role="Main")
        remix_rel = fake(
            id=200, type="master", title="Remix Album", year=1995, role="Remix"
        )
        compound_rel = fake(
            id=300,
            type="master",
            title="Collab",
            year=1996,
            role="Producer, Written-By",
        )
        self._serve_page(
            PageResult(
                items=[main_rel, remix_rel, compound_rel],
                page=1,
                total_items=3,
                total_pages=1,
            )
        )

        result = CliRunner().invoke(
            cli, ["get", "releases", "@a3857", "--role", "producer"]
        )
        assert result.exit_code == 0
        assert "Collab" in result.stdout
        assert "TDS" not in result.stdout
        assert "Remix Album" not in result.stdout

    def test_get_releases_role_no_match(self) -> None:
        """--role with no matching releases shows empty results."""
        artist = listed(id=3857, name="Nine Inch Nails")
        self._set_client(fake_client(artists_get=lambda _id: artist))

        rel = fake(id=100, type="master", title="TDS", year=1994, role="Main")
        self._serve_page(PageResult(items=[rel], page=1, total_items=1, total_pages=1))

        result = CliRunner().invoke(
            cli, ["get", "releases", "@a3857", "--role", "DJ Mix"]
        )
        assert result.exit_code == 0
        assert "TDS" not in result.stdout

    def test_get_releases_role_header_marks_the_filtered_bound(self) -> None:
        """--role filters client-side, so the total is an upper bound."""
        artist = listed(id=3857, name="Nine Inch Nails")
        self._set_client(fake_client(artists_get=lambda _id: artist))

        rel = fake(id=100, type="master", title="TDS", year=1994, role="Main")
        self._serve_page(
            PageResult(items=[rel], page=1, total_items=50, total_pages=10)
        )

        result = CliRunner().invoke(
            cli, ["get", "releases", "@a3857", "--role", "Main"]
        )
        assert result.exit_code == 0
        assert "of ≤50 results" in result.stdout

    def _set_exploding_client(self) -> None:
        """Any resource access proves validation ran after I/O started."""

        def _boom(_id: int) -> None:
            raise AssertionError("API call made before flag validation")

        self._set_client(
            fake_client(
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
        assert text_error(result).startswith("✗ --page is not available")

    @pytest.mark.parametrize(
        "argv",
        [
            ["get", "releases", "@a3857", "--after", "2:0"],
            ["get", "versions", "@m3719", "--after", "2:0"],
            ["get", "release", "@r847868", "--after", "2:0"],
            ["get", "artist", "@a3857", "--after", "2:0"],
        ],
    )
    def test_get_after_rejected_unless_role_filtered(self, argv: list[str]) -> None:
        self._set_exploding_client()
        result = CliRunner().invoke(cli, argv)
        assert text_error(result).startswith("✗ --after only continues")

    def test_get_invalid_cursor_rejected_before_io(self) -> None:
        self._set_exploding_client()
        result = CliRunner().invoke(
            cli, ["get", "releases", "@a3857", "--role", "Main", "--after", "nope"]
        )
        assert text_error(result).startswith("✗ Invalid --after cursor")

    def _capture_fetch(self) -> None:
        """Fake `fetch_page` to request its page from the listing it is given,
        which a fake listing records, and return an empty page."""

        def _fetch(listing: Any, *, page: int, per_page: int) -> PageResult:
            listing(page=page, per_page=per_page)
            return PageResult(items=[], page=1, total_items=0, total_pages=1)

        self._monkeypatch.setattr("agent_discogs.commands.get.fetch_page", _fetch)
        self._monkeypatch.setattr("agent_discogs.pagination.fetch_page", _fetch)

    def test_get_releases_for_a_label(self) -> None:
        label = listed(id=647, name="Nothing Records")
        self._set_client(fake_client(labels_get=lambda _id: label))
        rel = fake_model(
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
            result.stdout
        )
        assert (
            '@r4401 "Pretty Hate Machine" by Nine Inch Nails · 0694903742 · CD, Album'
            in result.stdout
        )

        data = json.loads(
            CliRunner().invoke(cli, ["get", "--json", "releases", "@l647"]).stdout
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
        artist = listed(id=3857, name="NIN")
        self._set_client(fake_client(artists_get=lambda _id: artist))
        self._capture_fetch()
        result = CliRunner().invoke(
            cli, ["get", "releases", "@a3857", "--sort", "year", "--desc"]
        )
        assert result.exit_code == 0
        assert artist.releases.list.calls == [
            {"sort": "year", "sort_order": "desc", "page": 1, "per_page": 5}
        ]

    def test_get_releases_role_with_sort_passes_sort_to_scan(self) -> None:
        artist = listed(id=3857, name="NIN")
        self._set_client(fake_client(artists_get=lambda _id: artist))
        self._capture_fetch()
        result = CliRunner().invoke(
            cli, ["get", "releases", "@a3857", "--role", "Main", "--sort", "title"]
        )
        assert result.exit_code == 0
        # The client-side scan reads Discogs' largest pages.
        assert artist.releases.list.calls == [
            {"sort": "title", "sort_order": "asc", "page": 1, "per_page": 100}
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
                ["get", "releases", "@l647", "--after", "2:0"],
                "--after only continues a client-side filtered listing",
            ),
        ],
    )
    def test_sort_flag_errors_before_io(self, argv: list[str], message: str) -> None:
        self._set_exploding_client()
        result = CliRunner().invoke(cli, argv)
        assert message in text_error(result)

    @pytest.mark.parametrize(
        ("argv", "message"),
        [
            (
                ["get", "releases", "@a11", "--country", "FR", "--format", "Vinyl"],
                (
                    "--country, --format do not apply to 'get releases'. "
                    "'get releases' takes --after, --desc, --limit, --max-requests, "
                    "--page, --role, --sort, --year."
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
                ["get", "releases", "@a1", "@a2", "--role", "Main", "--after", "2:0"],
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
                    "2:0",
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
        if json_output:
            error = json_error(result)
            assert error["code"] == "invalid_argument"
            assert message in error["message"]
        else:
            assert message in text_error(result)
        assert built == []  # rejected before any client exists: no request

    def test_get_releases_label_year_scans_client_side_with_cursor(self) -> None:
        label = listed(id=647, name="Nothing Records")
        self._set_client(fake_client(labels_get=lambda _id: label))

        def _fetch(listing: Any, *, page: int, per_page: int) -> PageResult:
            listing(page=page, per_page=per_page)
            api_page = page
            rows = [
                fake_model(
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
        assert label.releases.list.calls == [{"page": 1, "per_page": 100}]
        assert 'Releases on @l647 "Nothing Records" (page 1, 2 of ≤60 results)' in (
            result.stdout
        )
        assert '@r100 "T1-0" by A 1999 · CD' in result.stdout
        assert '@r103 "T1-3" by A 1999 · CD' in result.stdout
        assert "2001" not in result.stdout

        data = json.loads(
            CliRunner()
            .invoke(cli, ["get", "--json", "releases", "@l647", "--year", "1999"])
            .stdout
        )
        assert data["pagination"]["filtered"] is True
        assert all(r["year"] == 1999 for r in data["results"])


class TestShortcutCommands:
    def test_tracks_shortcut(self, use_client: UseClient) -> None:
        track = fake(position="1", title="Track One", duration="3:00", type=None)
        release = fake(
            id=123,
            title="Album",
            year=2020,
            artists=None,
            tracklist=[track],
        )
        use_client(fake_client(releases_get=lambda _id: release))
        result = CliRunner().invoke(cli, ["tracks", "@r123"])
        assert result.exit_code == 0
        assert "Track One" in result.stdout

    def test_price_shortcut(self, use_client: UseClient) -> None:
        price = fake(value=50.00, currency="USD")
        release = fake(
            id=123,
            title="Album",
            year=2020,
            artists=None,
            price_suggestions=fake(get=lambda: fake(conditions={"Mint (M)": price})),
            marketplace_stats=fake(
                get=lambda: fake(num_for_sale=10, lowest_price=None)
            ),
        )
        use_client(fake_client(releases_get=lambda _id: release))
        result = CliRunner().invoke(cli, ["price", "@r123"])
        assert result.exit_code == 0
        assert "Price Guide:" in result.stdout


class TestMainEntry:
    def test_main_invokes_cli(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """main() delegates to cli()."""
        calls: list[object] = []
        monkeypatch.setattr("agent_discogs.cli", lambda *a, **kw: calls.append(1))
        main()
        assert len(calls) == 1

    def test_dunder_main(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`python -m agent_discogs` runs main()."""
        calls: list[object] = []
        monkeypatch.setattr("agent_discogs.main", lambda: calls.append(1))
        runpy.run_module("agent_discogs", run_name="__main__")
        assert calls == [1]


class TestMultiRef:
    """`get`, `tracks`, and `price` accept several refs: one command, N blocks."""

    @pytest.fixture(autouse=True)
    def _patch_get(self, use_client: UseClient) -> None:
        releases = {
            847868: fake_model(
                id=847868,
                title="The Downward Spiral",
                year=1994,
                artists=None,
                community=None,
                labels=[fake(id=647, name="Nothing Records", catalog_number="92346-2")],
                formats=None,
                genres=None,
                styles=None,
                num_for_sale=None,
                lowest_price=None,
                master_id=None,
                tracklist=[fake(position="1", title="A", duration="1:00", type=None)],
            ),
            12453760: fake_model(
                id=12453760,
                title="The Downward Spiral",
                year=1994,
                artists=None,
                community=None,
                labels=[
                    fake(id=647, name="Nothing Records", catalog_number="7 92346-2")
                ],
                formats=None,
                genres=None,
                styles=None,
                num_for_sale=None,
                lowest_price=None,
                master_id=None,
                tracklist=[fake(position="1", title="A", duration="1:00", type=None)],
            ),
        }
        self.calls: list[int] = []

        def _get(release_id: int) -> object:
            self.calls.append(release_id)
            if release_id not in releases:
                raise NotFoundError("nope", status_code=404, response_body={})
            return releases[release_id]

        use_client(fake_client(releases_get=_get))

    def test_text_blocks_in_order_separated_by_blank_line(self) -> None:
        result = CliRunner().invoke(
            cli, ["get", "release", "@r847868", "@r12453760", "-c"]
        )
        assert result.exit_code == 0
        assert self.calls == [847868, 12453760]
        first, second = result.stdout.strip().split("\n\n")
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
        data = json.loads(result.stdout)
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
        assert isinstance(json.loads(result.stdout), dict)

        result = CliRunner().invoke(cli, ["get", "--json", "release", "@r1"])
        assert result.exit_code == 1
        assert set(json.loads(result.stdout)) == {"error"}

    def test_shortcuts_take_several_refs(self, use_client: UseClient) -> None:
        result = CliRunner().invoke(cli, ["tracks", "@r847868", "@r12453760"])
        assert result.exit_code == 0
        assert result.stdout.count("Tracklist: ") == 2

        def _release(release_id: int) -> object:
            if release_id == 1:
                raise NotFoundError("nope", status_code=404, response_body={})
            return fake(
                id=release_id,
                title=f"R{release_id}",
                artists=None,
                year=None,
                price_suggestions=fake(
                    get=lambda: fake(
                        conditions={
                            "Mint (M)": fake(value=float(release_id), currency="USD")
                        }
                    )
                ),
                marketplace_stats=fake(
                    get=lambda: fake(num_for_sale=1, lowest_price=None)
                ),
            )

        use_client(fake_client(releases_get=_release))
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
            CliRunner().invoke(cli, ["price", "--json", "@r10", "@r1"]).stdout
        )
        assert data[0]["suggestions"] == {
            "Mint (M)": {"value": 10.0, "currency": "USD"}
        }
        assert data[1]["error"]["code"] == "not_found"

    def test_ref_cap(self) -> None:
        refs = [f"@r{n}" for n in range(11)]
        result = CliRunner().invoke(cli, ["get", "release", *refs])
        assert text_error(result) == "✗ At most 10 refs per command (11 given).\n"
        assert self.calls == []

    def test_no_ref_is_a_usage_error(self) -> None:
        result = CliRunner().invoke(cli, ["get", "release"])
        assert result.exit_code == 2
        assert result.stdout == ""
        assert result.stderr.endswith("\n\nError: Missing argument 'REF...'.\n")


class TestExceptionHandling:
    def test_abort_propagates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _raise_abort(
            *_a: object,
            **_kw: object,
        ) -> None:
            raise click.exceptions.Abort

        monkeypatch.setattr("agent_discogs.commands.get._dispatch", _raise_abort)
        result = CliRunner().invoke(cli, ["get", "artist", "@a1"])
        # click's own abort, not reported as an unexpected error
        assert text_error(result) == "Aborted!\n"

    def test_keyboard_interrupt(self, use_client: UseClient) -> None:
        def _raise(_id: int) -> None:
            raise KeyboardInterrupt

        use_client(fake_client(artists_get=_raise))
        result = CliRunner().invoke(cli, ["get", "artist", "@a1"])
        assert result.exit_code == 130
        assert (result.stdout, result.stderr) == ("", "")  # no traceback

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
        error = json_error(result)
        assert error == {
            "code": "unexpected",
            "message": "Unexpected error: RuntimeError: client setup failed",
        }

        monkeypatch.setattr("agent_discogs.commands.search.get_client", _raise)
        result = CliRunner().invoke(cli, ["search", "--json", "x"])
        assert json_error(result) == error


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
    def _patch_search(
        self,
        monkeypatch: pytest.MonkeyPatch,
        use_client: UseClient,
        serve_page: ServePage,
    ) -> None:
        self._monkeypatch = monkeypatch
        use_client(fake_client())
        self._serve_page = serve_page

    def test_search_json(self) -> None:
        item = SearchResult.model_validate(
            {
                "id": 847868,
                "type": "release",
                "title": "Nine Inch Nails - The Downward Spiral",
                "year": "1994",
                "country": "US",
                "label": ["Nothing Records"],
                "catno": "92346-2",
                "format": ["CD", "Album"],
                "master_id": 3719,
                "community": {"have": 7544, "want": 900},
                "thumb": "https://img.discogs.com/…",
            }
        )
        self._serve_page(PageResult(items=[item], page=1, total_items=1, total_pages=1))
        result = CliRunner().invoke(cli, ["search", "--json", "test"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
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
        assert result.stdout.count("\n") == 1  # compact, one document

        full = CliRunner().invoke(cli, ["search", "--json", "--full", "test"])
        assert json.loads(full.stdout)["results"] == [item.model_dump(mode="json")]

    def test_full_requires_json(self) -> None:
        result = CliRunner().invoke(cli, ["search", "--full", "test"])
        assert text_error(result) == "✗ --full requires --json.\n"

    def test_json_error_envelope_for_flag_errors(self) -> None:
        result = CliRunner().invoke(cli, ["search", "--json", "test", "--page", "2"])
        error = json_error(result)
        assert error["code"] == "invalid_argument"
        assert error["message"].startswith("--page is not available")

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
        assert json_error(result) == {
            "code": "rate_limited",
            "message": "Rate limit exceeded.",
            "hint": "Retry in 12s. 60 req/min with DISCOGS_TOKEN, 25 without.",
            "retry_after": 12,
            "status": 429,
        }

        text = CliRunner().invoke(cli, ["search", "test"])
        assert text_error(text) == (
            "✗ Rate limit exceeded.\n"
            "  Retry in 12s. 60 req/min with DISCOGS_TOKEN, 25 without.\n"
        )

    def test_search_json_filtered_cursor(self) -> None:
        """Agents consume the cursor from JSON: present when more rows remain,
        absent once a scan has read every page."""
        item = SearchResult(id=1, type="release", title="X")
        bootleg = SearchResult(
            id=2, type="release", title="B", format=["Unofficial Release"]
        )

        self._serve_page(
            PageResult(items=[item] * 15, page=1, total_items=30, total_pages=2)
        )
        result = CliRunner().invoke(cli, ["search", "--json", "t"])
        data = json.loads(result.stdout)
        assert data["pagination"]["filtered"] is True
        assert data["pagination"]["capped"] is False
        assert data["pagination"]["next_cursor"] == "2:5"

        self._serve_page(
            PageResult(items=[bootleg], page=1, total_items=500, total_pages=34)
        )
        result = CliRunner().invoke(cli, ["search", "--json", "t"])
        data = json.loads(result.stdout)
        assert data["results"] == []
        assert data["pagination"]["capped"] is False
        assert data["pagination"]["next_cursor"] is None

    def test_search_json_server_side_has_no_cursor_keys(self) -> None:
        item = SearchResult(id=1, type="artist", title="X")
        self._serve_page(PageResult(items=[item], page=1, total_items=9, total_pages=2))
        result = CliRunner().invoke(cli, ["search", "--json", "artist", "x"])
        data = json.loads(result.stdout)
        assert data["pagination"] == {"page": 1, "total_items": 9, "total_pages": 2}


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
    def _patch_get(
        self,
        monkeypatch: pytest.MonkeyPatch,
        use_client: UseClient,
        serve_page: ServePage,
    ) -> None:
        self._monkeypatch = monkeypatch
        self._set_client = use_client
        self._serve_page = serve_page

    def test_get_release_json_projection(self) -> None:
        release = fake_model(
            id=847868,
            title="The Downward Spiral",
            year=1994,
            released="1994-03-08",
            country="US",
            artists=[fake(id=3857, name="Nine Inch Nails", join=None)],
            labels=[fake(id=647, name="Nothing Records", catalog_number="92346-2")],
            formats=[fake(name="CD", descriptions=["Album"])],
            genres=["Electronic", "Rock"],
            styles=None,
            master_id=3719,
            community=fake(have=7545, want=1342, rating=fake(average=4.42, count=593)),
            num_for_sale=49,
            lowest_price=2.45,
            notes="  Slipcase. ",
            tracklist=[
                fake(
                    position="1",
                    title="Mr. Self Destruct",
                    duration="4:30",
                    type=None,
                    artists=None,
                )
            ],
            extra_artists=[
                fake(id=20661, name="Flood", role="Producer", tracks="1, 2")
            ],
            identifiers=[fake(type="Barcode", value="765449234620", description=None)],
            images=[{"uri": "https://img…"}],
            videos=[{"uri": "https://youtube…"}],
        )
        self._set_client(fake_client(releases_get=lambda _id: release))
        result = CliRunner().invoke(cli, ["get", "--json", "release", "@r847868"])
        assert result.exit_code == 0
        assert json.loads(result.stdout) == {
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
            .stdout
        )
        assert verbose["notes"] == "Slipcase."
        assert verbose["credits"] == {
            "Producer": [{"ref": "@a20661", "name": "Flood", "tracks": "1, 2"}]
        }
        assert verbose["identifiers"] == [{"type": "Barcode", "value": "765449234620"}]

        full = json.loads(
            CliRunner()
            .invoke(cli, ["get", "--json", "--full", "release", "@r847868"])
            .stdout
        )
        assert full["id"] == 847868
        assert full["images"] == [{"uri": "https://img…"}]

    def test_get_artist_json_projection(self) -> None:
        artist = fake_model(
            id=3857,
            name="Nine Inch Nails",
            profile="Industrial rock project.",
            urls=["https://www.nin.com/", "https://twitter.com/nin"],
            members=[
                fake(id=27457, name="Trent Reznor", active=True),
                fake(id=4237, name="Chris Vrenna", active=False),
            ],
            images=[{"uri": "x"}],
        )
        self._set_client(fake_client(artists_get=lambda _id: artist))
        data = json.loads(
            CliRunner().invoke(cli, ["get", "--json", "artist", "@a3857"]).stdout
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
        self._set_client(fake_client(artists_get=lambda _id: artist))

        text = CliRunner().invoke(cli, ["get", "artist", "@a3857"]).stdout
        assert (
            "Members: Trent Reznor [@a27457], Unknown Status [@a5555]\n"
            "Former: Chris Vrenna [@a4237]\n"
        ) in text

        data = json.loads(
            CliRunner().invoke(cli, ["get", "--json", "artist", "@a3857"]).stdout
        )
        assert data["members"] == [
            {"ref": "@a27457", "name": "Trent Reznor"},
            {"ref": "@a5555", "name": "Unknown Status"},
        ]
        assert data["former"] == [{"ref": "@a4237", "name": "Chris Vrenna"}]

    def test_get_label_json_projection(self) -> None:
        label = fake_model(
            id=647,
            name="Nothing Records",
            profile=None,
            urls=None,
            parent_label=fake(id=2311, name="Interscope Records"),
            sub_labels=[fake(id=561260, name="NIN")],
        )
        self._set_client(fake_client(labels_get=lambda _id: label))
        data = json.loads(
            CliRunner().invoke(cli, ["get", "--json", "label", "@l647"]).stdout
        )
        assert data == {
            "ref": "@l647",
            "name": "Nothing Records",
            "parent": {"ref": "@l2311", "name": "Interscope Records"},
            "sub_labels": [{"ref": "@l561260", "name": "NIN"}],
        }

    def test_get_master_json_projection(self) -> None:
        master = fake_model(
            id=3719,
            title="The Downward Spiral",
            year=1994,
            artists=[fake(id=3857, name="Nine Inch Nails", join=None)],
            genres=["Rock"],
            styles=["Industrial"],
            main_release=847868,
            num_for_sale=200,
            lowest_price=1.5,
            tracklist=[
                fake(position="1", title="A", duration="", type=None, artists=None)
            ],
        )
        self._set_client(fake_client(masters_get=lambda _id: master))
        data = json.loads(
            CliRunner().invoke(cli, ["get", "--json", "master", "@m3719"]).stdout
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
            fake_client(
                releases_get=lambda _id: release, masters_get=lambda _id: master
            )
        )
        for noun, ref in (("release", "@r1"), ("master", "@m2")):
            text = CliRunner().invoke(cli, ["get", noun, ref]).stdout
            assert "Market: 9 for sale from 9.36" in text
            assert "$" not in text
            data = json.loads(
                CliRunner().invoke(cli, ["get", "--json", noun, ref]).stdout
            )
            assert data["market"] == {"for_sale": 9, "lowest": 9.36}

    def test_get_releases_json_projection(self) -> None:
        artist = listed(id=3857, name="NIN")
        self._set_client(fake_client(artists_get=lambda _id: artist))
        rel = fake_model(
            id=3373,
            type="master",
            title="Down In It",
            year=1989,
            role="Main",
            thumb="x",
        )
        self._serve_page(PageResult(items=[rel], page=1, total_items=1, total_pages=1))
        data = json.loads(
            CliRunner().invoke(cli, ["get", "--json", "releases", "@a3857"]).stdout
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
        master = listed(id=3719, title="TDS")
        self._set_client(fake_client(masters_get=lambda _id: master))
        ver = fake_model(
            id=847868,
            title="The Downward Spiral",
            released="1994",
            country="US",
            label="Nothing Records",
            catalog_number="92346-2",
            format="CD, Album",
            stats=fake(community=fake(in_collection=7544, in_wantlist=1)),
            thumb="x",
        )
        self._serve_page(PageResult(items=[ver], page=1, total_items=1, total_pages=1))
        data = json.loads(
            CliRunner().invoke(cli, ["get", "--json", "versions", "@m3719"]).stdout
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
        track = fake_model(
            position="A1",
            title="Track One",
            duration="4:00",
            type="track",
            artists=[fake(id=1, name="X", join=None)],
        )
        release = fake(id=123, title="R", tracklist=[track])
        self._set_client(fake_client(releases_get=lambda _id: release))
        data = json.loads(
            CliRunner().invoke(cli, ["get", "--json", "tracklist", "@r123"]).stdout
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
            .stdout
        )
        assert full == {"tracklist": [_via_json(track.model_dump())]}

    def test_get_credits_and_identifiers_json(self) -> None:
        credit = fake_model(id=20661, name="Flood", role="Producer", tracks="")
        ident = fake_model(type="Barcode", value="765449234620", description=None)
        release = fake(
            id=123, extra_artists=[credit], identifiers=[ident], tracklist=None
        )
        self._set_client(fake_client(releases_get=lambda _id: release))

        data = json.loads(
            CliRunner().invoke(cli, ["get", "--json", "credits", "@r123"]).stdout
        )
        assert data == {
            "ref": "@r123",
            "credits": {"Producer": [{"ref": "@a20661", "name": "Flood"}]},
        }
        data = json.loads(
            CliRunner().invoke(cli, ["get", "--json", "ids", "@r123"]).stdout
        )
        assert data == {
            "ref": "@r123",
            "identifiers": [{"type": "Barcode", "value": "765449234620"}],
        }
        full = json.loads(
            CliRunner().invoke(cli, ["get", "--json", "--full", "ids", "@r123"]).stdout
        )
        assert full == {"identifiers": [ident.model_dump()]}
        full = json.loads(
            CliRunner()
            .invoke(cli, ["get", "--json", "--full", "credits", "@r123"])
            .stdout
        )
        assert full == {"credits": [credit.model_dump()], "track_credits": []}

        empty = fake(id=123, extra_artists=None, identifiers=None)
        self._set_client(fake_client(releases_get=lambda _id: empty))
        data = json.loads(
            CliRunner().invoke(cli, ["get", "--json", "credits", "@r123"]).stdout
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
        release = fake(
            id=123,
            title="R",
            price_suggestions=fake(get=lambda: price_suggestions),
            marketplace_stats=fake(get=lambda: marketplace_stats),
        )
        self._set_client(fake_client(releases_get=lambda _id: release))
        data = json.loads(
            CliRunner().invoke(cli, ["get", "--json", "price", "@r123"]).stdout
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
            .stdout
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

        self._set_client(fake_client(masters_get=_missing))
        result = CliRunner().invoke(cli, ["get", "--json", "master", "@m4917"])
        assert result.exit_code == 1
        assert json.loads(result.stdout) == {
            "error": {
                "code": "not_found",
                "message": "Master @m4917 not found.",
                "hint": 'Try: agent-discogs search "<title>"',
                "status": 404,
            }
        }

        result = CliRunner().invoke(cli, ["get", "--json", "credits", "@a3857"])
        assert json_error(result)["code"] == "invalid_argument"

        result = CliRunner().invoke(
            cli, ["get", "--json", "versions", "@m3719", "--after", "2:0"]
        )
        assert json_error(result)["code"] == "invalid_argument"

    def test_get_full_requires_json(self) -> None:
        self._set_client(fake_client())
        result = CliRunner().invoke(cli, ["get", "--full", "master", "@m3719"])
        assert text_error(result) == "✗ --full requires --json.\n"


class TestJsonShortcuts:
    def test_tracks_json(self, use_client: UseClient) -> None:
        track = fake_model(position="1", title="Song", duration="3:00")
        release = fake(id=123, tracklist=[track])
        use_client(fake_client(releases_get=lambda _id: release))
        result = CliRunner().invoke(cli, ["tracks", "--json", "@r123"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert isinstance(data["tracklist"], list)
        assert data["tracklist"][0]["title"] == "Song"

    def test_price_json(self, use_client: UseClient) -> None:
        price_suggestions = PriceSuggestions.model_validate(
            {"Mint (M)": {"value": 50.0, "currency": "USD"}}
        )
        marketplace_stats = MarketplaceReleaseStats.model_validate(
            {"num_for_sale": 10, "lowest_price": None}
        )
        release = fake(
            id=123,
            title="R",
            price_suggestions=fake(get=lambda: price_suggestions),
            marketplace_stats=fake(get=lambda: marketplace_stats),
        )
        use_client(fake_client(releases_get=lambda _id: release))
        result = CliRunner().invoke(cli, ["price", "--json", "@r123"])
        assert result.exit_code == 0
        assert json.loads(result.stdout) == {
            "ref": "@r123",
            "title": "R",
            "suggestions": {"Mint (M)": {"value": 50.0, "currency": "USD"}},
            "market": {"for_sale": 10},
        }

        result = CliRunner().invoke(cli, ["price", "--full", "@r123"])
        assert text_error(result) == "✗ --full requires --json.\n"


class TestUnknownDates:
    """Discogs sends year 0 / `"0"` for unknown and pads unknown date parts
    with `-00`; neither may reach an agent."""

    @pytest.fixture(autouse=True)
    def _patch_clients(
        self, monkeypatch: pytest.MonkeyPatch, use_client: UseClient
    ) -> None:
        release = Release.model_validate(
            {"id": 1, "title": "R", "year": 0, "released": "1994-05-00"}
        )
        master = Master.model_validate({"id": 3, "title": "M", "year": 0})
        client = fake_client(
            releases_get=lambda _id: release, masters_get=lambda _id: master
        )
        use_client(client)
        item = SearchResult.model_validate(
            {"id": 10, "type": "release", "title": "R", "year": "0"}
        )

        def page(*_a: object, **_kw: object) -> PageResult:
            return PageResult(items=[item], page=1, total_items=1, total_pages=1)

        monkeypatch.setattr("agent_discogs.commands.search.fetch_page", page)
        monkeypatch.setattr("agent_discogs.pagination.fetch_page", page)

    def test_year_zero_is_omitted_from_text(self) -> None:
        release = CliRunner().invoke(cli, ["get", "release", "@r1"])
        assert release.stdout.splitlines()[0] == '@r1 [release] "R" by Unknown Artist'
        master = CliRunner().invoke(cli, ["get", "master", "@m3"])
        assert master.stdout.splitlines()[0] == '@m3 [master] "M" by Unknown Artist'
        search = CliRunner().invoke(cli, ["search", "release", "R"])
        assert search.stdout.splitlines()[2] == '@r10 "R"'

    def test_unknown_parts_are_omitted_from_json(self) -> None:
        def run(*args: str) -> object:
            return json.loads(CliRunner().invoke(cli, list(args)).stdout)

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
    def _patch_get(self, use_client: UseClient) -> None:
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
        use_client(fake_client(releases_get=lambda _id: release))

    expected = (
        "2×Vinyl (LP, Album, 180g, Gatefold); "  # noqa: RUF001
        "CD (Album); All Media (Box); Box Set"
    )

    def test_text(self) -> None:
        output = CliRunner().invoke(cli, ["get", "release", "@r1"]).stdout
        assert f"Format: {self.expected}" in output.splitlines()

    def test_json(self) -> None:
        result = CliRunner().invoke(cli, ["get", "--json", "release", "@r1"])
        assert json.loads(result.stdout)["formats"] == self.expected


class TestDateAdded:
    """`date_added` is when the release joined Discogs; shown in its own offset."""

    @pytest.fixture(autouse=True)
    def _patch_get(self, use_client: UseClient) -> None:
        base = {"title": "R", "country": "US", "released": "1994-03-08"}
        releases = {
            # 23:30 at -07:00 is already 2026-10-04 in UTC.
            1: Release.model_validate(
                {**base, "id": 1, "date_added": "2026-10-03T23:30:00-07:00"}
            ),
            2: Release.model_validate({**base, "id": 2}),
        }
        use_client(fake_client(releases_get=releases.__getitem__))

    def test_text_shows_the_date_beside_the_release_date(self) -> None:
        output = CliRunner().invoke(cli, ["get", "release", "@r1"]).stdout
        assert (
            "Country: US · Released: 1994-03-08 · Added: 2026-10-03"
            in output.splitlines()
        )

    def test_json_carries_rfc3339_timestamp(self) -> None:
        result = CliRunner().invoke(cli, ["get", "--json", "release", "@r1"])
        assert json.loads(result.stdout)["added"] == "2026-10-03T23:30:00-07:00"

    def test_missing_date_is_omitted(self) -> None:
        output = CliRunner().invoke(cli, ["get", "release", "@r2"]).stdout
        assert "Country: US · Released: 1994-03-08" in output.splitlines()
        result = CliRunner().invoke(cli, ["get", "--json", "release", "@r2"])
        data = json.loads(result.stdout)
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
    track = fake_model(position="1", title="Song", duration="3:00", type=None)
    release = fake(id=123, title="Album", year=2020, artists=None, tracklist=[track])

    def _get(_id: int) -> object:
        recorder()
        return release

    return fake_client(releases_get=_get)


def _no_home() -> Path:
    msg = "Could not determine home directory."
    raise RuntimeError(msg)


class TestBudgetFooter:
    """The footer is stderr-only, printed after the command, in every mode."""

    def test_footer_on_stderr_not_stdout(
        self, monkeypatch: pytest.MonkeyPatch, use_client: UseClient
    ) -> None:
        monkeypatch.setenv("DISCOGS_TOKEN", "t")
        use_client(_tracks_fake(lambda: trace.record(_network_event(43))))
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
        self, monkeypatch: pytest.MonkeyPatch, use_client: UseClient
    ) -> None:
        def _boom() -> None:
            trace.record(_network_event(5))
            raise RuntimeError("after the request")

        monkeypatch.setenv("DISCOGS_TOKEN", "t")
        use_client(_tracks_fake(_boom))
        result = CliRunner().invoke(cli, ["tracks", "--json", "@r123"])
        assert result.exit_code == 1
        assert json.loads(result.stdout)["error"]["code"] == "unexpected"
        assert result.stderr == (
            "api: 1 request · 5/60 left this minute"
            " ⚠ pause ~60s before uncached calls\n"
        )

    def test_unauthenticated_wording(self, use_client: UseClient) -> None:
        use_client(_tracks_fake(lambda: trace.record(_network_event(3, 25))))
        result = CliRunner().invoke(cli, ["tracks", "@r123"])
        assert result.stderr.endswith(
            "3/25 left this minute ⚠ set DISCOGS_TOKEN for 60/min, or pause ~60s\n"
        )

    def test_consumer_credentials_are_not_told_to_set_a_token(
        self, monkeypatch: pytest.MonkeyPatch, use_client: UseClient
    ) -> None:
        monkeypatch.setenv("DISCOGS_CONSUMER_KEY", "k")
        monkeypatch.setenv("DISCOGS_CONSUMER_SECRET", "s")
        use_client(_tracks_fake(lambda: trace.record(_network_event(3, 60))))
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
        price = fake_model(conditions={"Mint (M)": fake(value=50.0, currency="USD")})
        stats = fake_model(num_for_sale=10, lowest_price=None)

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
            return fake(
                id=release_id,
                title=f"R{release_id}",
                price_suggestions=fake(get=lambda: price),
                marketplace_stats=fake(get=lambda: stats),
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
    def _patch_get(self, use_client: UseClient) -> None:
        release = _suite_release()
        use_client(fake_client(releases_get=lambda _id: release))

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
    def _patch_get(self, use_client: UseClient) -> None:
        self._use_client = use_client

    def _serve(self, release: object) -> None:
        self._use_client(fake_client(releases_get=lambda _id: release))

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
    def test_panel_on_stderr_stdout_identical(self, use_client: UseClient) -> None:
        use_client(_tracks_fake(lambda: trace.record(_network_event())))
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

    def test_env_var_enables_debug(self, use_client: UseClient) -> None:
        use_client(_tracks_fake(lambda: None))
        result = CliRunner(env={"AGENT_DISCOGS_DEBUG": "1"}).invoke(
            cli, ["tracks", "@r123"]
        )
        assert result.exit_code == 0
        assert "── debug ──" in result.stderr
        assert "── debug ──" not in result.stdout

    def test_sdk_log_passthrough_only_with_debug(self, use_client: UseClient) -> None:
        sdk_logger = logging.getLogger("discogs_sdk")
        use_client(_tracks_fake(lambda: sdk_logger.debug("Cache hit: GET /x")))

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
        self, monkeypatch: pytest.MonkeyPatch, use_client: UseClient
    ) -> None:
        def _missing(_id: int) -> object:
            raise NotFoundError("nope", status_code=404, response_body={})

        use_client(fake_client(releases_get=_missing))
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
    Remix, year 1990), so client-side scans read every page of the listing."""
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
    def _client(self, use_client: UseClient) -> None:
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
            on_request=trace.record,
            max_retries=0,
        )
        use_client(client)

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
            # Client-side scans read 100-row pages until the page fills or the
            # listing ends: nothing matches here, so all 10 pages. Text adds
            # the header lookup; --max-requests caps the whole command.
            ("get releases @a1 --role Main --json", 10),
            ("get releases @a1 --role Main", 11),
            ("get releases @a1 --role Main --max-requests 4", 4),
            ("get releases @l1 --year 1999 --json", 10),
            ("get releases @l1 --year 1999", 11),
            ("get releases @l1 --year 1999 --json --max-requests 4", 4),
            ("search x --release-type all", 1),
            ("search artist x", 1),
            ("search x", 10),
            ("search x --max-requests 4", 4),
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
_FILTERED_PAGES = [[98, 99], [198, 199], [298, 299]]


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

    def test_capped_scan_continues_with_its_cap(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A filter that matches nothing stops each capped scan at its
        `--max-requests`; each "Continue scan" command repeats the cap and
        resumes at the next unscanned API page."""
        api = _FakeAPI(monkeypatch, _Catalogue(1500, sparse=True))  # 15 API pages
        argv = shlex.split("search --limit 2 --max-requests 5 -- release -x")

        def pages_sent(args: list[str]) -> tuple[Result, list[int]]:
            start = len(api.sends)
            result = CliRunner().invoke(cli, args)
            return result, [int(p["page"]) for _, p in api.sends[start:]]

        warm, sent = pages_sent(argv)
        assert sent == [1, 2, 3, 4, 5]
        continuation = _continuation(warm)
        assert continuation[continuation.index("--max-requests") + 1] == "5"
        _, sent = pages_sent(continuation)
        assert sent == [6, 7, 8, 9, 10]

        # Cache hits are free, so a cache-only scan never reaches its cap: it
        # reads the ten cached pages and fails on the eleventh.
        cached, sent = pages_sent(["--cached", *argv])
        assert cached.exit_code == 1
        assert "Not cached: GET /database/search" in cached.stderr
        assert sent == []


class _ScanCatalogue:
    """`rows` rows behind every listing (search results, artist and label
    releases), of which only the ids in `matches` pass the client-side filters
    (official format, role Main, year 1999). The API pages in `throttle` are
    answered with a 429 asking to retry in 30 seconds."""

    def __init__(
        self, matches: set[int], *, rows: int = 1000, throttle: tuple[int, ...] = ()
    ) -> None:
        self.matches, self.rows, self.throttle = matches, rows, throttle

    def __call__(self, path: str, params: dict[str, str]) -> object:
        key = _LISTING_KEYS.get(path.rsplit("/", 1)[-1])
        if key is None:
            return {"id": 1, "name": "Entity", "title": "Entity"}
        page, per = int(params["page"]), int(params["per_page"])
        if page in self.throttle:
            return httpx2.Response(429, json={}, headers={"Retry-After": "30"})
        rows = []
        for i in range((page - 1) * per, min(page * per, self.rows)):
            match = i in self.matches
            row: dict[str, object] = {"id": i, "title": f"Row{i}e1", "type": "release"}
            if key == "results":
                row["format"] = ["Vinyl"] if match else ["Unofficial Release"]
            else:
                row["role"] = "Main" if match else "Remix"
                row["year"] = 1999 if match else 2000
            rows.append(row)
        pagination = {"page": page, "pages": -(-self.rows // per), "items": self.rows}
        return {"pagination": pagination, key: rows}


# One match on each of API pages 1, 2, 5, 7 and 8 (100 rows a page): a
# `--limit 5` page fills on page 8, with rows left after it.
_SPARSE = {50, 150, 450, 650, 750}


class TestUncappedScan:
    """A client-side filtered listing scans until it fills `--limit` or runs
    out of rows; `--max-requests N` caps the sends of the whole command."""

    def _api(self, monkeypatch: pytest.MonkeyPatch, **kwargs: Any) -> _FakeAPI:
        self.api = _FakeAPI(
            monkeypatch, _ScanCatalogue(kwargs.pop("matches", _SPARSE), **kwargs)
        )
        return self.api

    def _run(self, *args: str) -> tuple[Result, list[tuple[str, int]]]:
        start = len(self.api.sends)
        result = CliRunner().invoke(cli, list(args))
        sent = [(path, int(p.get("page", 0))) for path, p in self.api.sends[start:]]
        return result, sent

    def test_sparse_filter_runs_to_completion(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._api(monkeypatch)
        result, sent = self._run("search", "x", "--json")
        assert result.exit_code == 0, result.output
        assert [page for _, page in sent] == list(range(1, 9))
        assert {p["per_page"] for _, p in self.api.sends} == {"100"}
        document = json.loads(result.stdout)
        assert [row["ref"] for row in document["results"]] == [
            f"@r{i}" for i in sorted(_SPARSE)
        ]
        assert document["pagination"]["capped"] is False
        assert "stop_reason" not in document["pagination"]
        assert document["pagination"]["next_cursor"] == "2:751"

        text, sent = self._run("search", "x")
        assert sent == []  # every page is cached now
        assert "\nNext page: " in text.stdout
        assert "Continue scan" not in text.stdout

    def test_dense_filter_costs_one_request(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._api(monkeypatch, matches=set(range(1000)))
        result, sent = self._run("search", "x", "--json")
        assert result.exit_code == 0, result.output
        assert sent == [("/database/search", 1)]

    def test_max_requests_caps_and_the_continuation_resumes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._api(monkeypatch)
        result, sent = self._run("search", "x", "--max-requests", "3", "--json")
        assert result.exit_code == 0, result.output
        assert [page for _, page in sent] == [1, 2, 3]
        pagination = json.loads(result.stdout)["pagination"]
        assert pagination["capped"] is True
        assert pagination["stop_reason"] == "max_requests"
        assert pagination["next_cursor"] == "2:300"
        assert (pagination["total_items"], pagination["total_pages"]) == (1000, 10)

        self._api(monkeypatch)  # a cold cache again
        text, _ = self._run("search", "x", "--max-requests", "3")
        assert "scan stopped at --max-requests 3" in text.stdout
        assert "\nContinue scan: " in text.stdout
        continuation = _continuation(text)
        assert continuation[continuation.index("--max-requests") + 1] == "3"
        assert continuation[continuation.index("--after") + 1] == "2:300"

        resumed, sent = self._run(*continuation)
        assert resumed.exit_code == 0, resumed.output
        assert [page for _, page in sent] == [4, 5, 6]
        assert [int(i) for i, _ in _ROW.findall(resumed.stdout)] == [450]
        assert "3:600" in _continuation(resumed)

    def test_rate_limit_mid_scan_returns_the_rows_so_far(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._api(monkeypatch, throttle=(4,))
        result, sent = self._run("search", "x", "--json")
        assert result.exit_code == 0, result.output
        assert [page for _, page in sent] == [1, 2, 3, 4]
        document = json.loads(result.stdout)
        assert [row["ref"] for row in document["results"]] == ["@r50", "@r150"]
        pagination = document["pagination"]
        assert pagination["capped"] is True
        assert pagination["stop_reason"] == "rate_limited"
        assert pagination["retry_after"] == 30
        assert pagination["next_cursor"] == "2:300"

        text, _ = self._run("search", "x")
        assert text.exit_code == 0, text.output
        assert "rate limited" in text.stdout.splitlines()[0]
        assert "retry in 30s" in text.stdout.splitlines()[0]
        assert "\nContinue scan: " in text.stdout

    def test_rate_limit_on_the_first_request_is_an_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._api(monkeypatch, throttle=(1,))
        result, sent = self._run("search", "x", "--json")
        assert result.exit_code == 1
        assert json.loads(result.stdout)["error"]["code"] == "rate_limited"
        assert sent == [("/database/search", 1)]

    @pytest.mark.parametrize("value", ["0", "-1"])
    @pytest.mark.parametrize(
        "argv", ["search x", "get releases @a1 --role Main"], ids=["search", "get"]
    )
    def test_non_positive_max_requests_is_rejected(
        self, monkeypatch: pytest.MonkeyPatch, argv: str, value: str
    ) -> None:
        self._api(monkeypatch)
        result, sent = self._run(*argv.split(), "--max-requests", value, "--json")
        assert result.exit_code == 1
        error = json.loads(result.stdout)["error"]
        assert error["code"] == "invalid_argument"
        assert "--max-requests" in error["message"]
        assert sent == []

    @pytest.mark.parametrize(
        "argv",
        [
            "search x --release-type all",
            "search artist x",
            "get versions @m1",
            "get releases @a1",
            "get releases @l1",
            "get releases @a1 @a2 --role Main",
            "get releases @l1 @l2 --year 1999",
        ],
    )
    def test_max_requests_needs_one_scan(
        self, monkeypatch: pytest.MonkeyPatch, argv: str
    ) -> None:
        self._api(monkeypatch)
        result, sent = self._run(*argv.split(), "--max-requests", "3", "--json")
        assert result.exit_code == 1
        error = json.loads(result.stdout)["error"]
        assert error["code"] == "invalid_argument"
        assert "--max-requests" in error["message"]
        assert sent == []

    def test_header_lookup_counts_first(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._api(monkeypatch)
        argv = ["get", "releases", "@a1", "--role", "Main", "--max-requests", "3"]
        cold, sent = self._run(*argv)
        assert cold.exit_code == 0, cold.output
        releases = "/artists/1/releases"
        assert sent == [("/artists/1", 0), (releases, 1), (releases, 2)]
        assert "\nContinue scan: " in cold.stdout

    def test_cached_header_leaves_the_budget_to_the_scan(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._api(monkeypatch)
        self._run("get", "artist", "@a1")  # only the artist is cached
        argv = ["get", "releases", "@a1", "--role", "Main", "--max-requests", "3"]
        warm, sent = self._run(*argv)
        assert warm.exit_code == 0, warm.output
        releases = "/artists/1/releases"
        assert sent == [(releases, 1), (releases, 2), (releases, 3)]

    @pytest.mark.parametrize("warm", [False, True], ids=["cold", "warm"])
    def test_json_reads_no_header(
        self, monkeypatch: pytest.MonkeyPatch, warm: bool
    ) -> None:
        self._api(monkeypatch)
        if warm:
            self._run("get", "artist", "@a1")
        argv = ["get", "releases", "@a1", "--role", "Main", "--json"]
        result, sent = self._run(*argv, "--max-requests", "3")
        assert result.exit_code == 0, result.output
        assert sent == [("/artists/1/releases", page) for page in (1, 2, 3)]
        assert json.loads(result.stdout)["pagination"]["capped"] is True

        # Cache hits are free: pages 1-3 cost nothing, the one send reads page 4.
        result, sent = self._run(*argv, "--max-requests", "1")
        assert sent == [("/artists/1/releases", 4)]
        assert json.loads(result.stdout)["pagination"]["next_cursor"] == "2:400"

    def test_json_spends_one_send_on_the_first_page(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._api(monkeypatch)
        argv = ["get", "releases", "@a1", "--role", "Main", "--json"]
        result, sent = self._run(*argv, "--max-requests", "1")
        assert result.exit_code == 0, result.output
        assert sent == [("/artists/1/releases", 1)]
        pagination = json.loads(result.stdout)["pagination"]
        assert (pagination["total_items"], pagination["total_pages"]) == (1000, 10)
        assert pagination["next_cursor"] == "2:100"

    def test_label_lookup_runs_before_the_scan(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._api(monkeypatch)
        argv = ["get", "releases", "@l1", "--year", "1999", "--max-requests", "2"]
        result, sent = self._run(*argv)
        assert result.exit_code == 0, result.output
        assert sent == [("/labels/1", 0), ("/labels/1/releases", 1)]
        assert '"Entity"' in result.stdout.splitlines()[0]

    def test_budget_spent_before_the_scan(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._api(monkeypatch)
        argv = ["get", "releases", "@a1", "--role", "Main", "--max-requests", "1"]
        result, sent = self._run(*argv)
        assert result.exit_code == 0, result.output
        assert sent == [("/artists/1", 0)]
        header = result.stdout.splitlines()[0]
        assert "before the scan started" in header
        assert "≤" not in result.stdout
        assert not re.search(r"\b0\b", result.stdout.split("Continue scan:")[0])
        assert "page" not in header
        continuation = _continuation(result)
        assert continuation[continuation.index("--after") + 1] == "1:0"

        resumed, sent = self._run(*continuation)
        assert resumed.exit_code == 0, resumed.output
        assert sent == [("/artists/1/releases", 1)]
        assert [int(i) for i, _ in _ROW.findall(resumed.stdout)] == [50]

    def test_fresh_header_budget_says_to_raise_the_cap(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Under --fresh the header is sent on every run: a cap of one still
        returns the empty capped page, and says the continuation needs more."""
        self._api(monkeypatch)
        argv = ["--fresh", "get", "releases", "@a1", "--role", "Main"]
        result, sent = self._run(*argv, "--max-requests", "1")
        assert result.exit_code == 0, result.output
        assert sent == [("/artists/1", 0)]
        header = result.stdout.splitlines()[0]
        assert "before the scan started" in header
        assert "continue with a higher --max-requests" in header
        continuation = _continuation(result)
        assert continuation[continuation.index("--after") + 1] == "1:0"

        # Without --fresh the same empty page carries no such note: the header
        # is cached from now on, so the same cap reaches the scan next time.
        result, _ = self._run(*argv[1:], "--max-requests", "1")
        assert result.exit_code == 0, result.output
        assert "higher --max-requests" not in result.stdout

    def test_non_listing_noun_names_what_it_takes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._api(monkeypatch)
        result, sent = self._run("get", "artist", "@a1", "--max-requests", "3")
        assert result.exit_code == 1
        assert "--max-requests does not apply to 'get artist'" in result.stderr
        assert sent == []


_NEWEST_ADDED = datetime(2026, 10, 1, 12, tzinfo=timezone(timedelta(hours=-7)))


class _AddedCatalogue:
    """`rows` releases behind a date-sorted release search, five a page, and
    a `/releases/<id>` record for each. Release `i` was added `i` days before
    2026-10-01 (newest first), so `--added-since 2026-09-25` keeps ids 0-6;
    the ids in `unofficial` carry the "Unofficial Release" description, and
    with `shuffled` release 3 claims a later date than release 2."""

    def __init__(
        self,
        rows: int = 12,
        *,
        unofficial: frozenset[int] = frozenset(),
        shuffled: bool = False,
        hour: int = 12,
    ) -> None:
        self.rows, self.unofficial, self.shuffled = rows, unofficial, shuffled
        self.newest = _NEWEST_ADDED.replace(hour=hour)

    def __call__(self, path: str, params: dict[str, str]) -> object:
        if path == "/database/search":
            page, per = int(params.get("page", "1")), 5
            ids = range((page - 1) * per, min(page * per, self.rows))
            pages = -(-self.rows // per)
            urls = {"next": "https://api.discogs.com/next"} if page < pages else {}
            pagination = {"page": page, "pages": pages, "items": self.rows}
            results = [{"id": i, "type": "release", "title": f"T{i}"} for i in ids]
            return {"pagination": {**pagination, "urls": urls}, "results": results}
        release_id = int(path.rsplit("/", 1)[1])
        days_back = 1 if self.shuffled and release_id == 3 else release_id
        added = (self.newest - timedelta(days=days_back)).isoformat()
        descriptions = ["Album"]
        if release_id in self.unofficial:
            descriptions.append("Unofficial Release")
        return {
            "id": release_id,
            "title": f"T{release_id}",
            "artists": [
                {"id": 9, "name": "Band", "join": "&"},
                {"id": 7, "name": "Guest", "join": ""},
            ],
            "year": 2026,
            "master_id": 300 + release_id,
            "country": "US",
            "labels": [{"id": 8, "name": "Lab", "catno": f"C{release_id}"}],
            "formats": [{"name": "CD", "qty": "1", "descriptions": descriptions}],
            "date_added": added,
            "uri": f"https://www.discogs.com/release/{release_id}",
        }


_ADDED = ["search", "release", "--artist", "Band", "--added-since", "2026-09-25"]


class TestAddedSince:
    """`search release ... --added-since` wraps the SDK's experimental
    `releases_added_since()`: uncapped by default, `--max-requests` opt-in."""

    def _api(self, monkeypatch: pytest.MonkeyPatch, **kwargs: Any) -> _FakeAPI:
        self.api = _FakeAPI(monkeypatch, _AddedCatalogue(**kwargs))
        return self.api

    def _run(self, *args: str) -> tuple[Result, list[str]]:
        start = len(self.api.sends)
        result = CliRunner().invoke(cli, list(args))
        return result, [path for path, _ in self.api.sends[start:]]

    def test_window_comes_back_newest_first_with_added_dates(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._api(monkeypatch)
        result, sent = self._run(*_ADDED, "--release-type", "all", "--json")
        assert result.exit_code == 0, result.output
        document = json.loads(result.stdout)
        assert document["complete"] is True
        assert document["total"] == 7
        rows = document["results"]
        assert [row["ref"] for row in rows] == [f"@r{i}" for i in range(7)]
        assert rows[0]["added"] == "2026-10-01T12:00:00-07:00"
        assert rows[0]["title"] == "Band & Guest - T0"
        assert rows[0]["master"] == "@m300"
        assert rows[6]["added"] == "2026-09-25T12:00:00-07:00"
        # Two search pages, a lookup for each release in the window, and one
        # for release 7, the first older than the cutoff.
        assert sent.count("/database/search") == 2
        assert len(sent) == 10
        search = next(p for path, p in self.api.sends if path == "/database/search")
        assert (search["type"], search["artist"]) == ("release", "Band")
        assert (search["sort"], search["sort_order"]) == ("date_added", "desc")
        assert search["per_page"] == "100"

        text, sent = self._run(*_ADDED, "--release-type", "all")
        assert sent == []  # all cached
        lines = text.stdout.splitlines()
        assert lines[0] == (
            "Search: release artist=Band release-type=all added since 2026-09-25 "
            "(7 results, newest first)"
        )
        assert lines[2] == (
            '@r0 "Band & Guest - T0" 2026 US · Lab C0 · CD (Album) · added '
            "2026-10-01 → @m300"
        )
        assert len(lines) == 2 + 7

    def test_max_requests_stops_with_an_incomplete_result(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._api(monkeypatch)
        argv = [*_ADDED, "--release-type", "all", "--max-requests", "3"]
        result, sent = self._run(*argv, "--json")
        assert result.exit_code == 0, result.output
        assert sent == ["/database/search", "/releases/0", "/releases/1"]
        document = json.loads(result.stdout)
        assert document["complete"] is False
        assert [row["ref"] for row in document["results"]] == ["@r0", "@r1"]

        self._api(monkeypatch)  # a cold cache again
        text, sent = self._run(*argv)
        assert len(sent) == 3
        assert text.stdout.splitlines()[-1].startswith(
            "Stopped at --max-requests 3: the result may be incomplete."
        )

    def test_cached_window_is_complete_within_one_request(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._api(monkeypatch)
        self._run(*_ADDED, "--release-type", "all")
        result, sent = self._run(
            *_ADDED, "--release-type", "all", "--max-requests", "1", "--json"
        )
        assert result.exit_code == 0, result.output
        assert sent == []
        document = json.loads(result.stdout)
        assert document["complete"] is True
        assert len(document["results"]) == 7

    def test_release_type_filters_on_looked_up_formats(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._api(monkeypatch, unofficial=frozenset({2, 5}))
        result, sent = self._run(*_ADDED, "--json")  # official, the default
        assert result.exit_code == 0, result.output
        refs = [row["ref"] for row in json.loads(result.stdout)["results"]]
        assert refs == ["@r0", "@r1", "@r3", "@r4", "@r6"]
        assert len(sent) == 10  # what --release-type all costs: no extra request

        result, _ = self._run(*_ADDED, "--format", "CD", "--release-type", "unofficial")
        assert result.exit_code == 0, result.output
        assert [int(i) for i in re.findall(r"@r(\d+)", result.stdout)] == [2, 5]

    def test_unofficial_is_filtered_by_discogs(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._api(monkeypatch)
        result, _ = self._run(*_ADDED, "--release-type", "unofficial", "--json")
        assert result.exit_code == 0, result.output
        search = next(p for path, p in self.api.sends if path == "/database/search")
        assert search["format"] == "Unofficial Release"
        # Discogs did the filtering; the formats are not checked again.
        assert len(json.loads(result.stdout)["results"]) == 7

    def test_explicit_limit_caps_the_rows(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._api(monkeypatch)
        argv = [*_ADDED, "--release-type", "all", "--limit", "3"]
        result, _ = self._run(*argv, "--json")
        document = json.loads(result.stdout)
        assert (len(document["results"]), document["total"]) == (3, 7)
        text, _ = self._run(*argv)
        assert "(3 of 7 results, newest first)" in text.stdout.splitlines()[0]

    def test_rendering_failure_is_inside_the_boundary(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._api(monkeypatch)

        def _boom(*_a: object, **_kw: object) -> None:
            raise RuntimeError("bad row")

        monkeypatch.setattr(
            "agent_discogs.commands.search.project_added_release", _boom
        )
        result, _ = self._run(*_ADDED, "--json")
        assert result.exit_code == 1
        assert json.loads(result.stdout)["error"]["code"] == "unexpected"

        monkeypatch.setattr(
            "agent_discogs.commands.search.format_added_releases", _boom
        )
        result, _ = self._run(*_ADDED)
        assert result.exit_code == 1
        assert result.stdout == ""
        assert "bad row" in result.stderr

    @pytest.mark.parametrize(
        "argv",
        [
            "search artist Band --added-since 2026-09-25",
            "search Band --added-since 2026-09-25",
            "search release Band --added-since 2026-09-25 --sort date_added",
            "search release Band --added-since 2026-09-25 --desc",
            "search release Band --added-since 2026-09-25 --page 2",
            "search release Band --added-since 2026-09-25 --after 2:0",
            "search release Band --added-since 2026-09-25 --max-requests 0",
        ],
    )
    def test_rejected_before_any_request(
        self, monkeypatch: pytest.MonkeyPatch, argv: str
    ) -> None:
        self._api(monkeypatch)
        result, sent = self._run(*argv.split(), "--json")
        assert result.exit_code == 1
        assert json.loads(result.stdout)["error"]["code"] == "invalid_argument"
        assert sent == []

    def test_cached_lookup_miss_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._api(monkeypatch)
        self._run(*_ADDED, "--release-type", "all", "--max-requests", "2")
        result, sent = self._run("--cached", *_ADDED, "--release-type", "all", "--json")
        assert result.exit_code == 1
        error = json.loads(result.stdout)["error"]
        assert error["code"] == "cache_miss"
        assert "/releases/1" in error["message"]  # the search page was cached
        assert sent == []

    def test_rows_show_the_utc_day_the_cutoff_compares(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Release 6, added 2026-09-25 20:00 Pacific, is 09-26 in UTC: inside
        an --added-since 2026-09-26 window, and its row says so."""
        self._api(monkeypatch, hour=20)
        argv = ["search", "release", "--artist", "Band", "--release-type", "all"]
        result, _ = self._run(*argv, "--added-since", "2026-09-26")
        assert result.exit_code == 0, result.output
        lines = result.stdout.splitlines()
        assert "(7 results, newest first)" in lines[0]
        assert lines[-1].endswith("· added 2026-09-26 → @m306")

    def test_broken_date_order_is_reported(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._api(monkeypatch, shuffled=True)
        result, _ = self._run(*_ADDED, "--release-type", "all", "--json")
        assert result.exit_code == 1
        error = json.loads(result.stdout)["error"]
        assert error["code"] == "bad_response"
        assert "not sorted by date_added" in error["message"]


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
    def _real_client(self, real_sdk: RealSdk) -> Iterator[None]:
        self.sdk = real_sdk
        self.sends, self.sleeps = real_sdk.sends, real_sdk.sleeps
        self.cache_home = real_sdk.cache_home
        real_sdk.respond = lambda _request: httpx2.Response(
            200,
            json=_RELEASE_WITH_HEADINGS,
            headers={
                "X-Discogs-Ratelimit": "60",
                "X-Discogs-Ratelimit-Used": "1",
                "X-Discogs-Ratelimit-Remaining": "59",
            },
        )
        yield
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
        self.sdk.respond = lambda _request: next(replies)

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
        self.sdk.respond = lambda _request: httpx2.Response(
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
        self.sdk.respond = lambda _request: httpx2.Response(
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
        self.sdk.respond = lambda _request: httpx2.Response(
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

        self.sdk.respond = _refuse
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
        self.sdk.respond = lambda _request: httpx2.Response(200, json=release)
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
        self.sdk.respond = lambda _request: httpx2.Response(
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
            self.sdk.respond = lambda _request, sent=sent: httpx2.Response(
                200, json=sent
            )
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
            self.sdk.respond = lambda request, listing=listing: httpx2.Response(
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
        self.sdk.respond = lambda _request: httpx2.Response(
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
        fails with `bad_response` instead of `invalid_argument` or an empty page.
        A text catalogue's label header, read first, gets a valid answer."""
        label = httpx2.Response(200, json={"id": 1, "name": "Label"})
        self.sdk.respond = lambda request: (
            label
            if request.url.path == "/labels/1"
            else httpx2.Response(200, text="<html>Bad gateway</html>")
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
        listing = [path for path in self.sends if path != "/labels/1"]
        assert listing == [self.sends[0]] * 2

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

        self.sdk.respond = _respond
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
        self.sdk.respond = lambda _request: httpx2.Response(
            200, json={"results": [row]}
        )
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
        self.sdk.respond = lambda _request: httpx2.Response(
            200, json={"id": "one", "title": "T"}
        )
        client = client_module.get_client()
        client._send("GET", client._build_url("/releases/1"), parse=lambda d: d)
        self.sdk.respond = lambda _request: httpx2.Response(
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
