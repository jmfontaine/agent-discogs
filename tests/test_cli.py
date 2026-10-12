"""Tests for CLI argument parsing and dispatch."""

from __future__ import annotations

import importlib
import importlib.metadata
import json
import logging
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace

import click
import pytest
from click.testing import CliRunner
from discogs_sdk import (
    Artist,
    CacheMissError,
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

from agent_discogs import cli, main, trace
from agent_discogs.pagination import PageResult


def _fake(**kwargs: object) -> SimpleNamespace:
    return SimpleNamespace(**kwargs)


def _fake_client(
    *,
    artists_get: object = None,
    labels_get: object = None,
    masters_get: object = None,
    releases_get: object = None,
) -> SimpleNamespace:
    """Build a fake Discogs client with specified resource getters."""
    return SimpleNamespace(
        artists=SimpleNamespace(get=artists_get or (lambda _id: None)),
        labels=SimpleNamespace(get=labels_get or (lambda _id: None)),
        masters=SimpleNamespace(get=masters_get or (lambda _id: None)),
        releases=SimpleNamespace(get=releases_get or (lambda _id: None)),
    )


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
        monkeypatch.setattr("agent_discogs.commands.search.get_client", lambda: None)

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

    def test_search_pagination_next_uses_cursor(self) -> None:
        """Default (official) search pages by cursor; header marks the bound."""
        result_item = _fake(
            id=1, type="release", title="Test", year=None, label=None, format=None
        )
        self._set_fetch_result(
            PageResult(items=[result_item], page=1, total_items=50, total_pages=10)
        )
        result = CliRunner().invoke(cli, ["search", "test"])
        assert result.exit_code == 0
        assert "of ≤50 results" in result.output
        assert "Next page: agent-discogs search test --after 2:6.0" in result.output
        assert "--page" not in result.output

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

    def test_search_footer_is_shell_safe_and_keeps_limit(self) -> None:
        result_item = _fake(
            id=1, type="release", title="Test", year=None, label=None, format=None
        )
        self._set_fetch_result(
            PageResult(items=[result_item], page=1, total_items=50, total_pages=10)
        )
        result = CliRunner().invoke(
            cli,
            [
                "search",
                "release",
                "Plastic Dreams",
                "--label",
                "R & S Records",
                "--limit",
                "1",
            ],
        )
        assert result.exit_code == 0
        assert (
            "Next page: agent-discogs search release 'Plastic Dreams' "
            "--label 'R & S Records' --limit 1 --after 2:2.0"
        ) in result.output

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
        assert "Continue scan: agent-discogs search test --after 2:6.0" in result.output

    def test_search_with_type_in_next_page(self) -> None:
        result_item = _fake(
            id=1, type="artist", title="Test", year=None, label=None, format=None
        )
        self._set_fetch_result(
            PageResult(items=[result_item], page=1, total_items=20, total_pages=4)
        )
        result = CliRunner().invoke(cli, ["search", "artist", "test"])
        assert result.exit_code == 0
        assert "agent-discogs search artist" in result.output

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

    def test_release_type_unofficial_shows_only_unofficial(self) -> None:
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
        result = CliRunner().invoke(
            cli, ["search", "test", "--release-type", "unofficial"]
        )
        assert result.exit_code == 0
        assert "Bootleg" in result.output
        assert "Official" not in result.output

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

    def test_release_type_next_page_includes_flag(self) -> None:
        """Next page command includes --release-type when not default."""
        item = _fake(
            id=1,
            type="release",
            title="Boot",
            year=None,
            label=None,
            format=["Unofficial Release"],
        )
        self._set_fetch_result(
            PageResult(items=[item], page=1, total_items=50, total_pages=10)
        )
        result = CliRunner().invoke(
            cli, ["search", "test", "--release-type", "unofficial"]
        )
        assert result.exit_code == 0
        assert "--release-type unofficial" in result.output

    def test_search_filters_in_next_page(self) -> None:
        """Next page command includes all active filters."""
        item = _fake(
            id=1, type="release", title="Test", year=None, label=None, format=["Vinyl"]
        )
        self._set_fetch_result(
            PageResult(items=[item], page=1, total_items=50, total_pages=10)
        )
        result = CliRunner().invoke(
            cli,
            [
                "search",
                "test",
                "--artist",
                "Nine Inch Nails",
                "--genre",
                "Rock",
                "--year",
                "1994",
                "--country",
                "US",
                "--format",
                "Vinyl",
            ],
        )
        assert result.exit_code == 0
        assert "--artist 'Nine Inch Nails'" in result.output
        assert "--genre Rock" in result.output
        assert "--year 1994" in result.output
        assert "--country US" in result.output
        assert "--format Vinyl" in result.output
        assert "--after 2:6.0" in result.output

    def test_release_type_default_next_page_omits_flag(self) -> None:
        """Next page command omits --release-type when official (default)."""
        item = _fake(
            id=1, type="release", title="Test", year=None, label=None, format=["Vinyl"]
        )
        self._set_fetch_result(
            PageResult(items=[item], page=1, total_items=50, total_pages=10)
        )
        result = CliRunner().invoke(cli, ["search", "test"])
        assert result.exit_code == 0
        assert "--release-type" not in result.output

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
        fetch_params: dict[str, object] = {}

        def _capture_fetch(
            _client: object,
            _path: object,
            params: dict[str, object],
            *_rest: object,
            **_kw: object,
        ) -> PageResult:
            fetch_params.update(params)
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
        assert fetch_params["per_page"] == 5

    def test_filtered_search_continues_from_cursor(self) -> None:
        """--after resumes at the API page and offset encoded in the cursor."""
        seen: list[tuple[int, int]] = []

        def _mock_fetch(
            _client: object, _path: object, params: dict[str, object], *_a: object
        ) -> PageResult:
            page = int(str(params["page"]))
            seen.append((page, int(str(params["per_page"]))))
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
        assert "--limit 3 --after 3:1.6" in result.output

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
            position="A1", title="Mr. Self Destruct", duration="4:09", type_=None
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
        artist = _fake(id=3857, name="Nine Inch Nails")
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
        master = _fake(id=4917, title="The Downward Spiral")
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
            type_=None,
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
                    position="1", title="A", duration="4:30", type_=None, artists=None
                ),
                _fake(
                    position="2", title="B", duration="4:24", type_=None, artists=None
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
        artist = _fake(id=3857, name="Nine Inch Nails")
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
        artist = _fake(id=3857, name="Nine Inch Nails")
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

    def test_get_releases_role_next_page(self) -> None:
        """Next page command includes --role when active."""
        artist = _fake(id=3857, name="Nine Inch Nails")
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
        assert (
            "Next page: agent-discogs get releases @a3857 --role Main --after 2:6.0"
            in result.output
        )

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

    def test_get_versions_filters_in_next_page(self) -> None:
        """Next page command includes --country, --format, --label when active."""
        master = _fake(id=4917, title="TDS")
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
                items=[ver], page=1, total_items=50, total_pages=10
            ),
        )

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
        assert "--country US" in result.output
        assert "--format Vinyl" in result.output
        assert "--label Nothing" in result.output
        assert "--page 2" in result.output

    def test_get_versions_with_filters(self) -> None:
        master = _fake(id=4917, title="TDS")
        self._set_client(_fake_client(masters_get=lambda _id: master))
        self._monkeypatch.setattr(
            "agent_discogs.commands.get.fetch_page",
            lambda *_a, **_kw: PageResult(
                items=[], page=1, total_items=0, total_pages=1
            ),
        )

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

    def _capture_fetch(self) -> dict[str, object]:
        """Record the path/params the command sends; return an empty page."""
        seen: dict[str, object] = {}

        def _fetch(
            _client: object, path: str, params: dict[str, object], *_a: object
        ) -> PageResult:
            seen["path"] = path
            seen["params"] = dict(params)
            return PageResult(items=[], page=1, total_items=0, total_pages=1)

        self._monkeypatch.setattr("agent_discogs.commands.get.fetch_page", _fetch)
        self._monkeypatch.setattr("agent_discogs.pagination.fetch_page", _fetch)
        return seen

    def test_get_releases_for_a_label(self) -> None:
        label = _fake(id=647, name="Nothing Records")
        self._set_client(_fake_client(labels_get=lambda _id: label))
        rel = _fake_model(
            id=4401,
            title="Pretty Hate Machine",
            artist="Nine Inch Nails",
            year=0,
            catalog_number="0694903742",
            format="CD, Album",
        )
        seen: dict[str, object] = {}

        def _fetch(
            _client: object, path: str, params: dict[str, object], *_a: object
        ) -> PageResult:
            seen["path"] = path
            return PageResult(items=[rel], page=1, total_items=2718, total_pages=544)

        self._monkeypatch.setattr("agent_discogs.commands.get.fetch_page", _fetch)
        result = CliRunner().invoke(cli, ["get", "releases", "@l647"])
        assert result.exit_code == 0
        assert seen["path"] == "/labels/647/releases"
        assert 'Releases on @l647 "Nothing Records" (page 1, 1 of 2,718 results)' in (
            result.output
        )
        assert (
            '@r4401 "Pretty Hate Machine" by Nine Inch Nails · 0694903742 · CD, Album'
            in result.output
        )
        assert "Next page: agent-discogs get releases @l647 --page 2" in result.output

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

    def test_get_releases_sort_reaches_api_and_footer(self) -> None:
        artist = _fake(id=3857, name="NIN")
        self._set_client(_fake_client(artists_get=lambda _id: artist))
        seen = self._capture_fetch()
        result = CliRunner().invoke(
            cli, ["get", "releases", "@a3857", "--sort", "year", "--desc"]
        )
        assert result.exit_code == 0
        assert seen["params"] == {
            "sort": "year",
            "sort_order": "desc",
            "page": 1,
            "per_page": 5,
        }

        def _fetch(*_a: object, **_kw: object) -> PageResult:
            return PageResult(items=[], page=1, total_items=50, total_pages=10)

        self._monkeypatch.setattr("agent_discogs.commands.get.fetch_page", _fetch)
        result = CliRunner().invoke(
            cli, ["get", "releases", "@a3857", "--sort", "year", "--desc"]
        )
        assert (
            "Next page: agent-discogs get releases @a3857 --sort year --desc --page 2"
            in result.output
        )

    def test_get_releases_role_with_sort_passes_sort_to_scan(self) -> None:
        artist = _fake(id=3857, name="NIN")
        self._set_client(_fake_client(artists_get=lambda _id: artist))
        seen = self._capture_fetch()
        result = CliRunner().invoke(
            cli, ["get", "releases", "@a3857", "--role", "Main", "--sort", "title"]
        )
        assert result.exit_code == 0
        params = seen["params"]
        assert isinstance(params, dict)
        assert params["sort"] == "title"
        assert params["sort_order"] == "asc"
        assert params["per_page"] == 15  # client-side scan overfetches

    def test_get_versions_year_and_sort_reach_api(self) -> None:
        master = _fake(id=3719, title="TDS")
        self._set_client(_fake_client(masters_get=lambda _id: master))
        seen = self._capture_fetch()
        result = CliRunner().invoke(
            cli,
            ["get", "versions", "@m3719", "--year", "1994", "--sort", "released"],
        )
        assert result.exit_code == 0
        assert seen["path"] == "/masters/3719/versions"
        assert seen["params"] == {
            "page": 1,
            "per_page": 5,
            "released": "1994",
            "sort": "released",
            "sort_order": "asc",
        }

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
                "--sort/--desc apply to 'get releases' and 'get versions' only.",
            ),
            (["get", "releases", "@a3857", "--desc"], "--desc needs --sort <key>."),
            (
                ["get", "releases", "@a3857", "--year", "1994"],
                "--year filters versions and label catalogues",
            ),
            (
                ["get", "release", "@r847868", "--year", "1994"],
                "--year applies to 'get versions' and 'get releases @l...' only.",
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

    def test_get_releases_label_year_scans_client_side_with_cursor(self) -> None:
        label = _fake(id=647, name="Nothing Records")
        self._set_client(_fake_client(labels_get=lambda _id: label))
        seen: list[dict[str, object]] = []

        def _fetch(
            _client: object, path: str, params: dict[str, object], *_a: object
        ) -> PageResult:
            seen.append({"path": path, **params})
            api_page = int(str(params["page"]))
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
        assert seen == [{"path": "/labels/647/releases", "page": 1, "per_page": 6}]
        assert 'Releases on @l647 "Nothing Records" (page 1, 2 of ≤60 results)' in (
            result.output
        )
        assert '@r100 "T1-0" by A 1999 · CD' in result.output
        assert '@r103 "T1-3" by A 1999 · CD' in result.output
        assert "2001" not in result.output
        assert (
            "Next page: agent-discogs get releases @l647 --year 1999 --limit 2 "
            "--after 2:1.4"
        ) in result.output

        data = json.loads(
            CliRunner()
            .invoke(cli, ["get", "--json", "releases", "@l647", "--year", "1999"])
            .output
        )
        assert data["pagination"]["filtered"] is True
        assert all(r["year"] == 1999 for r in data["results"])


class TestShortcutCommands:
    def test_tracks_shortcut(self, monkeypatch: pytest.MonkeyPatch) -> None:
        track = _fake(position="1", title="Track One", duration="3:00", type_=None)
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
                tracklist=[_fake(position="1", title="A", duration="1:00", type_=None)],
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
                tracklist=[_fake(position="1", title="A", duration="1:00", type_=None)],
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

        def _raise() -> bool:
            raise RuntimeError("kaboom")

        monkeypatch.setattr("agent_discogs.commands.status.has_token", _raise)
        result = CliRunner().invoke(cli, ["status"])
        assert result.exit_code == 1
        assert "✗ Unexpected error: kaboom" in result.output

    def test_client_failure_is_inside_the_json_boundary(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _raise() -> None:
            raise RuntimeError("cache dir unwritable")

        monkeypatch.setattr("agent_discogs.commands.get.get_client", _raise)
        result = CliRunner().invoke(cli, ["get", "--json", "artist", "@a1"])
        assert result.exit_code == 1
        assert json.loads(result.output)["error"] == {
            "code": "unexpected",
            "message": "Unexpected error: cache dir unwritable",
        }

        monkeypatch.setattr("agent_discogs.commands.search.get_client", _raise)
        result = CliRunner().invoke(cli, ["search", "--json", "x"])
        assert json.loads(result.output)["error"]["code"] == "unexpected"


def _fake_model(**kwargs: object) -> SimpleNamespace:
    """Fake SDK model with model_dump() support."""
    ns = SimpleNamespace(**kwargs)
    ns.model_dump = lambda: dict(kwargs)
    return ns


def _via_json(data: object) -> object:
    """What a raw `--full` dump of a fake looks like after `default=str`."""
    return json.loads(json.dumps(data, default=str))


class TestJsonSearch:
    @pytest.fixture(autouse=True)
    def _patch_search(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._monkeypatch = monkeypatch
        monkeypatch.setattr("agent_discogs.commands.search.get_client", lambda: None)

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
                "slow", status_code=429, response_body={}, retry_after="12"
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
                    type_=None,
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
                _fake(position="1", title="A", duration="", type_=None, artists=None)
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
        artist = _fake(id=3857, name="NIN")
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
        master = _fake(id=3719, title="TDS")
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
            type_="track",
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
        monkeypatch.setattr("agent_discogs.commands.search.get_client", lambda: None)
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


def _tracks_client(
    recorder: Callable[[], None], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`tracks @r123` served by a fake whose lookup also runs `recorder`, the
    way the real client's `on_request` hook fires inside the SDK call."""
    track = _fake_model(position="1", title="Song", duration="3:00", type_=None)
    release = _fake(id=123, title="Album", year=2020, artists=None, tracklist=[track])

    def _get(_id: int) -> object:
        recorder()
        return release

    monkeypatch.setattr(
        "agent_discogs.commands.get.get_client",
        lambda: _fake_client(releases_get=_get),
    )


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
        self.log: list[str] = []
        scoped = SimpleNamespace(
            cache_only=_CacheScope(self.log, "cache_only"),
            no_cache=_CacheScope(self.log, "no_cache"),
        )
        monkeypatch.setattr("agent_discogs.client.get_client", lambda: scoped)
        _tracks_client(lambda: self.log.append("request"), monkeypatch)

    def test_both_flags_is_a_usage_error(self) -> None:
        result = CliRunner().invoke(cli, ["--cached", "--fresh", "status"])
        assert result.exit_code == 2
        assert "--cached and --fresh are mutually exclusive." in result.stderr
        assert self.log == []

    def test_cached_wraps_the_whole_command(self) -> None:
        result = CliRunner().invoke(cli, ["--cached", "tracks", "@r123"])
        assert result.exit_code == 0
        assert self.log == ["enter cache_only", "request", "exit cache_only"]

    def test_fresh_wraps_the_whole_command(self) -> None:
        result = CliRunner().invoke(cli, ["--fresh", "tracks", "@r123"])
        assert result.exit_code == 0
        assert self.log == ["enter no_cache", "request", "exit no_cache"]

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

        monkeypatch.setattr(
            "agent_discogs.commands.get.get_client",
            lambda: _fake_client(releases_get=_get),
        )
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

    def test_first_request_miss_still_reports_zero_spend(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The SDK emits no event for a `CacheMissError`, so a run whose very
        first lookup misses has an empty trace; the footer must still say so."""

        def _miss(release_id: int) -> object:
            raise CacheMissError(
                "GET", f"https://api.discogs.com/releases/{release_id}?curr_abbr=USD"
            )

        monkeypatch.setattr(
            "agent_discogs.commands.get.get_client",
            lambda: _fake_client(releases_get=_miss),
        )
        result = CliRunner().invoke(cli, ["--cached", "price", "@r847868"])
        assert result.exit_code == 1
        assert result.stdout == ""
        assert result.stderr == (
            "✗ Not cached: GET /releases/847868?curr_abbr=USD\n"
            "  Rerun without --cached to fetch it from the API.\n"
            "api: 0 requests · cached\n"
        )

        result = CliRunner().invoke(cli, ["--cached", "price", "--json", "@r847868"])
        assert result.exit_code == 1
        assert json.loads(result.stdout)["error"]["code"] == "cache_miss"
        assert result.stderr == "api: 0 requests · cached\n"


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
