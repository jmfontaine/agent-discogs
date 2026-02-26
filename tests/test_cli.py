"""Tests for CLI argument parsing and dispatch."""

from __future__ import annotations

import importlib
import importlib.metadata
import json
from types import SimpleNamespace

import click
import pytest
from click.testing import CliRunner

from agent_discogs import cli
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
        assert "search" in result.output
        assert "get" in result.output
        assert "status" in result.output
        assert "tracks" in result.output
        assert "price" in result.output

    def test_no_args_shows_help(self) -> None:
        result = CliRunner().invoke(cli, [])
        assert result.exit_code == 0
        assert "Usage:" in result.output or "search" in result.output

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
        from agent_discogs import main

        result = CliRunner().invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert main is not None


class TestStatusCommand:
    def test_status(self) -> None:
        result = CliRunner().invoke(cli, ["status"])
        assert result.exit_code == 0
        assert "agent-discogs v0.1.0" in result.output
        assert "Auth:" in result.output
        assert "Cache:" in result.output


class TestAliases:
    def test_find_alias(self) -> None:
        result = CliRunner().invoke(cli, ["find", "--help"])
        assert result.exit_code == 0
        assert "--year" in result.output

    def test_fetch_alias(self) -> None:
        result = CliRunner().invoke(cli, ["fetch", "--help"])
        assert result.exit_code == 0
        assert "release" in result.output

    def test_query_alias(self) -> None:
        result = CliRunner().invoke(cli, ["query", "--help"])
        assert result.exit_code == 0
        assert "--year" in result.output

    def test_show_alias(self) -> None:
        result = CliRunner().invoke(cli, ["show", "--help"])
        assert result.exit_code == 0
        assert "release" in result.output


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

    def test_search_filter_only(self) -> None:
        """Filter-only search without query text (e.g. --catno)."""
        self._set_fetch_result(
            PageResult(items=[], page=1, total_items=0, total_pages=1)
        )
        result = CliRunner().invoke(cli, ["search", "release", "--catno", "INT-92346"])
        assert result.exit_code == 0

    def test_search_error(self) -> None:
        def _raise(*_a: object, **_kw: object) -> None:
            raise Exception("connection failed")

        self._monkeypatch.setattr("agent_discogs.pagination.fetch_page", _raise)
        result = CliRunner().invoke(cli, ["search", "test query"])
        assert result.exit_code == 1
        assert "error" in result.output.lower()

    def test_search_pagination_next(self) -> None:
        result_item = _fake(
            id=1, type="release", title="Test", year=None, label=None, format=None
        )
        self._set_fetch_result(
            PageResult(items=[result_item], page=1, total_items=50, total_pages=10)
        )
        result = CliRunner().invoke(cli, ["search", "test"])
        assert result.exit_code == 0
        assert "Next page:" in result.output
        assert "--page 2" in result.output

    def test_search_pagination_no_next(self) -> None:
        self._set_fetch_result(
            PageResult(items=[], page=3, total_items=15, total_pages=3)
        )
        result = CliRunner().invoke(cli, ["search", "test", "--page", "3"])
        assert result.exit_code == 0
        assert "Next page:" not in result.output

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

    def test_filtered_search_user_page_2(self) -> None:
        """Filtered search with --page 2 skips items from page 1."""
        call_count = 0

        def _mock_fetch(*_a: object, **_kw: object) -> PageResult:
            nonlocal call_count
            call_count += 1
            items = [
                _fake(
                    id=call_count * 10 + i,
                    type="release",
                    title=f"Official {call_count}-{i}",
                    year=None,
                    label=None,
                    format=["Vinyl"],
                )
                for i in range(3)
            ]
            return PageResult(
                items=items,
                page=call_count,
                total_items=30,
                total_pages=10,
            )

        self._monkeypatch.setattr("agent_discogs.pagination.fetch_page", _mock_fetch)
        result = CliRunner().invoke(
            cli, ["search", "test", "--limit", "3", "--page", "2"]
        )
        assert result.exit_code == 0
        # Page 2 means skip first 3 items, show next 3
        assert "Official 2" in result.output

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
        price = _fake(value=100.00)
        release = _fake(
            id=367113,
            title="TDS",
            year=1994,
            artists=None,
            price_suggestions=_fake(get=lambda: _fake(conditions={"Mint (M)": price})),
            marketplace_stats=_fake(
                get=lambda: _fake(num_for_sale=100, lowest_price=_fake(value=5.00))
            ),
        )
        self._set_client(_fake_client(releases_get=lambda _id: release))
        result = CliRunner().invoke(cli, ["get", "price", "@r367113"])
        assert result.exit_code == 0
        assert "Price Guide:" in result.output
        assert "$100.00" in result.output

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
            catno="INT-92346",
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
            catno="C",
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
            raise Exception("API down")

        self._set_client(_fake_client(artists_get=_raise))
        result = CliRunner().invoke(cli, ["get", "artist", "@a1"])
        assert result.exit_code == 1
        assert "error" in result.output.lower()

    def test_get_release_verbose(self) -> None:
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
        result = CliRunner().invoke(cli, ["get", "release", "@r367113", "--verbose"])
        assert result.exit_code == 0
        assert "Notes: Pressed at Sterling Sound." in result.output

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
        assert "--role Main" in result.output
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


class TestResolveCommand:
    def test_resolve_none_command(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """resolve_command returns None tuple when super returns None cmd."""
        from agent_discogs import AliasGroup

        group = AliasGroup(name="test")
        ctx = click.Context(group)
        monkeypatch.setattr(
            click.Group,
            "resolve_command",
            lambda _self, _ctx, _args: (None, None, ["arg"]),
        )
        name, cmd, remaining = group.resolve_command(ctx, ["arg"])
        assert name is None
        assert cmd is None
        assert remaining == ["arg"]


class TestMainEntry:
    def test_main_invokes_cli(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """main() delegates to cli()."""
        from agent_discogs import main

        calls: list[object] = []
        monkeypatch.setattr("agent_discogs.cli", lambda *a, **kw: calls.append(1))
        main()
        assert len(calls) == 1

    def test_dunder_main(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """__main__.py runs main()."""
        calls: list[object] = []
        monkeypatch.setattr("agent_discogs.main", lambda: calls.append(1))

        import agent_discogs.__main__ as dunder_main

        importlib.reload(dunder_main)
        assert len(calls) >= 1


class TestExceptionHandling:
    def test_abort_propagates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _raise_abort(
            *_a: object,
            **_kw: object,
        ) -> None:
            raise click.exceptions.Abort()

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
        """Exception bubbling through AliasGroup.invoke -> format_error."""

        def _raise() -> None:
            raise RuntimeError("kaboom")

        monkeypatch.setattr(
            "agent_discogs.commands.get.get_client",
            _raise,
        )
        result = CliRunner().invoke(cli, ["get", "artist", "@a1"])
        assert result.exit_code == 1
        assert "error" in result.output.lower()


def _fake_model(**kwargs: object) -> SimpleNamespace:
    """Fake SDK model with model_dump() support."""
    ns = SimpleNamespace(**kwargs)
    ns.model_dump = lambda: dict(kwargs)
    return ns


class TestJsonSearch:
    @pytest.fixture(autouse=True)
    def _patch_search(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._monkeypatch = monkeypatch
        monkeypatch.setattr("agent_discogs.commands.search.get_client", lambda: None)

    def test_search_json(self) -> None:
        item = _fake_model(id=367113, type="release", title="TDS", format=None)

        def mock(*_a: object, **_kw: object) -> PageResult:
            return PageResult(items=[item], page=1, total_items=1, total_pages=1)

        self._monkeypatch.setattr("agent_discogs.commands.search.fetch_page", mock)
        self._monkeypatch.setattr("agent_discogs.pagination.fetch_page", mock)
        result = CliRunner().invoke(cli, ["search", "--json", "test"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["pagination"]["page"] == 1
        assert data["pagination"]["total_items"] == 1
        assert len(data["results"]) == 1
        assert data["results"][0]["id"] == 367113

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

    def test_get_release_json(self) -> None:
        release = _fake_model(id=367113, title="TDS", year=1994)
        self._set_client(_fake_client(releases_get=lambda _id: release))
        result = CliRunner().invoke(cli, ["get", "--json", "release", "@r367113"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["id"] == 367113
        assert data["title"] == "TDS"

    def test_get_artist_json(self) -> None:
        artist = _fake_model(id=3857, name="NIN")
        self._set_client(_fake_client(artists_get=lambda _id: artist))
        result = CliRunner().invoke(cli, ["get", "--json", "artist", "@a3857"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["id"] == 3857
        assert data["name"] == "NIN"

    def test_get_label_json(self) -> None:
        label = _fake_model(id=2919, name="Nothing Records")
        self._set_client(_fake_client(labels_get=lambda _id: label))
        result = CliRunner().invoke(cli, ["get", "--json", "label", "@l2919"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["name"] == "Nothing Records"

    def test_get_master_json(self) -> None:
        master = _fake_model(id=4917, title="TDS", year=1994)
        self._set_client(_fake_client(masters_get=lambda _id: master))
        result = CliRunner().invoke(cli, ["get", "--json", "master", "@m4917"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["id"] == 4917

    def test_get_releases_json(self) -> None:
        artist = _fake(id=3857, name="NIN")
        self._set_client(_fake_client(artists_get=lambda _id: artist))
        rel = _fake_model(id=100, type="master", title="X", year=2000)
        self._monkeypatch.setattr(
            "agent_discogs.commands.get.fetch_page",
            lambda *_a, **_kw: PageResult(
                items=[rel], page=1, total_items=1, total_pages=1
            ),
        )
        result = CliRunner().invoke(cli, ["get", "--json", "releases", "@a3857"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["pagination"]["page"] == 1
        assert len(data["results"]) == 1

    def test_get_versions_json(self) -> None:
        master = _fake(id=4917, title="TDS")
        self._set_client(_fake_client(masters_get=lambda _id: master))
        ver = _fake_model(id=367113, released="1994", country="US")
        self._monkeypatch.setattr(
            "agent_discogs.commands.get.fetch_page",
            lambda *_a, **_kw: PageResult(
                items=[ver], page=1, total_items=1, total_pages=1
            ),
        )
        result = CliRunner().invoke(cli, ["get", "--json", "versions", "@m4917"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["pagination"]["page"] == 1
        assert data["results"][0]["id"] == 367113

    def test_get_tracklist_json(self) -> None:
        track = _fake_model(position="A1", title="Track One", duration="4:00")
        release = _fake(id=123, tracklist=[track])
        self._set_client(_fake_client(releases_get=lambda _id: release))
        result = CliRunner().invoke(cli, ["get", "--json", "tracklist", "@r123"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data["tracklist"], list)
        assert data["tracklist"][0]["title"] == "Track One"

    def test_get_price_json(self) -> None:
        price_suggestions = _fake_model(conditions={"Mint (M)": {"value": 100.0}})
        marketplace_stats = _fake_model(num_for_sale=50, lowest_price=5.0)
        release = _fake(
            id=123,
            price_suggestions=_fake(get=lambda: price_suggestions),
            marketplace_stats=_fake(get=lambda: marketplace_stats),
        )
        self._set_client(_fake_client(releases_get=lambda _id: release))
        result = CliRunner().invoke(cli, ["get", "--json", "price", "@r123"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "conditions" in data
        assert "marketplace_stats" in data


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
        price_suggestions = _fake_model(conditions={"Mint (M)": {"value": 50.0}})
        marketplace_stats = _fake_model(num_for_sale=10, lowest_price=None)
        release = _fake(
            id=123,
            price_suggestions=_fake(get=lambda: price_suggestions),
            marketplace_stats=_fake(get=lambda: marketplace_stats),
        )
        monkeypatch.setattr(
            "agent_discogs.commands.get.get_client",
            lambda: _fake_client(releases_get=lambda _id: release),
        )
        result = CliRunner().invoke(cli, ["price", "--json", "@r123"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "conditions" in data
