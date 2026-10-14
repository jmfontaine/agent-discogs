"""Tests for paging: `--limit` / `--page` bounds, continuation commands,
and client-side filtered scans with `--max-requests`."""

from __future__ import annotations

import json
import re
import shlex
from typing import Any

import httpx2
import pytest
from click.testing import CliRunner, Result

from agent_discogs import cli
from tests.conftest import (
    _LISTING_KEYS,
    _ROW,
    _Catalogue,
    _empty_listing,
    _FakeAPI,
)


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


_FOOTER = re.compile(r"^(?:Next page|Continue scan): (.+)$", re.MULTILINE)


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
    "versions, --release-type official": (
        "get versions @m1 --release-type official --format LP --limit 2",
        2,
        _FILTERED_PAGES,
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


# 250 versions of a master, 30 of them tagged "Unofficial Release" (ten on
# each 100-row API page) and 5 "Partially Unofficial", all on the first.
_UNOFFICIAL = {*range(0, 100, 10), *range(100, 200, 10), *range(200, 250, 5)}
_PARTIAL = {1, 2, 3, 4, 5}
_OFFICIAL = [i for i in range(250) if i not in _UNOFFICIAL]


def _version_format(i: int) -> str:
    if i in _UNOFFICIAL:
        return "CD, Album, Unofficial Release"
    if i in _PARTIAL:
        return "CD, Album, Partially Unofficial"
    return "Vinyl, LP, Album"


def _versions(path: str, params: dict[str, str]) -> object:
    """The master's versions; like Discogs, `format` keeps the rows that list
    it as a whole description."""
    if not path.endswith("/versions"):
        return {"id": 1, "title": "Master Title"}
    ids = list(range(250))
    if "format" in params:
        ids = [i for i in ids if params["format"] in _version_format(i).split(", ")]
    rows = [
        {"id": i, "title": "Album", "label": f"Label{i}", "format": _version_format(i)}
        for i in ids
    ]
    page, per = int(params["page"]), int(params["per_page"])
    pagination = {
        "page": page,
        "pages": max(1, -(-len(rows) // per)),
        "items": len(rows),
        "per_page": per,
    }
    return {"pagination": pagination, "versions": rows[(page - 1) * per : page * per]}


_VERSIONS = "/masters/1/versions"
_VERSION_ROW = re.compile(r"^@r(\d+) ", re.MULTILINE)


class TestVersionsReleaseType:
    """`get versions --release-type`: `unofficial` is filtered by Discogs,
    `official` (and `unofficial` with `--format`) is a client-side scan on the
    whole "Unofficial Release" description, `all` is today's listing."""

    @pytest.fixture(autouse=True)
    def _fake_api(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.api = _FakeAPI(monkeypatch, _versions)

    def _run(self, *args: str) -> tuple[Result, list[tuple[str, dict[str, str]]]]:
        start = len(self.api.sends)
        result = CliRunner().invoke(cli, ["get", "versions", "@m1", *args])
        return result, self.api.sends[start:]

    @staticmethod
    def _document(result: Result) -> tuple[list[int], dict[str, Any]]:
        assert result.exit_code == 0, result.output
        document = json.loads(result.stdout)
        ids = [int(row["ref"].removeprefix("@r")) for row in document["results"]]
        return ids, document["pagination"]

    def test_unofficial_is_filtered_by_discogs(self) -> None:
        result, sent = self._run("--release-type", "unofficial", "--limit", "100")
        assert result.exit_code == 0, result.output
        # One listing request, then the title for the header, as without the
        # flag.
        assert sent == [
            (
                _VERSIONS,
                {"format": "Unofficial Release", "page": "1", "per_page": "100"},
            ),
            ("/masters/1", {}),
        ]
        assert "(page 1, 30 of 30 versions)" in result.stdout.splitlines()[0]
        assert sorted(map(int, _VERSION_ROW.findall(result.stdout))) == sorted(
            _UNOFFICIAL
        )

        # Server-side pages: --page works, and the next page is a --page.
        result, _ = self._run("--release-type", "unofficial", "--page", "2", "--json")
        ids, pagination = self._document(result)
        assert ids == sorted(_UNOFFICIAL)[5:10]
        assert "filtered" not in pagination

    def test_unofficial_with_format_scans(self) -> None:
        argv = ["--release-type", "unofficial", "--format", "Album", "--limit", "100"]
        result, sent = self._run(*argv, "--json")
        ids, pagination = self._document(result)
        assert ids == sorted(_UNOFFICIAL)
        assert not _PARTIAL & set(ids)
        assert [(path, p["page"], p["format"]) for path, p in sent] == [
            (_VERSIONS, str(page), "Album") for page in (1, 2, 3)
        ]
        assert pagination["filtered"] is True
        assert pagination["total_items"] == 250

    def test_official_keeps_partially_unofficial(self) -> None:
        argv = ["--release-type", "official", "--limit", "100", "--json"]
        result, sent = self._run(*argv)
        ids, pagination = self._document(result)
        assert ids == _OFFICIAL[:100]
        assert set(ids) >= _PARTIAL
        assert sent == [
            (_VERSIONS, {"page": str(page), "per_page": "100"}) for page in (1, 2)
        ]
        assert pagination["capped"] is False
        assert pagination["next_cursor"] == "2:112"

    def test_capped_scan_reports_its_stop(self) -> None:
        argv = ["--release-type", "official", "--limit", "100"]
        result, sent = self._run(*argv, "--max-requests", "1", "--json")
        ids, pagination = self._document(result)
        assert ids == _OFFICIAL[:90]
        assert [p["page"] for _, p in sent] == ["1"]
        assert pagination["capped"] is True
        assert pagination["stop_reason"] == "max_requests"
        assert pagination["next_cursor"] == "2:100"

    def test_capped_scan_continues_without_repeating_a_row(self) -> None:
        argv = ["--release-type", "official", "--limit", "100"]
        # The title, then the first page; the title is cached after.
        first, sent = self._run(*argv, "--max-requests", "2")
        assert first.exit_code == 0, first.output
        assert [(path, p.get("page")) for path, p in sent] == [
            ("/masters/1", None),
            (_VERSIONS, "1"),
        ]
        assert "scan stopped at --max-requests 2" in first.stdout.splitlines()[0]
        assert "≤250 versions" in first.stdout.splitlines()[0]
        continuation = _continuation(first)
        assert "\nContinue scan: " in first.stdout
        assert continuation[:3] == ["get", "versions", "@m1"]
        assert continuation[continuation.index("--release-type") + 1] == "official"
        assert continuation[continuation.index("--max-requests") + 1] == "2"
        assert continuation[continuation.index("--after") + 1] == "2:100"

        start = len(self.api.sends)
        second = CliRunner().invoke(cli, continuation)
        assert second.exit_code == 0, second.output
        assert [p["page"] for _, p in self.api.sends[start:]] == ["2", "3"]
        shown = [
            [int(i) for i in _VERSION_ROW.findall(r.stdout)] for r in (first, second)
        ]
        assert shown == [_OFFICIAL[:90], _OFFICIAL[90:190]]
        assert "\nNext page: " in second.stdout

    def test_text_cap_spent_on_the_title(self) -> None:
        result, sent = self._run("--release-type", "official", "--max-requests", "1")
        assert result.exit_code == 0, result.output
        assert sent == [("/masters/1", {})]
        header = result.stdout.splitlines()[0]
        assert header == (
            'Versions of @m1 "Master Title" '
            "(--max-requests 1 ran out before the scan started)"
        )
        continuation = _continuation(result)
        assert continuation[continuation.index("--after") + 1] == "1:0"

    @pytest.mark.parametrize(
        "args",
        [
            ["--release-type", "official", "--page", "2"],
            ["--release-type", "unofficial", "--format", "CD", "--page", "2"],
            ["--release-type", "unofficial", "--after", "2:0"],
            ["--release-type", "all", "--after", "2:0"],
            ["--after", "2:0"],
            ["--release-type", "unofficial", "--max-requests", "3"],
            ["--max-requests", "3"],
        ],
    )
    def test_rejected_before_any_request(self, args: list[str]) -> None:
        result, sent = self._run(*args, "--json")
        assert result.exit_code == 1
        assert json.loads(result.stdout)["error"]["code"] == "invalid_argument"
        assert sent == []

    def test_release_type_belongs_to_versions(self) -> None:
        result = CliRunner().invoke(
            cli, ["get", "releases", "@a1", "--release-type", "official"]
        )
        assert result.exit_code == 1
        assert "--release-type does not apply to 'get releases'" in result.stderr
        assert self.api.sends == []

    @pytest.mark.parametrize("args", [["--json"], []], ids=["json", "text"])
    def test_all_is_the_default(
        self, monkeypatch: pytest.MonkeyPatch, args: list[str]
    ) -> None:
        default, default_sent = self._run(*args)
        assert default.exit_code == 0, default.output
        self.api = _FakeAPI(monkeypatch, _versions)  # a cold cache again
        explicit, explicit_sent = self._run("--release-type", "all", *args)
        assert explicit.stdout == default.stdout
        assert explicit_sent == default_sent
        assert (_VERSIONS, {"page": "1", "per_page": "5"}) in default_sent
        if not args:
            assert "\nNext page: agent-discogs get versions @m1 --page 2" in (
                default.stdout
            )
