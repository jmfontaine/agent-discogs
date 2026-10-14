"""Tests for `get collection`, against the real SDK client over a mock
transport."""

from __future__ import annotations

import json
import shlex
from typing import Any

import httpx2
import pytest
from click.testing import CliRunner, Result

from agent_discogs import cli
from tests.conftest import _FakeAPI, json_error, text_error

_USER = "/users/me"
_RECORDS = f"{_USER}/collection/folders/0/releases"
_FOLDER_LIST = f"{_USER}/collection/folders"
_SEARCH = "/database/search"
# Folder 5 is "Vinyl", 1 is "Uncategorized"; 0 ("All") is never a record's.
_FOLDERS = [
    {"id": 0, "name": "All", "count": 250},
    {"id": 1, "name": "Uncategorized", "count": 249},
    {"id": 5, "name": "Vinyl", "count": 1},
]
# The Techno records: 12 of 250, on each of the three 100-record pages.
_TECHNO = (5, 17, 40, 99, 100, 130, 160, 199, 200, 210, 230, 249)


def _too_many() -> httpx2.Response:
    return httpx2.Response(429, json={}, headers={"Retry-After": "30"})


class _Collection:
    """The token's user `me` owns `size` records, in collection order. Record
    `n` is release `1000 + n`, `Record <n>` by Artist on Label (`CAT<n>`), a
    House LP from 2001 in Uncategorized, added on 2024-01-(n % 28 + 1), except
    that the `_TECHNO` records are Techno 12" singles from 1992 on Tresor,
    record 3 is release 7, rated 4, in Vinyl, record 120 is release 7 again,
    in Uncategorized, and `odd` replaces records by number. `throttle`
    answers with a 429: `"identity"` the username, `"folders"` the folder
    list, `"page:N"` collection page N. A search lists releases 1 and 2."""

    def __init__(
        self,
        size: int = 250,
        *,
        throttle: tuple[str, ...] = (),
        odd: dict[int, dict[str, Any]] | None = None,
    ) -> None:
        self.size, self.throttle, self.odd = size, throttle, odd or {}

    def __call__(self, path: str, params: dict[str, str]) -> object:
        if path == "/oauth/identity":
            if "identity" in self.throttle:
                return _too_many()
            return {"id": 7, "username": "me"}
        if path == _FOLDER_LIST:
            if "folders" in self.throttle:
                return _too_many()
            return {"folders": _FOLDERS}
        if path == _RECORDS:
            page = int(params["page"])
            if f"page:{page}" in self.throttle:
                return _too_many()
            return self._page(page, int(params["per_page"]))
        if path == _SEARCH:
            rows = [{"id": i, "title": f"Title {i}", "type": "release"} for i in (1, 2)]
            pages = {"page": 1, "pages": 1, "items": 2, "per_page": 100}
            return {"pagination": pages, "results": rows}
        raise AssertionError(path)

    def _page(self, page: int, per: int) -> object:
        items = [
            self.odd.get(n) or self.record(n)
            for n in range((page - 1) * per, min(page * per, self.size))
        ]
        pages = {"page": page, "pages": -(-self.size // per), "items": self.size}
        return {"pagination": {**pages, "per_page": per}, "releases": items}

    @staticmethod
    def record(n: int) -> dict[str, Any]:
        techno = n in _TECHNO
        release = {3: 7, 120: 7}.get(n, 1000 + n)
        return {
            "id": release,
            "instance_id": 10_000 + n,
            "folder_id": 5 if n == 3 else 1,
            "rating": 4 if n == 3 else 0,
            "date_added": f"2024-01-{n % 28 + 1:02d}T10:00:00-08:00",
            "basic_information": {
                "id": release,
                "title": f"Record {n}",
                "year": 1992 if techno else 2001,
                "artists": [{"name": "Artist", "join": ""}],
                "labels": [
                    {"name": "Tresor" if techno else "Label", "catno": f"CAT{n}"}
                ],
                "formats": [
                    {
                        "name": "Vinyl",
                        "qty": "1",
                        "descriptions": ['12"', "33 ⅓ RPM"] if techno else ["LP"],
                    }
                ],
                "genres": ["Electronic"],
                "styles": ["Techno"] if techno else ["House"],
            },
        }


class _Harness:
    """`_FakeAPI` over `collection`. With `token`, the environment holds the
    credentials of a user, as `get collection` needs."""

    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        collection: _Collection | None = None,
        *,
        token: bool = True,
    ) -> None:
        if token:
            monkeypatch.setenv("DISCOGS_TOKEN", "test-token")
        self.api = _FakeAPI(monkeypatch, collection or _Collection())

    def run(self, *args: str) -> tuple[Result, list[tuple[str, dict[str, str]]]]:
        start = len(self.api.sends)
        result = CliRunner().invoke(cli, list(args))
        return result, list(self.api.sends[start:])


def _paths(sent: list[tuple[str, dict[str, str]]]) -> list[str]:
    return [path for path, _ in sent]


def _pages(sent: list[tuple[str, dict[str, str]]]) -> list[int]:
    return [int(params["page"]) for path, params in sent if path == _RECORDS]


def _document(result: Result) -> dict[str, Any]:
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def _refs(result: Result) -> list[str]:
    return [row["ref"] for row in _document(result)["results"]]


def _continuation(result: Result) -> list[str]:
    """The argv of the `Continue scan:` or `Next page:` footer."""
    line = next(
        line
        for line in result.stdout.splitlines()
        if line.startswith(("Continue scan:", "Next page:"))
    )
    return shlex.split(line.split(":", 1)[1])[1:]  # drop "agent-discogs"


_TECHNO_REFS = [f"@r{1000 + n}" for n in _TECHNO]


class TestListing:
    def test_pages_server_side(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _Harness(monkeypatch)
        result, sent = h.run("get", "collection", "--json")
        document = _document(result)
        assert _paths(sent) == ["/oauth/identity", _FOLDER_LIST, _RECORDS]
        assert sent[2][1] == {"page": "1", "per_page": "5"}
        assert document["pagination"] == {
            "page": 1,
            "total_items": 250,
            "total_pages": 50,
        }
        assert document["results"][3] == {
            "ref": "@r7",
            "title": "Artist - Record 3",
            "year": 2001,
            "label": "Label",
            "catno": "CAT3",
            "format": "Vinyl (LP)",
            "folder": "Vinyl",
            "added": "2024-01-04T10:00:00-08:00",
            "rating": 4,
        }
        assert "rating" not in document["results"][0]  # 0 is unrated

        last, sent = h.run(
            "get", "collection", "--limit", "100", "--page", "3", "--json"
        )
        assert _paths(sent) == [_RECORDS]  # the username and folders are cached
        assert _refs(last) == [f"@r{1000 + n}" for n in range(200, 250)]

        again, sent = h.run("get", "collection", "--json")
        assert sent == []
        assert again.stdout == result.stdout

    def test_text_rows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _Harness(monkeypatch)
        result, _ = h.run("get", "collection")
        assert result.exit_code == 0, result.output
        lines = result.stdout.splitlines()
        assert lines[0] == "Collection: me (page 1, 5 of 250 records)"
        assert lines[2] == (
            '@r1000 "Artist - Record 0" 2001 · Label CAT0 · Vinyl (LP) '
            "· folder Uncategorized · added 2024-01-01"
        )
        assert lines[5] == (
            '@r7 "Artist - Record 3" 2001 · Label CAT3 · Vinyl (LP) '
            "· folder Vinyl · added 2024-01-04 · rating 4"
        )
        assert lines[-1] == "Next page: agent-discogs get collection --page 2"

    def test_a_release_owned_twice_shows_twice(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch)
        result, _ = h.run("get", "collection", "--limit", "100", "--page", "2")
        copies = [
            line for line in result.stdout.splitlines() if line.startswith("@r7 ")
        ]
        assert copies == [
            (
                '@r7 "Artist - Record 120" 2001 · Label CAT120 · Vinyl (LP) '
                "· folder Uncategorized · added 2024-01-09"
            )
        ]
        first, _ = h.run("get", "collection", "--json")
        second, _ = h.run(
            "get", "collection", "--limit", "100", "--page", "2", "--json"
        )
        folders = [
            row["folder"]
            for result in (first, second)
            for row in _document(result)["results"]
            if row["ref"] == "@r7"
        ]
        assert folders == ["Vinyl", "Uncategorized"]

    def test_full_gives_the_records_without_the_folder_list(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch)
        result, sent = h.run("get", "collection", "--json", "--full")
        assert _paths(sent) == ["/oauth/identity", _RECORDS]
        row = _document(result)["results"][3]
        assert row["folder_id"] == 5
        assert row["instance_id"] == 10_003
        assert row["basic_information"]["labels"][0]["catalog_number"] == "CAT3"

    def test_sort_is_sent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _Harness(monkeypatch)
        result, sent = h.run("get", "collection", "--sort", "added", "--desc")
        assert result.exit_code == 0, result.output
        assert sent[-1] == (
            _RECORDS,
            {"sort": "added", "sort_order": "desc", "page": "1", "per_page": "5"},
        )
        assert result.stdout.splitlines()[-1] == (
            "Next page: agent-discogs get collection --sort added --desc --page 2"
        )

    def test_cached_misses_fail(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _Harness(monkeypatch)
        result, sent = h.run("--cached", "get", "collection", "--json")
        assert json_error(result)["code"] == "cache_miss"
        assert sent == []
        h.run("get", "collection")
        result, sent = h.run("--cached", "get", "collection")
        assert result.exit_code == 0, result.output
        assert sent == []

        # A scan fails at the first page the cache does not hold.
        result, sent = h.run("--cached", "get", "collection", "--style", "Techno")
        assert "Not cached" in text_error(result)
        assert sent == []

    def test_records_with_missing_parts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A copy in no folder, in a folder the list does not name, without
        release data, from year 0; the day added is the timestamp's own."""
        odd = {
            0: {"id": 50, "folder_id": None},
            1: {
                "id": 51,
                "folder_id": 99,
                "date_added": "2024-01-31T23:30:00-08:00",
                "basic_information": {"id": 51, "title": "Bare", "year": 0},
            },
        }
        h = _Harness(monkeypatch, _Collection(3, odd=odd))
        result, _ = h.run("get", "collection")
        assert result.exit_code == 0, result.output
        assert result.stdout.splitlines()[2:4] == [
            "@r50",
            '@r51 "Bare" · folder 99 · added 2024-01-31',
        ]
        rows = _document(h.run("get", "collection", "--json")[0])["results"]
        assert rows[:2] == [
            {"ref": "@r50"},
            {
                "ref": "@r51",
                "title": "Bare",
                "folder": "folder 99",
                "added": "2024-01-31T23:30:00-08:00",
            },
        ]

    def test_an_empty_collection(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _Harness(monkeypatch, _Collection(0))
        result, _ = h.run("get", "collection")
        assert result.exit_code == 0, result.output
        assert result.stdout == "Collection: me (page 1, 0 of 0 records)\n\n"
        result, _ = h.run("get", "collection", "--style", "Techno", "--json")
        assert _document(result)["results"] == []

    @pytest.mark.parametrize("throttle", ["identity", "folders"])
    def test_a_429_before_the_listing_fails(
        self, monkeypatch: pytest.MonkeyPatch, throttle: str
    ) -> None:
        h = _Harness(monkeypatch, _Collection(throttle=(throttle,)))
        result, sent = h.run("get", "collection", "--style", "Techno", "--json")
        assert json_error(result)["code"] == "rate_limited"
        assert _RECORDS not in _paths(sent)


class TestFilters:
    def test_scans_for_the_matches(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _Harness(monkeypatch)
        argv = ["get", "collection", "--style", "Techno", "--limit", "100"]
        result, sent = h.run(*argv, "--json")
        assert _paths(sent) == [
            "/oauth/identity",
            _FOLDER_LIST,
            _RECORDS,
            _RECORDS,
            _RECORDS,
        ]
        assert _pages(sent) == [1, 2, 3]
        assert all("sort" not in params for _, params in sent)
        document = _document(result)
        assert [row["ref"] for row in document["results"]] == _TECHNO_REFS
        assert document["pagination"] == {
            "page": 1,
            "total_items": 250,
            "total_pages": 3,
            "filtered": True,
            "capped": False,
            "next_cursor": None,
        }

        text, sent = h.run(*argv)
        assert sent == []
        lines = text.stdout.splitlines()
        assert lines[0] == "Collection: me style=Techno (page 1, 12 of ≤250 records)"
        assert len(lines) == 14

    @pytest.mark.parametrize(
        "filters",
        [
            ["--format", '12"'],
            ["--format", "vinyl", "--year", "1992"],
            ["--genre", "electronic", "--label", "TRESOR"],
            ["--style", "techno", "--format", "33 ⅓ rpm"],
        ],
    )
    def test_each_filter_matches_one_value_in_any_case(
        self, monkeypatch: pytest.MonkeyPatch, filters: list[str]
    ) -> None:
        h = _Harness(monkeypatch)
        result, _ = h.run("get", "collection", *filters, "--limit", "100", "--json")
        assert _refs(result) == _TECHNO_REFS

    def test_a_filter_matches_a_whole_value(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch)
        result, _ = h.run("get", "collection", "--style", "Tech", "--json")
        assert _refs(result) == []
        result, _ = h.run("get", "collection", "--genre", "Techno", "--json")
        assert _refs(result) == []

    def test_max_requests_caps_and_the_continuation_resumes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch)
        argv = ["get", "collection", "--style", "Techno", "--limit", "100"]
        result, sent = h.run(*argv, "--max-requests", "3", "--json")
        assert _paths(sent) == ["/oauth/identity", _FOLDER_LIST, _RECORDS]
        pagination = _document(result)["pagination"]
        assert pagination["capped"] is True
        assert pagination["stop_reason"] == "max_requests"
        assert pagination["next_cursor"] == "2:100"

        h = _Harness(monkeypatch)  # a cold cache again
        text, _ = h.run(*argv, "--max-requests", "3")
        assert "scan stopped at --max-requests 3" in text.stdout.splitlines()[0]
        continuation = _continuation(text)
        assert continuation[-2:] == ["--after", "2:100"]
        seen = [line.split()[0] for line in text.stdout.splitlines()[2:6]]
        more, sent = h.run(*continuation)
        assert _pages(sent) == [2, 3]
        assert more.exit_code == 0, more.output
        seen += [line.split()[0] for line in more.stdout.splitlines()[2:]]
        assert seen == _TECHNO_REFS  # every match once, in order

    def test_a_cap_spent_before_the_scan_reads_no_page(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch)
        argv = ["get", "collection", "--style", "Techno"]
        result, sent = h.run(*argv, "--max-requests", "2")
        assert result.exit_code == 0, result.output
        assert _paths(sent) == ["/oauth/identity", _FOLDER_LIST]
        assert result.stdout.splitlines()[0] == (
            "Collection: me style=Techno "
            "(--max-requests 2 ran out before the scan started)"
        )
        assert _continuation(result)[-2:] == ["--after", "1:0"]

        # A cap the username spends sends nothing more: no row needs a name.
        h = _Harness(monkeypatch)
        result, sent = h.run(*argv, "--max-requests", "1", "--json")
        assert _paths(sent) == ["/oauth/identity"]
        pagination = _document(result)["pagination"]
        assert pagination["total_items"] is None
        assert pagination["capped"] is True

    def test_rate_limit_keeps_the_rows_read(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch, _Collection(throttle=("page:2",)))
        argv = ["get", "collection", "--style", "Techno", "--limit", "100"]
        result, _ = h.run(*argv, "--json")
        document = _document(result)
        assert _refs(result) == _TECHNO_REFS[:4]
        assert document["pagination"]["stop_reason"] == "rate_limited"
        assert document["pagination"]["retry_after"] == 30

        h = _Harness(monkeypatch, _Collection(throttle=("page:1",)))
        result, _ = h.run(*argv, "--json")
        assert json_error(result)["code"] == "rate_limited"

    def test_the_continuation_repeats_every_flag(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch)
        argv = ["get", "collection", "--format", '12"', "--style", "Techno"]
        flags = ["--sort", "year", "--desc", "--limit", "2", "--max-requests", "3"]
        result, _ = h.run("--fresh", *argv, *flags)
        assert result.exit_code == 0, result.output
        assert _continuation(result) == [
            "--fresh",
            *argv,
            *("--sort", "year", "--desc", "--max-requests", "3"),
            *("--limit", "2", "--after", "2:18"),
        ]
        assert result.stdout.splitlines()[0] == (
            'Collection: me format=12" style=Techno (page 1, 2 of ≤250 records)'
        )

    def test_fresh_resends_the_lookups_that_spent_the_cap(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch)
        argv = ["--fresh", "get", "collection", "--style", "Techno"]
        for _ in range(2):
            result, sent = h.run(*argv, "--max-requests", "2")
            assert _paths(sent) == ["/oauth/identity", _FOLDER_LIST]
            assert "--fresh sends the header lookup again" in result.stdout

    def test_shares_the_owned_read(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _Harness(monkeypatch)
        result, sent = h.run(
            "search", "release", "x", "--release-type", "all", "--owned"
        )
        assert result.exit_code == 0, result.output
        assert _pages(sent) == [1, 2, 3]

        argv = ["get", "collection", "--style", "Techno", "--limit", "100"]
        result, sent = h.run(*argv)
        assert result.exit_code == 0, result.output
        assert _paths(sent) == [_FOLDER_LIST]
        result, sent = h.run(*argv, "--json", "--full")
        assert sent == []
        assert len(_document(result)["results"]) == 12


class TestArguments:
    @pytest.mark.parametrize(
        "argv",
        [
            "get collection @r1",
            "get collection --sort price",
            "get collection --desc",
            "get collection --max-requests 5",
            "get collection --after 2:0",
            "get collection --style Techno --page 2",
            "get collection --style Techno --max-requests 0",
            "get collection --limit 101",
            "get collection --for-sale",
            "get collection --country US",
            "get collection --owned",
            "get collection --role Main",
            "get release @r1 --genre Rock",
            "get versions @m1 --style Techno",
        ],
    )
    def test_misplaced_flags_fail_before_any_request(
        self, monkeypatch: pytest.MonkeyPatch, argv: str
    ) -> None:
        h = _Harness(monkeypatch)
        result, sent = h.run(*argv.split(), "--json")
        assert json_error(result)["code"] == "invalid_argument"
        assert sent == []

    def test_a_ref_is_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _Harness(monkeypatch)
        result, sent = h.run("get", "collection", "@r1")
        assert text_error(result).startswith(
            "✗ get collection takes no ref: it lists your own collection."
        )
        assert sent == []

    @pytest.mark.parametrize("value", ["", "  "])
    def test_an_empty_filter_is_rejected(
        self, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        h = _Harness(monkeypatch)
        result, sent = h.run("get", "collection", "--style", value)
        assert text_error(result).startswith(
            "✗ --style needs a value: a filter matches one value exactly."
        )
        assert sent == []


class TestNeedsAUser:
    @pytest.mark.parametrize("mode", ["none", "consumer"])
    def test_fails_before_any_request(
        self, monkeypatch: pytest.MonkeyPatch, mode: str
    ) -> None:
        if mode == "consumer":
            monkeypatch.setenv("DISCOGS_CONSUMER_KEY", "key")
            monkeypatch.setenv("DISCOGS_CONSUMER_SECRET", "secret")
        h = _Harness(monkeypatch, token=False)
        result, sent = h.run("get", "collection", "--json")
        error = json_error(result)
        assert error["code"] == "auth_required"
        assert "get collection" in error["message"]
        assert sent == []

        result, sent = h.run("get", "collection", "--style", "Techno")
        assert "DISCOGS_TOKEN" in text_error(result)
        assert sent == []
