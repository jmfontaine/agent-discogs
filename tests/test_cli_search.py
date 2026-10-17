"""Tests for the `search` command: request parameters, text output, sort,
the unofficial release type, and `--added-since`."""

from __future__ import annotations

import json
import re
import shlex
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from click.testing import CliRunner, Result
from discogs_sdk import (
    SearchResult,
)

from agent_discogs import cli
from agent_discogs.pagination import PageResult
from tests.conftest import (
    _ROW,
    ServePage,
    UseClient,
    _Catalogue,
    _empty_listing,
    _FakeAPI,
    fake_client,
    json_error,
    text_error,
)


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
            ("--credit", "credit"),
            ("--track", "track"),
            ("--title", "release_title"),
            ("--anv", "anv"),
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

    def test_new_filters_with_the_existing_ones(self) -> None:
        sent = self._sent(
            "release",
            *("--artist", "Horace Silver", "--format", "Vinyl", "--year", "1956"),
            *("--credit", "Rudy Van Gelder", "--track", "Senor Blues"),
            *("--title", "Six Pieces of Silver"),
        )
        assert sent == {
            "type": "release",
            "artist": "Horace Silver",
            "format": "Vinyl",
            "year": "1956",
            "release_title": "Six Pieces of Silver",
            "credit": "Rudy Van Gelder",
            "track": "Senor Blues",
            "page": "1",
            "per_page": "5",
        }

    @pytest.mark.parametrize(
        ("args", "param"),
        [
            (["--anv", "Miles"], "anv"),
            (["artist", "--anv", "Miles"], "anv"),
            (["artist", "--title", "Kind of Blue"], "release_title"),
            (["label", "--title", "Kind of Blue"], "release_title"),
            (["--credit", "Rudy Van Gelder"], "credit"),
        ],
    )
    def test_accepted_without_a_query(self, args: list[str], param: str) -> None:
        kind = {"type": args[0]} if not args[0].startswith("-") else {}
        assert self._sent(*args) == {
            **kind,
            param: args[-1],
            "page": "1",
            "per_page": "5",
        }

    def test_blank_new_filter_is_dropped(self) -> None:
        assert self._sent("Spiral", "--credit", " ", "--title", "") == {
            "q": "Spiral",
            "page": "1",
            "per_page": "5",
        }

    @pytest.mark.parametrize(
        ("args", "message"),
        [
            (
                ["artist", "--credit", "x"],
                (
                    "--credit matches the credits of releases and masters; an artist "
                    "search has none. Search releases: agent-discogs search release "
                    '--credit "..."'
                ),
            ),
            (
                ["label", "--track", "x"],
                (
                    "--track matches the track titles of releases and masters; a "
                    "label search has none. Search releases: agent-discogs search "
                    'release --track "..."'
                ),
            ),
            (
                ["release", "--anv", "x"],
                (
                    "--anv matches artist name variations; a release search has "
                    'none. Search artists: agent-discogs search artist --anv "..."'
                ),
            ),
            (
                ["master", "--anv", "x"],
                (
                    "--anv matches artist name variations; a master search has "
                    'none. Search artists: agent-discogs search artist --anv "..."'
                ),
            ),
            (
                ["label", "--anv", "x"],
                (
                    "--anv matches artist name variations; a label search has "
                    'none. Search artists: agent-discogs search artist --anv "..."'
                ),
            ),
        ],
    )
    def test_filter_on_a_type_it_cannot_match_is_rejected(
        self, args: list[str], message: str
    ) -> None:
        result = CliRunner().invoke(cli, ["search", *args])
        assert text_error(result) == f"✗ {message}\n"
        error = json_error(CliRunner().invoke(cli, ["search", *args, "--json"]))
        assert (error["code"], error["message"]) == ("invalid_argument", message)
        assert self.api.sends == []


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

    def test_header_echoes_the_new_filters_by_their_flag_names(self) -> None:
        item = SearchResult(id=1, type="release", title="Six Pieces", format=["LP"])
        self._serve_page(PageResult(items=[item], page=1, total_items=1, total_pages=1))
        result = CliRunner().invoke(
            cli,
            [
                "search",
                "release",
                *("--credit", "Rudy Van Gelder", "--artist", "Horace Silver"),
                *("--title", "Six Pieces of Silver"),
            ],
        )
        assert result.exit_code == 0, result.output
        assert result.stdout.splitlines()[0] == (
            'Search: release artist="Horace Silver" title="Six Pieces of Silver" '
            'credit="Rudy Van Gelder" (page 1, 1 of ≤1 results)'
        )

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


_FOOTER = re.compile(r"^(?:Next page|Continue scan): (.+)$", re.MULTILINE)

_TRICKY = 'Rudy Van "Gelder" $HOME'


class TestNewFilterContinuation:
    """The printed continuation repeats `--title`, `--credit`, `--track` and
    `--anv` by their flag names, quoted so they paste back unchanged."""

    @pytest.mark.parametrize(
        ("release_type", "continues_with"),
        [(["--release-type", "all"], "--page"), ([], "--after")],
        ids=["server-side", "filtered"],
    )
    def test_continuation_repeats_every_new_filter(
        self,
        monkeypatch: pytest.MonkeyPatch,
        release_type: list[str],
        continues_with: str,
    ) -> None:
        api = _FakeAPI(monkeypatch, _Catalogue())
        argv = [
            "search",
            *("--title", _TRICKY, "--credit", _TRICKY),
            *("--track", _TRICKY, "--anv", _TRICKY),
            *release_type,
            *("--limit", "2", "--", "Spiral"),
        ]
        first = CliRunner().invoke(cli, argv)
        assert first.exit_code == 0, first.output
        match = _FOOTER.search(first.stdout)
        assert match, first.output
        emitted = shlex.split(match.group(1))
        assert "--release-title" not in emitted
        for flag in ("--title", "--credit", "--track", "--anv"):
            assert emitted[emitted.index(flag) + 1] == _TRICKY
        assert continues_with in emitted

        second = CliRunner().invoke(cli, emitted[1:])
        assert second.exit_code == 0, second.output
        sent = [params for path, params in api.sends if path == "/database/search"]
        assert len({json.dumps({**p, "page": None}, sort_keys=True) for p in sent}) == 1
        assert {"release_title", "credit", "track", "anv"} <= sent[-1].keys()
        assert sent[-1]["release_title"] == _TRICKY


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

    def test_a_capped_rerun_continues_past_the_cached_lookups(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._api(monkeypatch)
        argv = [*_ADDED, "--release-type", "all", "--max-requests", "3", "--json"]
        first, sent = self._run(*argv)
        assert len(sent) == 3
        assert json.loads(first.stdout)["total"] == 2
        # The search page and the lookups already made are cache hits, so the
        # cap goes on lookups the first run did not reach.
        second, sent = self._run(*argv)
        assert len(sent) == 3
        assert json.loads(second.stdout)["total"] == 5

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

    def test_new_filters_reach_the_date_sorted_search(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._api(monkeypatch)
        argv = [*_ADDED, "--credit", "Rudy Van Gelder", "--title", "Blue", "--json"]
        result, _ = self._run(*argv)
        assert result.exit_code == 0, result.output
        search = next(p for path, p in self.api.sends if path == "/database/search")
        assert (search["credit"], search["release_title"]) == (
            "Rudy Van Gelder",
            "Blue",
        )
        assert (search["sort"], search["sort_order"]) == ("date_added", "desc")
        assert "title" not in search
