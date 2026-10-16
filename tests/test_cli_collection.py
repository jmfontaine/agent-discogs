"""Tests for `get collection`, against the real SDK client over a mock
transport."""

from __future__ import annotations

import json
import re
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
# "To Sell" holds the records a test moves there through `odd`.
_FOLDERS = [
    {"id": 0, "name": "All", "count": 250},
    {"id": 1, "name": "Uncategorized", "count": 249},
    {"id": 5, "name": "Vinyl", "count": 1},
    {"id": 7, "name": "To Sell", "count": 0},
]


def _folder(folder_id: int) -> str:
    """The path of one folder's records."""
    return f"{_USER}/collection/folders/{folder_id}/releases"


# The Techno records: 12 of 250, on each of the three 100-record pages.
_TECHNO = (5, 17, 40, 99, 100, 130, 160, 199, 200, 210, 230, 249)
# The masters of the releases that have one: release 7 (records 3 and 120)
# is master 60, as release 1050 is when a test gives it one through `odd`;
# releases 1011 and 1012 are master 61. No other record has a `master_id`.
_MASTERS = {7: 60, 1011: 61, 1012: 61}

_VALUE_PATH = f"{_USER}/collection/value"
# The collection value as Discogs writes it, in the profile's currency.
_VALUE = {"minimum": "€75.50", "median": "€100.25", "maximum": "€1,250.00"}


def _too_many() -> httpx2.Response:
    return httpx2.Response(429, json={}, headers={"Retry-After": "30"})


class _Collection:
    """The token's user `me` owns `size` records, in collection order. Record
    `n` is release `1000 + n`, `Record <n>` by Artist (artist 4242) on Label
    (`CAT<n>`), an Electronic, House LP from 2001 in Uncategorized, added on
    2024-01-(n % 28 + 1), except that the `_TECHNO` records are Techno 12"
    singles from 1992 on Tresor, record 3 is release 7, rated 4, in Vinyl,
    record 120 is release 7 again, in Uncategorized, the releases in
    `_MASTERS` name their master, and `odd` replaces records by number. Each
    folder of `folders` (by default `_FOLDERS`) lists the records whose
    `folder_id` is its id, and folder 0 every record. The profile adds
    `profile` (by default `curr_abbr: EUR`) and the collection value is
    `value` (by default `_VALUE`). `throttle` answers with a 429:
    `"identity"` the username, `"folders"` the folder list, `"page:N"` page
    N of any folder. A search lists releases 1 and 2."""

    def __init__(
        self,
        size: int = 250,
        *,
        throttle: tuple[str, ...] = (),
        odd: dict[int, dict[str, Any]] | None = None,
        profile: dict[str, Any] | None = None,
        value: dict[str, Any] | None = None,
        folders: list[dict[str, Any]] | None = None,
    ) -> None:
        self.size, self.throttle, self.odd = size, throttle, odd or {}
        self.profile = {"curr_abbr": "EUR"} if profile is None else profile
        self.value = _VALUE if value is None else value
        self.folders = _FOLDERS if folders is None else folders

    def __call__(self, path: str, params: dict[str, str]) -> object:
        if path == "/oauth/identity":
            if "identity" in self.throttle:
                return _too_many()
            return {"id": 7, "username": "me"}
        if path == _FOLDER_LIST:
            if "folders" in self.throttle:
                return _too_many()
            return {"folders": self.folders}
        folder = re.fullmatch(rf"{_USER}/collection/folders/(\d+)/releases", path)
        if folder:
            page = int(params["page"])
            if f"page:{page}" in self.throttle:
                return _too_many()
            return self._page(int(folder[1]), page, int(params["per_page"]))
        if path == _USER:
            return {"id": 7, "username": "me", **self.profile}
        if path == _VALUE_PATH:
            return self.value
        if path == _SEARCH:
            rows = [{"id": i, "title": f"Title {i}", "type": "release"} for i in (1, 2)]
            pages = {"page": 1, "pages": 1, "items": 2, "per_page": 100}
            return {"pagination": pages, "results": rows}
        raise AssertionError(path)

    def _page(self, folder: int, page: int, per: int) -> object:
        """Page `page` of folder `folder`: every record for folder 0, else
        the records whose `folder_id` is `folder`."""
        records = [self.odd.get(n) or self.record(n) for n in range(self.size)]
        if folder:
            records = [r for r in records if r.get("folder_id") == folder]
        items = records[(page - 1) * per : page * per]
        pages = {"page": page, "pages": -(-len(records) // per), "items": len(records)}
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
                "artists": [{"id": 4242, "name": "Artist", "join": ""}],
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
            }
            | ({"master_id": _MASTERS[release]} if release in _MASTERS else {}),
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
            "artists": [{"ref": "@a4242", "name": "Artist"}],
            "year": 2001,
            "label": "Label",
            "catno": "CAT3",
            "format": "Vinyl (LP)",
            "genres": ["Electronic"],
            "styles": ["House"],
            "master": "@m60",
            "folder": "Vinyl",
            "added": "2024-01-04T10:00:00-08:00",
            "rating": 4,
        }
        assert list(document["results"][3]) == [  # the documented key order
            "ref",
            *("title", "artists", "year", "label", "catno", "format"),
            *("genres", "styles", "master", "folder", "added", "rating"),
        ]
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
        assert row["basic_information"]["master_id"] == 60
        assert "artists" not in row  # the record as sent, not the projection

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

    def test_rows_carry_artists_genres_styles_and_the_master(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Each from `basic_information`, in credit order, and each omitted
        when empty; Discogs' master 0 is no master."""

        def release(n: int, **information: Any) -> dict[str, Any]:
            """Record `n` as release `1000 + n`, with no master unless given."""
            record = _Collection.record(n)
            basic = record["basic_information"]
            basic.pop("master_id", None)
            return {
                **record,
                "id": 1000 + n,
                "basic_information": {**basic, "id": 1000 + n, **information},
            }

        split = [
            {"id": 1, "name": "One", "join": "&"},
            {"name": "Two", "join": ","},
            {"id": 3, "join": ""},
            {"join": ""},
        ]
        odd = {
            0: release(50, master_id=60),
            1: release(1, master_id=0, artists=split),
            2: release(2, genres=[], styles=[], artists=[]),
            3: release(3, artists=[{"join": ""}], genres=None, styles=None),
        }
        h = _Harness(monkeypatch, _Collection(13, odd=odd))
        result, _ = h.run("get", "collection", "--limit", "20", "--json")
        rows = {row["ref"]: row for row in _document(result)["results"]}
        assert rows["@r1050"]["master"] == "@m60"
        assert rows["@r1001"]["artists"] == [
            {"ref": "@a1", "name": "One"},
            {"name": "Two"},
            {"ref": "@a3"},
        ]
        for ref in ("@r1002", "@r1003"):
            assert not {"artists", "genres", "styles"} & set(rows[ref])
        masters = {ref: row.get("master") for ref, row in rows.items()}
        assert masters == {
            "@r1050": "@m60",
            **{f"@r{1000 + n}": None for n in range(1, 11)},
            "@r1011": "@m61",
            "@r1012": "@m61",
        }
        joins = [a for row in rows.values() for a in row.get("artists", [])]
        assert not any("join" in a for a in joins)

    def test_an_empty_collection(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _Harness(monkeypatch, _Collection(0))
        result, _ = h.run("get", "collection")
        assert result.exit_code == 0, result.output
        assert result.stdout == "Collection: me (page 1, 0 of 0 records)\n\n"
        result, _ = h.run("get", "collection", "--style", "Techno", "--json")
        assert _document(result)["results"] == []

    @pytest.mark.parametrize(
        ("throttle", "flags"),
        [
            ("identity", ["--style", "Techno"]),
            ("folders", ["--style", "Techno"]),
            ("folders", ["--folder", "Vinyl"]),
            ("folders", ["--folder", "Vinyl", "--json", "--full"]),
        ],
    )
    def test_a_429_before_the_listing_fails(
        self, monkeypatch: pytest.MonkeyPatch, throttle: str, flags: list[str]
    ) -> None:
        h = _Harness(monkeypatch, _Collection(throttle=(throttle,)))
        result, sent = h.run("get", "collection", *flags, "--json")
        assert json_error(result)["code"] == "rate_limited"
        assert not [path for path in _paths(sent) if path.endswith("/releases")]


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
        assert all("Techno" in row["styles"] for row in document["results"])
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


def _numbered(*numbers: int) -> list[str]:
    """The refs of records `numbers` (record 3 and 120 are release 7)."""
    return [f"@r{_Collection.record(n)['id']}" for n in numbers]


def _moved(n: int, folder_id: int, **information: Any) -> dict[str, Any]:
    """Record `n` in folder `folder_id`, with `information` replacing parts
    of its `basic_information`."""
    record = _Collection.record(n)
    basic = {**record["basic_information"], **information}
    return {**record, "folder_id": folder_id, "basic_information": basic}


class TestPatterns:
    def test_title_and_artist_match_a_whole_value(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch)
        argv = ["get", "collection", "--limit", "100", "--json"]
        result, sent = h.run(*argv, "--title", "record 12")
        assert _refs(result) == _numbered(12)  # not 120-129
        assert _pages(sent) == [1, 2, 3]
        assert _document(result)["pagination"]["filtered"] is True
        result, _ = h.run(*argv, "--title", "*record 12*")
        assert _refs(result) == _numbered(12, *range(120, 130))
        assert len(_refs(h.run(*argv, "--artist", "*rtis*")[0])) == 100
        assert _refs(h.run(*argv, "--artist", "artist")[0])[:3] == _numbered(0, 1, 2)
        # --title never matches the artist, --artist never the title.
        assert _refs(h.run(*argv, "--title", "Artist")[0]) == []
        assert _refs(h.run(*argv, "--artist", "Record 1")[0]) == []
        assert _refs(h.run(*argv, "--artist", "*record*")[0]) == []

    def test_a_split_release_matches_any_artist(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        split = [
            {"id": 1, "name": "Miles Davis", "join": "&"},
            {"id": 2, "name": "John Coltrane", "join": ""},
        ]
        odd = {4: _moved(4, 1, artists=split)}
        h = _Harness(monkeypatch, _Collection(odd=odd))
        argv = ["get", "collection", "--json"]
        for artist in ("miles davis", "John Coltrane", "*coltrane", "miles*"):
            assert _refs(h.run(*argv, "--artist", artist)[0]) == _numbered(4)
        assert _refs(h.run(*argv, "--artist", "miles")[0]) == []

    def test_every_filter_must_match(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _Harness(monkeypatch)
        argv = ["get", "collection", "--limit", "100"]
        filters = ["--style", "Techno", "--artist", "artist", "--title", "*1*"]
        result, _ = h.run(*argv, *filters, "--json")
        assert _refs(result) == _numbered(17, 100, 130, 160, 199, 210)
        result, _ = h.run(*argv, *filters, "--format", "LP", "--json")
        assert _refs(result) == []
        text, _ = h.run(*argv, *filters)
        assert text.stdout.splitlines()[0] == (
            "Collection: me style=Techno artist=artist title=*1* "
            "(page 1, 6 of ≤250 records)"
        )

    def test_the_continuation_keeps_the_patterns(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch)
        filters = ["--artist", "\\Art*", "--title", "Record 1*"]
        result, _ = h.run("get", "collection", *filters, "--limit", "2")
        assert result.exit_code == 0, result.output
        assert result.stdout.splitlines()[0] == (
            'Collection: me artist=\\Art* title="Record 1*" (page 1, 2 of ≤250 records)'
        )
        continuation = _continuation(result)
        assert continuation == [
            *("get", "collection", *filters),
            *("--limit", "2", "--after", "2:11"),
        ]
        more, _ = h.run(*continuation, "--json")
        assert _refs(more) == _numbered(11, 12)

    @pytest.mark.parametrize(
        ("flags", "numbers"),
        [
            (["--style", "techno"], _TECHNO),
            (["--style", "*tech*"], _TECHNO),
            (["--style", "*o*"], _TECHNO + tuple(n for n in range(250))),
            (["--year", "19*"], _TECHNO),
            (["--year", "199?"], ()),  # `?` is literal
            (["--label", "tres*"], _TECHNO),
            (["--genre", "Elec*"], tuple(range(250))),
        ],
    )
    def test_existing_filters_take_patterns(
        self,
        monkeypatch: pytest.MonkeyPatch,
        flags: list[str],
        numbers: tuple[int, ...],
    ) -> None:
        h = _Harness(monkeypatch)
        result, _ = h.run("get", "collection", *flags, "--limit", "100", "--json")
        assert _refs(result) == _numbered(*sorted(set(numbers)))[:100]

    def test_a_record_with_no_year_never_matches_a_year(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        odd = {1: _moved(1, 1, year=0), 2: _moved(2, 1, year=None)}
        h = _Harness(monkeypatch, _Collection(4, odd=odd))
        result, _ = h.run("get", "collection", "--year", "*", "--json")
        assert _refs(result) == _numbered(0, 3)


class TestFolder:
    def test_lists_one_folder_server_side(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for name in ("Vinyl", "vinyl"):
            h = _Harness(monkeypatch)
            result, sent = h.run("get", "collection", "--folder", name, "--json")
            assert _paths(sent) == ["/oauth/identity", _FOLDER_LIST, _folder(5)]
            document = _document(result)
            assert _refs(result) == ["@r7"]
            assert document["pagination"] == {
                "page": 1,
                "total_items": 1,
                "total_pages": 1,
            }
        text, sent = h.run("get", "collection", "--folder", "vinyl")
        assert sent == []
        assert text.stdout.splitlines()[0] == (
            "Collection: me folder=vinyl (page 1, 1 of 1 records)"
        )
        assert "Next page:" not in text.stdout

    def test_a_folder_name_with_a_space(self, monkeypatch: pytest.MonkeyPatch) -> None:
        odd = {10: _moved(10, 7), 11: _moved(11, 7)}
        h = _Harness(monkeypatch, _Collection(odd=odd))
        argv = ["get", "collection", "--folder", "To Sell", "--limit", "1"]
        result, sent = h.run(*argv)
        assert result.exit_code == 0, result.output
        assert _paths(sent)[-1] == _folder(7)
        lines = result.stdout.splitlines()
        assert lines[0] == 'Collection: me folder="To Sell" (page 1, 1 of 2 records)'
        assert lines[-1] == (
            "Next page: agent-discogs get collection --folder 'To Sell' "
            "--limit 1 --page 2"
        )
        again, sent = h.run(*argv)
        assert sent == []
        assert again.stdout == result.stdout
        more, sent = h.run(*_continuation(result), "--json")
        assert _refs(more) == _numbered(11)
        assert _paths(sent) == [_folder(7)]

    def test_pages_a_folder(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _Harness(monkeypatch)
        argv = ["get", "collection", "--folder", "Uncategorized", "--limit", "2"]
        result, sent = h.run(*argv, "--page", "2", "--sort", "year")
        assert result.exit_code == 0, result.output
        assert sent[-1] == (
            _folder(1),
            {"sort": "year", "sort_order": "asc", "page": "2", "per_page": "2"},
        )
        assert result.stdout.splitlines()[-1] == (
            "Next page: agent-discogs get collection --folder Uncategorized "
            "--sort year --limit 2 --page 3"
        )

    def test_all_is_folder_0(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _Harness(monkeypatch)
        result, sent = h.run("get", "collection", "--folder", "All", "--json")
        assert _paths(sent) == ["/oauth/identity", _FOLDER_LIST, _RECORDS]
        plain, sent = h.run("get", "collection", "--json")
        assert sent == []
        assert plain.stdout == result.stdout

    def test_scans_only_the_folder(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _Harness(monkeypatch)
        argv = ["get", "collection", "--folder", "Uncategorized", "--style", "Techno"]
        result, sent = h.run(*argv, "--limit", "100")
        assert result.exit_code == 0, result.output
        assert _paths(sent)[2:] == [_folder(1)] * 3
        lines = result.stdout.splitlines()
        assert lines[0] == (
            "Collection: me folder=Uncategorized style=Techno "
            "(page 1, 12 of ≤249 records)"
        )
        assert [line.split()[0] for line in lines[2:]] == _TECHNO_REFS

        h = _Harness(monkeypatch)
        result, sent = h.run(*argv, "--limit", "100", "--max-requests", "4")
        assert _paths(sent) == ["/oauth/identity", _FOLDER_LIST, *[_folder(1)] * 2]
        continuation = _continuation(result)
        assert continuation[:6] == argv
        assert continuation[-2:] == ["--after", "2:200"]
        more, sent = h.run(*continuation)
        assert _paths(sent) == [_folder(1)]
        assert more.exit_code == 0, more.output

    def test_a_cap_spent_by_the_username_resolves_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch)
        argv = ["get", "collection", "--folder", "Vinyl", "--style", "Techno"]
        result, sent = h.run(*argv, "--max-requests", "1", "--json")
        assert _paths(sent) == ["/oauth/identity"]
        pagination = _document(result)["pagination"]
        assert pagination["capped"] is True
        assert pagination["next_cursor"] == "1:0"
        text, _ = h.run(*argv, "--max-requests", "1")
        assert _continuation(text) == [
            *argv,
            *("--max-requests", "1", "--after", "1:0"),
        ]

    def test_full_reads_the_folder_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _Harness(monkeypatch)
        result, sent = h.run(
            "get", "collection", "--folder", "Vinyl", "--json", "--full"
        )
        assert _paths(sent) == ["/oauth/identity", _FOLDER_LIST, _folder(5)]
        assert _document(result)["results"][0]["folder_id"] == 5

    def test_an_unknown_folder(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _Harness(monkeypatch)
        result, sent = h.run("get", "collection", "--folder", "Jazzz", "--json")
        error = json_error(result)
        assert error["code"] == "invalid_argument"
        assert error["message"] == (
            '--folder "Jazzz" matches no folder. '
            "Your folders: All, Uncategorized, Vinyl, To Sell."
        )
        assert _paths(sent) == ["/oauth/identity", _FOLDER_LIST]
        text, sent = h.run("get", "collection", "--folder", "Jazzz")
        assert sent == []
        assert text_error(text).startswith('✗ --folder "Jazzz" matches no folder.')

    def test_an_ambiguous_folder(self, monkeypatch: pytest.MonkeyPatch) -> None:
        folders = [*_FOLDERS, {"id": 8, "name": "Jazz"}, {"id": 9, "name": "jazz"}]
        h = _Harness(monkeypatch, _Collection(folders=folders))
        result, sent = h.run("get", "collection", "--folder", "JAZZ", "--json")
        error = json_error(result)
        assert error["code"] == "invalid_argument"
        assert error["message"].startswith(
            '--folder "JAZZ" matches more than one folder: Jazz (id 8), jazz (id 9).'
        )
        assert _paths(sent) == ["/oauth/identity", _FOLDER_LIST]

    def test_cached_misses_fail(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _Harness(monkeypatch)
        result, sent = h.run("--cached", "get", "collection", "--folder", "Vinyl")
        assert "Not cached" in text_error(result)
        assert sent == []


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
            "get collection --folder Vinyl --max-requests 5",
            "get collection --folder Vinyl --after 1:0",
            "get collection --artist x --page 2",
            "get collection --title x --page 2",
            "get collection --folder Vinyl --value",
            "get collection --artist x --value",
            "get collection --title x --value",
            "get release @r1 --artist x",
            "get versions @m1 --folder Vinyl",
            "get wantlist --title x",
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

    @pytest.mark.parametrize("flag", ["--style", "--artist", "--title"])
    @pytest.mark.parametrize("value", ["", "  "])
    def test_an_empty_filter_is_rejected(
        self, monkeypatch: pytest.MonkeyPatch, flag: str, value: str
    ) -> None:
        h = _Harness(monkeypatch)
        result, sent = h.run("get", "collection", flag, value)
        assert text_error(result).startswith(
            f"✗ {flag} needs a value: a filter matches a whole value, ignoring "
            "case, with * for any run of characters."
        )
        assert sent == []

    @pytest.mark.parametrize("value", ["", "  "])
    def test_an_empty_folder_is_rejected(
        self, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        h = _Harness(monkeypatch)
        result, sent = h.run("get", "collection", "--folder", value, "--json")
        error = json_error(result)
        assert error["code"] == "invalid_argument"
        assert error["message"].startswith("--folder needs a folder name")
        assert sent == []


class TestValue:
    def test_prints_the_value_in_the_profile_currency(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch)
        result, sent = h.run("get", "collection", "--value", "--json")
        assert result.exit_code == 0, result.output
        assert result.stdout == (
            '{"currency":"EUR","minimum":75.5,"median":100.25,"maximum":1250.0}\n'
        )
        assert _paths(sent) == ["/oauth/identity", _USER, _VALUE_PATH]

        again, sent = h.run("get", "collection", "--value", "--json")
        assert sent == []
        assert again.stdout == result.stdout

        text, _ = h.run("get", "collection", "--value")
        assert text.stdout == (
            "Collection value (EUR): min 75.50 · median 100.25 · max 1250.00\n"
        )

    def test_full_prints_the_record_as_sent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Unparsed, so an amount the CLI cannot read can still be seen."""
        value = {**_VALUE, "maximum": "about 1.250.000,0,0"}
        h = _Harness(monkeypatch, _Collection(value=value))
        result, sent = h.run("get", "collection", "--value", "--json", "--full")
        assert _document(result) == {"currency": "EUR", "value": value}
        assert len(sent) == 3

    @pytest.mark.parametrize(
        ("profile", "value", "field"),
        [
            ({}, _VALUE, "currency_code"),
            ({"curr_abbr": "€"}, _VALUE, "currency_code"),
            ({"curr_abbr": "EUR"}, {**_VALUE, "median": "€12.34.56"}, "median"),
            ({"curr_abbr": "EUR"}, {**_VALUE, "maximum": "N/A"}, "maximum"),
            ({"curr_abbr": "EUR"}, {"median": "€1", "maximum": "€2"}, "minimum"),
        ],
    )
    def test_an_unreadable_value_is_a_bad_response(
        self,
        monkeypatch: pytest.MonkeyPatch,
        profile: dict[str, Any],
        value: dict[str, Any],
        field: str,
    ) -> None:
        h = _Harness(monkeypatch, _Collection(profile=profile, value=value))
        result, _ = h.run("get", "collection", "--value", "--json")
        error = json_error(result)
        assert error["code"] == "bad_response"
        assert field in error["message"]
        assert "retrying will not help" in error["hint"]
        for raw in ("€", "12.34.56", "N/A"):
            assert raw not in error["message"]

        text, _ = h.run("get", "collection", "--value")
        assert text_error(text).startswith(
            "✗ Discogs sent a response that cannot be read: "
        )

    @pytest.mark.parametrize(
        "flags", [["--limit", "5"], ["--style", "Techno"], ["--sort", "added"]]
    )
    def test_takes_no_other_flag(
        self, monkeypatch: pytest.MonkeyPatch, flags: list[str]
    ) -> None:
        h = _Harness(monkeypatch)
        result, sent = h.run("get", "collection", "--value", *flags, "--json")
        error = json_error(result)
        assert error["code"] == "invalid_argument"
        assert error["message"].startswith(
            f"--value takes no other get collection flag ({flags[0]} given)"
        )
        assert sent == []

    @pytest.mark.parametrize(
        ("argv", "message"),
        [
            (
                ["collection", "--value", "--limit", "5", "--desc"],
                "--value takes no other get collection flag (--desc, --limit given)",
            ),
            (
                ["collection", "@r1", "--value"],
                "get collection takes no ref",
            ),
            (["release", "@r1", "--value"], "--value does not apply to 'get release'"),
        ],
    )
    def test_misplaced_value_fails_before_any_request(
        self, monkeypatch: pytest.MonkeyPatch, argv: list[str], message: str
    ) -> None:
        h = _Harness(monkeypatch)
        result, sent = h.run("get", *argv)
        assert text_error(result).startswith(f"✗ {message}")
        assert sent == []

    def test_cached_misses_fail(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _Harness(monkeypatch)
        result, sent = h.run("--cached", "get", "collection", "--value", "--json")
        assert json_error(result)["code"] == "cache_miss"
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

        for argv in (["--style", "Techno"], ["--value"]):
            result, sent = h.run("get", "collection", *argv)
            assert "DISCOGS_TOKEN" in text_error(result)
            assert sent == []
