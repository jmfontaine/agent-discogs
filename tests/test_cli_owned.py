"""Tests for ownership: `--owned` on list rows and `get owned`, against the
real SDK client over a mock transport."""

from __future__ import annotations

import json
import re
from typing import Any

import httpx2
import pytest
from click.testing import CliRunner, Result

from agent_discogs import cli
from tests.conftest import _empty_listing, _FakeAPI, json_error, text_error

_USER = "/users/me"
_FOLDER_ALL = f"{_USER}/collection/folders/0/releases"
# Folder 5 is "Vinyl", 1 is "Uncategorized"; 0 ("All") is never an item's.
_FOLDERS = [
    {"id": 0, "name": "All", "count": 250},
    {"id": 1, "name": "Uncategorized", "count": 249},
    {"id": 5, "name": "Vinyl", "count": 1},
]


def _too_many() -> httpx2.Response:
    return httpx2.Response(429, json={}, headers={"Retry-After": "30"})


class _Account:
    """Discogs as the token's user `me` sees it. Every listing holds releases
    1-5 (an artist's release 4 is a master; versions 2 and 3 are tagged
    "Unofficial Release", which a versions `format` filter matches); the
    "All" folder holds `size` records, all of other releases except release 2
    once (record 10), release 3 twice (records 20 and 150, the second on page
    2) and release 6 once, in no folder Discogs named (record 30). The
    wantlist holds `wants` wants, all of other releases except release 1
    (want 5, page 1) and release 4 (want 120, page 2). Version, label and
    artist rows carry Discogs' `stats`, the user's `in_collection` being the
    copies the folder holds and `in_wantlist` 1 for a wanted release, except
    the row of release `unstated`, whose `stats.user` is null. `throttle`
    answers with a 429: `"identity"` the username, `"collection:N"`
    collection page N, `"wants:N"` wants page N, and `"search:2"` a second
    search page (the search then has two). With `label_repeats`, a label
    catalogue sends releases 2 and 3 twice in a row each."""

    def __init__(
        self,
        size: int = 250,
        *,
        wants: int = 250,
        throttle: tuple[str, ...] = (),
        unstated: int | None = None,
        label_repeats: bool = False,
    ) -> None:
        self.size, self.throttle, self.unstated = size, throttle, unstated
        self.label_repeats = label_repeats
        # record: (release id, folder id)
        self.copies = {10: (2, 1), 20: (3, 5), 30: (6, None), 150: (3, 1)}
        self.wants = [{5: 1, 120: 4}.get(n, 3000 + n) for n in range(wants)]

    def _stats(self, release_id: int) -> dict[str, Any]:
        held = sum(rid == release_id for rid, _ in self.copies.values())
        user = {"in_collection": held, "in_wantlist": int(release_id in self.wants)}
        return {
            "community": {"in_collection": 9, "in_wantlist": 3},
            "user": None if release_id == self.unstated else user,
        }

    def _wants_page(self, page: int, per: int) -> object:
        if f"wants:{page}" in self.throttle:
            return _too_many()
        ids = self.wants[(page - 1) * per : page * per]
        wants = [{"id": rid, "basic_information": {"id": rid}} for rid in ids]
        size = len(self.wants)
        pages = {"page": page, "pages": -(-size // per), "items": size}
        return {"pagination": {**pages, "per_page": per}, "wants": wants}

    def __call__(self, path: str, params: dict[str, str]) -> object:
        if path == "/oauth/identity":
            if "identity" in self.throttle:
                return _too_many()
            return {"id": 7, "username": "me"}
        if path == f"{_USER}/wants":
            return self._wants_page(int(params["page"]), int(params["per_page"]))
        if path == f"{_USER}/collection/folders":
            return {"folders": _FOLDERS}
        if path == _FOLDER_ALL:
            return self._folder_page(int(params["page"]), int(params["per_page"]))
        if path.startswith(f"{_USER}/collection/releases/"):
            release_id = int(path.rsplit("/", 1)[-1])
            items = [
                {"id": rid, "instance_id": n, "folder_id": folder}
                for n, (rid, folder) in sorted(self.copies.items())
                if rid == release_id
            ]
            pages = {"page": 1, "pages": 1, "items": len(items), "per_page": 100}
            return {"pagination": pages, "releases": items}
        key = path.rsplit("/", 1)[-1]
        # Version and label catalogue rows carry no type; they are releases.
        rows: list[dict[str, Any]] = [
            {"id": i, "title": f"Title {i}"} for i in range(1, 6)
        ]
        if key in ("versions", "releases"):
            for row in rows:
                row["stats"] = self._stats(row["id"])
        if key == "versions":
            for row in rows:
                unofficial = row["id"] in (2, 3)
                row["format"] = "CD, Unofficial Release" if unofficial else "CD"
            # The community's counts, which the row shows before the mark.
            rows[1]["stats"]["community"] = {"in_collection": 41, "in_wantlist": 284}
            if "format" in params:
                rows = [r for r in rows if params["format"] in r["format"].split(", ")]
            return {"pagination": _one_page(len(rows)), "versions": rows}
        if key == "releases" and path.startswith("/labels/"):
            if self.label_repeats:
                twice = (2, 3)
                rows = [
                    r for row in rows for r in [row] * (2 if row["id"] in twice else 1)
                ]
            return {"pagination": _one_page(len(rows)), "releases": rows}
        for row in rows:
            row["type"] = "release"
        if key == "releases":  # an artist's: release 4 is a master
            rows[3]["type"] = "master"
            return {"pagination": _one_page(5), "releases": rows}
        if key == "search":
            if "search:2" not in self.throttle:
                return {"pagination": _one_page(5), "results": rows}
            if params["page"] == "2":
                return _too_many()
            pages = {"page": 1, "pages": 2, "items": 105, "per_page": 100}
            return {"pagination": pages, "results": rows}
        return {"id": 1, "name": "Entity", "title": "Entity"}

    def _folder_page(self, page: int, per: int) -> object:
        if f"collection:{page}" in self.throttle:
            return _too_many()
        items = []
        for n in range((page - 1) * per, min(page * per, self.size)):
            rid, folder = self.copies.get(n, (1000 + n, 1))
            items.append({"id": rid, "instance_id": n, "folder_id": folder})
        pages = {"page": page, "pages": -(-self.size // per), "items": self.size}
        return {"pagination": {**pages, "per_page": per}, "releases": items}


def _one_page(items: int) -> dict[str, int]:
    return {"page": 1, "pages": 1, "items": items, "per_page": 5}


class _Harness:
    """`_FakeAPI` over `account`. With `token`, the environment holds the
    credentials of a user, as `--owned` and `get owned` need."""

    def __init__(
        self, monkeypatch: pytest.MonkeyPatch, account: _Account, *, token: bool = True
    ) -> None:
        if token:
            monkeypatch.setenv("DISCOGS_TOKEN", "test-token")
        self.api = _FakeAPI(monkeypatch, account)

    def run(self, *args: str) -> tuple[Result, list[str]]:
        start = len(self.api.sends)
        result = CliRunner().invoke(cli, list(args))
        return result, [path for path, _ in self.api.sends[start:]]

    def pages(self) -> list[int]:
        return [int(p["page"]) for path, p in self.api.sends if path == _FOLDER_ALL]


_SEARCH = ("search", "release", "x", "--release-type", "all")


def _owned(result: Result, key: str = "owned") -> dict[str, Any]:
    assert result.exit_code == 0, result.output
    document = json.loads(result.stdout)
    return {row["ref"]: row.get(key, "absent") for row in document["results"]}


# The marks, the ownership one with its multiplication sign read as `x`; a
# wantlist mark comes last.
_MARK = re.compile(r"· ((?:not )?owned(?: \u00d7\d+| \?)?)(?= ·|$)")
_WANTED_MARK = re.compile(r"· ((?:not )?wanted(?: \?)?)$")


def _marks(result: Result, pattern: re.Pattern[str] = _MARK) -> dict[str, str]:
    """The mark `pattern` finds on each row, by ref, or "none"."""
    assert result.exit_code == 0, result.output
    marks = {}
    for line in result.stdout.splitlines():
        if line.startswith("@"):
            found = pattern.search(line)
            mark = found.group(1).replace("\u00d7", "x") if found else "none"
            marks[line.split(" ", 1)[0]] = mark
    return marks


class TestOwnedRows:
    def test_search_marks_each_row_from_one_collection_read(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch, _Account())
        result, sent = h.run(*_SEARCH, "--owned", "--json")
        assert _owned(result) == {"@r1": 0, "@r2": 1, "@r3": 2, "@r4": 0, "@r5": 0}
        assert sent == ["/database/search", "/oauth/identity", *[_FOLDER_ALL] * 3]
        assert h.pages() == [1, 2, 3]
        assert json.loads(result.stdout)["collection"] == {
            "complete": True,
            "records_read": 250,
            "total_records": 250,
        }

        again, sent = h.run(*_SEARCH, "--owned", "--json")
        assert sent == []  # all cached for the hour
        assert again.stdout == result.stdout

        text, sent = h.run(*_SEARCH, "--owned")
        assert sent == []
        assert _marks(text) == {
            "@r1": "not owned",
            "@r2": "owned",
            "@r3": "owned x2",
            "@r4": "not owned",
            "@r5": "not owned",
        }
        assert "?" not in text.stdout

    @pytest.mark.parametrize("args", [(), ("--format", "CD")], ids=["server", "scan"])
    def test_versions_release_type_marks_the_rows_kept(
        self, monkeypatch: pytest.MonkeyPatch, args: tuple[str, ...]
    ) -> None:
        h = _Harness(monkeypatch, _Account())
        argv = ["get", "versions", "@m1", "--release-type", "unofficial", *args]
        result, _ = h.run(*argv, "--owned", "--json")
        assert _owned(result) == {"@r2": 1, "@r3": 2}
        text, _ = h.run(*argv, "--owned")
        assert _marks(text) == {"@r2": "owned", "@r3": "owned x2"}

    def test_without_owned_nothing_changes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch, _Account())
        result, sent = h.run(*_SEARCH, "--json")
        assert sent == ["/database/search"]
        assert set(_owned(result).values()) == {"absent"}
        assert "collection" not in json.loads(result.stdout)
        text, _ = h.run(*_SEARCH)
        assert "owned" not in text.stdout

    def test_a_page_without_release_rows_reads_no_collection(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DISCOGS_TOKEN", "test-token")
        api = _FakeAPI(monkeypatch, _empty_listing)
        result = CliRunner().invoke(cli, [*_SEARCH, "--owned", "--json"])
        assert result.exit_code == 0, result.output
        assert [path for path, _ in api.sends] == ["/database/search"]
        assert json.loads(result.stdout) == {
            "pagination": {"page": 1, "total_items": 0, "total_pages": 1},
            "results": [],
        }

    def test_cap_stops_the_read_and_leaves_rows_undecided(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Every send counts: a cold `--max-requests 3` is the search page,
        the username and one collection page."""
        h = _Harness(monkeypatch, _Account())
        result, sent = h.run(*_SEARCH, "--owned", "--max-requests", "3", "--json")
        assert sent == ["/database/search", "/oauth/identity", _FOLDER_ALL]
        # Release 3's second copy is on the unread page 2: the count found so
        # far is shown, and no row is reported 0.
        assert _owned(result) == {
            "@r1": None,
            "@r2": 1,
            "@r3": 1,
            "@r4": None,
            "@r5": None,
        }
        assert json.loads(result.stdout)["collection"] == {
            "complete": False,
            "records_read": 100,
            "total_records": 250,
            "stop_reason": "max_requests",
        }

        # A cold cache again: on the warm one, cache hits are free and the
        # read would go on to the end.
        h = _Harness(monkeypatch, _Account())
        text, _ = h.run(*_SEARCH, "--owned", "--max-requests", "3")
        assert _marks(text)["@r1"] == "owned ?"
        assert _marks(text)["@r2"] == "owned"
        assert "not owned" not in text.stdout
        assert "stopped at --max-requests 3" in text.stdout

    def test_cache_hits_leave_the_cap_to_the_collection(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch, _Account())
        h.run(*_SEARCH)  # the listing page is cached, so free below
        result, sent = h.run(*_SEARCH, "--owned", "--max-requests", "2", "--json")
        assert sent == ["/oauth/identity", _FOLDER_ALL]
        assert _owned(result)["@r1"] is None

    def test_cap_counts_the_whole_command(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With nothing left of the cap after the listing, not even the
        username is asked for."""
        h = _Harness(monkeypatch, _Account())
        result, sent = h.run(*_SEARCH, "--owned", "--max-requests", "1", "--json")
        assert sent == ["/database/search"]  # nothing left for the username
        assert set(_owned(result).values()) == {None}
        assert json.loads(result.stdout)["collection"] == {
            "complete": False,
            "records_read": 0,
            "total_records": None,
            "stop_reason": "max_requests",
        }

    def test_an_artist_search_is_named_with_its_article(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch, _Account())
        result, _ = h.run("search", "artist", "x", "--owned", "--json")
        assert "and an artist search has none" in json_error(result)["message"]

    def test_a_failed_username_lookup_is_not_retried_per_ref(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch, _Account(throttle=("identity",)))
        result, sent = h.run("get", "owned", "@r1", "@r2", "--json")
        assert result.exit_code == 1
        assert sent == ["/oauth/identity"]
        blocks = json.loads(result.stdout)
        assert [b["error"]["code"] for b in blocks] == ["rate_limited"] * 2

    def test_rate_limit_mid_read_keeps_what_was_read(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch, _Account(throttle=("collection:2",)))
        result, _ = h.run(*_SEARCH, "--owned", "--json")
        assert h.pages() == [1, 2]
        assert _owned(result) == {
            "@r1": None,
            "@r2": 1,
            "@r3": 1,
            "@r4": None,
            "@r5": None,
        }
        collection = json.loads(result.stdout)["collection"]
        assert collection["stop_reason"] == "rate_limited"
        assert collection["retry_after"] == 30

        text, _ = h.run(*_SEARCH, "--owned")
        assert _marks(text)["@r5"] == "owned ?"
        assert "rate limited" in text.stdout
        assert "retry in 30s" in text.stdout

    @pytest.mark.parametrize("throttle", ["identity", "collection:1"])
    def test_rate_limit_before_any_record_leaves_every_row_undecided(
        self, monkeypatch: pytest.MonkeyPatch, throttle: str
    ) -> None:
        h = _Harness(monkeypatch, _Account(throttle=(throttle,)))
        result, _ = h.run(*_SEARCH, "--owned", "--json")
        assert set(_owned(result).values()) == {None}
        assert json.loads(result.stdout)["collection"] == {
            "complete": False,
            "records_read": 0,
            "total_records": None,
            "stop_reason": "rate_limited",
            "retry_after": 30,
        }

    def test_a_rate_limited_listing_scan_reads_no_collection(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Discogs just answered 429: asking for the username would meet
        another, so the rows the scan kept are all undecided."""
        h = _Harness(monkeypatch, _Account(throttle=("search:2",)))
        result, sent = h.run("search", "x", "--limit", "10", "--owned", "--json")
        assert sent == ["/database/search", "/database/search"]
        assert set(_owned(result).values()) == {None}
        document = json.loads(result.stdout)
        assert document["pagination"]["stop_reason"] == "rate_limited"
        assert document["collection"]["stop_reason"] == "rate_limited"
        assert document["collection"]["retry_after"] == 30

    def test_full_rows_carry_owned(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _Harness(monkeypatch, _Account())
        result, _ = h.run(*_SEARCH, "--owned", "--json", "--full")
        assert result.exit_code == 0, result.output
        rows = json.loads(result.stdout)["results"]
        assert [row["owned"] for row in rows] == [0, 1, 2, 0, 0]

    def test_cached_with_nothing_cached_is_a_miss(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch, _Account())
        result, sent = h.run("--cached", *_SEARCH, "--owned", "--json")
        assert json_error(result)["code"] == "cache_miss"
        assert sent == []

        h.run(*_SEARCH)  # only the listing is cached
        result, sent = h.run("--cached", *_SEARCH, "--owned", "--json")
        assert json_error(result)["code"] == "cache_miss"
        assert sent == []

    @pytest.mark.parametrize(
        "argv",
        [
            "search artist x --owned",
            "search label x --owned",
            "search master x --owned",
            "search release x --owned --added-since 2024-01-01",
            "get versions @m1 --max-requests 3",
            "get versions @m1 --owned --max-requests 3",
            "get versions @m1 @m2 --owned --release-type official --max-requests 3",
            "get releases @a1 --max-requests 3",
            "get releases @a1 --owned --max-requests 3",
            "get releases @l1 --owned --max-requests 3",
            "get release @r1 --owned",
            "get owned @r1 --owned",
        ],
    )
    def test_misplaced_flags_fail_before_any_request(
        self, monkeypatch: pytest.MonkeyPatch, argv: str
    ) -> None:
        h = _Harness(monkeypatch, _Account())
        result, sent = h.run(*argv.split(), "--json")
        assert json_error(result)["code"] == "invalid_argument"
        assert sent == []


# A listing's path, and the header lookup its text view reads.
_LISTINGS = {
    "get versions @m1": ("/masters/1/versions", "/masters/1"),
    "get releases @l1": ("/labels/1/releases", "/labels/1"),
    "get releases @a1": ("/artists/1/releases", "/artists/1"),
}


class TestOwnedFromStats:
    """`get versions` and `get releases` rows carry the user's own count,
    `stats.user.in_collection`: `--owned` reads no collection there."""

    @pytest.mark.parametrize("argv", list(_LISTINGS))
    def test_get_listings_mark_release_rows(
        self, monkeypatch: pytest.MonkeyPatch, argv: str
    ) -> None:
        listing, header = _LISTINGS[argv]
        h = _Harness(monkeypatch, _Account())
        result, sent = h.run(*argv.split(), "--owned", "--json")
        assert sent == [listing]
        owned = {"@r1": 0, "@r2": 1, "@r3": 2, "@r4": 0, "@r5": 0}
        if argv.endswith("@a1"):  # the artist's row 4 is a master: no mark
            owned = {"@r1": 0, "@r2": 1, "@r3": 2, "@m4": "absent", "@r5": 0}
        assert _owned(result) == owned
        assert "collection" not in json.loads(result.stdout)

        h = _Harness(monkeypatch, _Account())
        text, sent = h.run(*argv.split(), "--owned")
        assert sorted(sent) == sorted([listing, header])
        marks = {ref: "not owned" if n == 0 else "none" for ref, n in owned.items()}
        assert _marks(text) == {**marks, "@r2": "owned", "@r3": "owned x2"}
        assert "Owned:" not in text.stdout

    def test_the_mark_ends_the_row_after_the_community_counts(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch, _Account())
        result, _ = h.run("get", "versions", "@m1", "--owned")
        assert result.exit_code == 0, result.output
        row = "@r2 · CD, Unofficial Release · have 41 · want 284 · owned"
        assert row in result.stdout.splitlines()

    def test_label_repeats_are_dropped_before_marking(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch, _Account(label_repeats=True))
        result, _ = h.run("get", "releases", "@l1", "--owned", "--json", "--limit", "7")
        assert result.exit_code == 0, result.output
        document = json.loads(result.stdout)
        assert document["pagination"]["duplicates_dropped"] == 2
        assert [(row["ref"], row["owned"]) for row in document["results"]] == [
            *(("@r1", 0), ("@r2", 1), ("@r3", 2), ("@r4", 0), ("@r5", 0))
        ]
        text, _ = h.run("get", "releases", "@l1", "--owned", "--limit", "7")
        assert "(page 1, 5 of 7 results; 2 repeated rows dropped)" in text.stdout
        assert list(_marks(text).items()) == [
            *(("@r1", "not owned"), ("@r2", "owned"), ("@r3", "owned x2")),
            *(("@r4", "not owned"), ("@r5", "not owned")),
        ]
        assert sum(line.startswith("@r") for line in text.stdout.splitlines()) == 5

    @pytest.mark.parametrize("argv", list(_LISTINGS))
    def test_a_row_without_user_stats_is_undecided(
        self, monkeypatch: pytest.MonkeyPatch, argv: str
    ) -> None:
        listing, _ = _LISTINGS[argv]
        h = _Harness(monkeypatch, _Account(unstated=3))
        result, sent = h.run(*argv.split(), "--owned", "--json")
        assert sent == [listing]
        assert _owned(result)["@r3"] is None
        assert _owned(result)["@r2"] == 1
        text, sent = h.run(*argv.split(), "--owned")
        assert listing not in sent  # cached; only the header may be read
        assert _marks(text)["@r3"] == "owned ?"
        assert "Owned:" not in text.stdout

    def test_full_records_carry_owned(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _Harness(monkeypatch, _Account())
        result, _ = h.run("get", "versions", "@m1", "--owned", "--json", "--full")
        assert result.exit_code == 0, result.output
        document = json.loads(result.stdout)
        assert [row["owned"] for row in document["results"]] == [0, 1, 2, 0, 0]
        assert document["results"][2]["stats"]["user"]["in_collection"] == 2
        assert "collection" not in document

    def test_several_refs_send_no_identity_and_no_collection_page(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch, _Account())
        result, sent = h.run("--fresh", "get", "versions", "@m1", "@m2", "--owned")
        assert result.exit_code == 0, result.output
        assert sorted(sent) == sorted(
            ["/masters/1/versions", "/masters/1", "/masters/2/versions", "/masters/2"]
        )

    def test_cached_marks_come_from_the_cached_listing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch, _Account())
        h.run("get", "versions", "@m1", "--json")
        cached, sent = h.run("--cached", "get", "versions", "@m1", "--owned", "--json")
        assert sent == []
        assert _owned(cached)["@r3"] == 2
        fresh, sent = h.run("--fresh", "get", "versions", "@m1", "--owned", "--json")
        assert sent == ["/masters/1/versions"]
        assert fresh.stdout == cached.stdout

    def test_the_cap_with_owned_caps_the_scan_alone(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The text header is read before a scan, so it counts first."""
        argv = ["get", "versions", "@m1", "--owned", "--release-type", "official"]
        h = _Harness(monkeypatch, _Account())
        result, sent = h.run(*argv, "--max-requests", "1")
        assert result.exit_code == 0, result.output
        assert sent == ["/masters/1"]

        h = _Harness(monkeypatch, _Account())
        result, sent = h.run(*argv, "--max-requests", "2")
        assert sent == ["/masters/1", "/masters/1/versions"]
        assert _marks(result) == {
            "@r1": "not owned",
            "@r4": "not owned",
            "@r5": "not owned",
        }

    @pytest.mark.parametrize("argv", list(_LISTINGS))
    def test_max_requests_with_owned_alone_has_nothing_to_cap(
        self, monkeypatch: pytest.MonkeyPatch, argv: str
    ) -> None:
        h = _Harness(monkeypatch, _Account())
        result, sent = h.run(*argv.split(), "--owned", "--max-requests", "3", "--json")
        error = json_error(result)
        assert error["code"] == "invalid_argument"
        assert "--owned" not in error["message"]
        assert sent == []


_WANTS = f"{_USER}/wants"


class TestWantedRows:
    """`--wanted` marks release rows with the user's wantlist: from each row's
    own `stats.user.in_wantlist` on `get versions` and `get releases`, from
    one read of the wantlist on `search`."""

    @pytest.mark.parametrize("argv", list(_LISTINGS))
    def test_get_listings_mark_from_the_row_stats(
        self, monkeypatch: pytest.MonkeyPatch, argv: str
    ) -> None:
        listing, header = _LISTINGS[argv]
        h = _Harness(monkeypatch, _Account())
        result, sent = h.run(*argv.split(), "--wanted", "--json")
        assert sent == [listing]
        wanted = _owned(result, "wanted")
        assert {ref: wanted[ref] for ref in ("@r1", "@r2", "@r3", "@r5")} == {
            "@r1": True,
            "@r2": False,
            "@r3": False,
            "@r5": False,
        }
        # An artist's release 4 is a master, which gets no mark.
        row4 = {"@m4": "absent"} if "@a" in argv else {"@r4": True}
        assert {ref: wanted[ref] for ref in row4} == row4
        assert "wantlist" not in json.loads(result.stdout)

        text, sent = h.run(*argv.split(), "--wanted")
        assert sent == [header]
        marks = _marks(text, _WANTED_MARK)
        assert marks["@r1"] == "wanted"
        assert marks["@r2"] == "not wanted"
        if "@a" in argv:
            assert marks["@m4"] == "none"
        assert "Wantlist:" not in text.stdout

    def test_a_row_without_user_stats_is_undecided(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch, _Account(unstated=1))
        result, _ = h.run("get", "versions", "@m1", "--wanted", "--json")
        assert _owned(result, "wanted")["@r1"] is None
        text, _ = h.run("get", "versions", "@m1", "--wanted")
        assert _marks(text, _WANTED_MARK)["@r1"] == "wanted ?"

    def test_both_marks_on_one_row_owned_first(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch, _Account())
        result, sent = h.run("get", "versions", "@m1", "--owned", "--wanted")
        assert sent == ["/masters/1/versions", "/masters/1"]
        rows = {line.split(" ", 1)[0]: line for line in result.stdout.splitlines()}
        assert rows["@r1"].endswith("· not owned · wanted")
        assert rows["@r3"].endswith("· owned \u00d72 · not wanted")

    def test_search_reads_the_wantlist_once(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch, _Account())
        result, sent = h.run(*_SEARCH, "--wanted", "--json")
        assert sent == ["/database/search", "/oauth/identity", *[_WANTS] * 3]
        assert _owned(result, "wanted") == {
            "@r1": True,
            "@r2": False,
            "@r3": False,
            "@r4": True,
            "@r5": False,
        }
        document = json.loads(result.stdout)
        assert document["wantlist"] == {
            "complete": True,
            "wants_read": 250,
            "total_wants": 250,
        }
        assert "collection" not in document

        text, sent = h.run(*_SEARCH, "--wanted")
        assert sent == []  # all of it from the cache
        assert _marks(text, _WANTED_MARK)["@r4"] == "wanted"
        assert "wanted ?" not in text.stdout
        assert "Wantlist:" not in text.stdout

    def test_search_with_both_flags_reads_the_username_once(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch, _Account())
        result, sent = h.run(*_SEARCH, "--owned", "--wanted", "--json")
        assert sent.count("/oauth/identity") == 1
        assert sent.count(_FOLDER_ALL) == 3
        assert sent.count(_WANTS) == 3
        document = json.loads(result.stdout)
        assert document["collection"]["complete"] is True
        assert document["wantlist"]["complete"] is True
        assert document["results"][0]["owned"] == 0
        assert document["results"][0]["wanted"] is True

    def test_the_cap_stops_the_read_and_leaves_rows_undecided(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Cold, `--max-requests 3` is the search page, the username and the
        first wants page: release 1 is found there, the others undecided."""
        h = _Harness(monkeypatch, _Account())
        result, sent = h.run(*_SEARCH, "--wanted", "--max-requests", "3", "--json")
        assert sent == ["/database/search", "/oauth/identity", _WANTS]
        assert _owned(result, "wanted") == {
            "@r1": True,
            "@r2": None,
            "@r3": None,
            "@r4": None,
            "@r5": None,
        }
        assert json.loads(result.stdout)["wantlist"] == {
            "complete": False,
            "wants_read": 100,
            "total_wants": 250,
            "stop_reason": "max_requests",
        }
        text, _ = h.run("--fresh", *_SEARCH, "--wanted", "--max-requests", "3")
        assert _marks(text, _WANTED_MARK)["@r2"] == "wanted ?"
        assert (
            "Wantlist: the read stopped at --max-requests 3 (100 of 250 wants "
            'read): "wanted ?" rows are not in the part read.'
        ) in text.stdout

    def test_a_spent_cap_sends_nothing_more(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch, _Account())
        result, sent = h.run(*_SEARCH, "--wanted", "--max-requests", "1", "--json")
        assert sent == ["/database/search"]
        assert set(_owned(result, "wanted").values()) == {None}
        assert json.loads(result.stdout)["wantlist"] == {
            "complete": False,
            "wants_read": 0,
            "total_wants": None,
            "stop_reason": "max_requests",
        }

    def test_rate_limit_mid_read_keeps_what_was_read(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch, _Account(throttle=("wants:2",)))
        result, _ = h.run(*_SEARCH, "--wanted", "--json")
        wanted = _owned(result, "wanted")
        assert wanted["@r1"] is True
        assert wanted["@r4"] is None
        assert json.loads(result.stdout)["wantlist"] == {
            "complete": False,
            "wants_read": 100,
            "total_wants": 250,
            "stop_reason": "rate_limited",
            "retry_after": 30,
        }
        text, _ = h.run(*_SEARCH, "--wanted")
        assert (
            "Wantlist: the read was rate limited, retry in 30s (100 of 250 wants "
            'read): "wanted ?" rows are not in the part read.'
        ) in text.stdout

    @pytest.mark.parametrize("throttle", ["identity", "wants:1"])
    def test_rate_limit_before_any_want_leaves_every_row_undecided(
        self, monkeypatch: pytest.MonkeyPatch, throttle: str
    ) -> None:
        h = _Harness(monkeypatch, _Account(throttle=(throttle,)))
        result, _ = h.run(*_SEARCH, "--wanted", "--json")
        assert set(_owned(result, "wanted").values()) == {None}
        wantlist = json.loads(result.stdout)["wantlist"]
        assert wantlist["wants_read"] == 0
        assert wantlist["stop_reason"] == "rate_limited"

    def test_a_rate_limited_listing_scan_reads_no_wantlist(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch, _Account(throttle=("search:2",)))
        result, sent = h.run("search", "x", "--limit", "10", "--wanted", "--json")
        assert sent == ["/database/search", "/database/search"]
        assert json.loads(result.stdout)["wantlist"]["stop_reason"] == "rate_limited"

    def test_a_rate_limited_collection_read_reads_no_wantlist(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch, _Account(throttle=("collection:1",)))
        result, sent = h.run(*_SEARCH, "--owned", "--wanted", "--json")
        assert _WANTS not in sent
        assert json.loads(result.stdout)["wantlist"]["stop_reason"] == "rate_limited"

    def test_a_page_without_release_rows_reads_no_wantlist(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DISCOGS_TOKEN", "test-token")
        api = _FakeAPI(monkeypatch, _empty_listing)
        result = CliRunner().invoke(cli, [*_SEARCH, "--wanted", "--json"])
        assert result.exit_code == 0, result.output
        assert [path for path, _ in api.sends] == ["/database/search"]
        assert "wantlist" not in json.loads(result.stdout)

    def test_cached_search_with_nothing_cached_is_a_miss(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch, _Account())
        h.run(*_SEARCH)  # only the listing is cached
        result, sent = h.run("--cached", *_SEARCH, "--wanted", "--json")
        assert json_error(result)["code"] == "cache_miss"
        assert sent == []

    def test_cached_listing_marks_send_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch, _Account())
        h.run("get", "versions", "@m1", "--json")
        result, sent = h.run("--cached", "get", "versions", "@m1", "--wanted", "--json")
        assert sent == []
        assert _owned(result, "wanted")["@r1"] is True

    def test_the_continuation_repeats_wanted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch, _Account())
        args = ("get", "versions", "@m1", "--release-type", "official")
        result, _ = h.run(*args, "--limit", "1", "--wanted")
        assert result.exit_code == 0, result.output
        footer = result.stdout.rstrip().splitlines()[-1]
        assert footer.startswith("Next page: agent-discogs get versions @m1")
        assert "--wanted" in footer

    def test_the_auth_message_names_wanted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch, _Account(), token=False)
        result, sent = h.run(*_SEARCH, "--wanted", "--json")
        assert json_error(result)["message"] == (
            "--wanted reads your own Discogs account, but no credentials are set."
        )
        assert sent == []

    @pytest.mark.parametrize(
        "argv",
        [
            "search artist x --wanted",
            "search label x --wanted",
            "search master x --wanted",
            "search release x --wanted --added-since 2024-01-01",
            "get versions @m1 --wanted --max-requests 3",
            "get releases @a1 --wanted --max-requests 3",
            "get release @r1 --wanted",
            "get owned @r1 --wanted",
            "get wantlist --wanted",
        ],
    )
    def test_misplaced_flags_fail_before_any_request(
        self, monkeypatch: pytest.MonkeyPatch, argv: str
    ) -> None:
        h = _Harness(monkeypatch, _Account())
        result, sent = h.run(*argv.split(), "--json")
        assert json_error(result)["code"] == "invalid_argument"
        assert sent == []

    def test_inventory_points_at_in_wantlist(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch, _Account())
        result, sent = h.run("get", "inventory", "seller", "--wanted", "--json")
        assert "--in-wantlist" in json_error(result)["message"]
        assert sent == []

    def test_a_search_master_error_points_at_versions(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch, _Account())
        result, _ = h.run("search", "master", "x", "--wanted", "--json")
        assert "get versions @m... --wanted" in json_error(result)["message"]


class TestNeedsAUser:
    @pytest.mark.parametrize("mode", ["none", "consumer"])
    @pytest.mark.parametrize(
        "argv",
        [
            [*_SEARCH, "--owned"],
            [*_SEARCH, "--wanted"],
            ["get", "versions", "@m1", "--owned"],
            ["get", "versions", "@m1", "--wanted"],
            ["get", "releases", "@a1", "--owned"],
            ["get", "releases", "@l1", "--wanted"],
            ["get", "owned", "@r1", "@r2"],
        ],
    )
    def test_fails_before_any_request(
        self, monkeypatch: pytest.MonkeyPatch, mode: str, argv: list[str]
    ) -> None:
        if mode == "consumer":
            monkeypatch.setenv("DISCOGS_CONSUMER_KEY", "key")
            monkeypatch.setenv("DISCOGS_CONSUMER_SECRET", "secret")
        h = _Harness(monkeypatch, _Account(), token=False)
        result, sent = h.run(*argv, "--json")
        error = json_error(result)
        assert error["code"] == "auth_required"
        assert "DISCOGS_TOKEN" in error["hint"]
        assert "OAuth" in error["hint"]
        assert sent == []

        result, sent = h.run(*argv)
        assert "DISCOGS_TOKEN" in text_error(result)
        assert sent == []


class TestGetOwned:
    def test_counts_and_folders_per_ref(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _Harness(monkeypatch, _Account())
        result, sent = h.run("get", "owned", "@r3", "@r1", "--json")
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout) == [
            {"ref": "@r3", "owned": 2, "folders": ["Vinyl", "Uncategorized"]},
            {"ref": "@r1", "owned": 0},
        ]
        assert sent == [
            "/oauth/identity",
            f"{_USER}/collection/releases/3",
            f"{_USER}/collection/folders",
            f"{_USER}/collection/releases/1",
        ]

        text, sent = h.run("get", "owned", "@r3", "@r1")
        assert sent == []
        assert text.stdout == (
            "@r3 owned \u00d72: Vinyl, Uncategorized\n\n@r1 not owned\n"
        )

    def test_a_copy_in_no_named_folder_has_no_folder(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch, _Account())
        result, _ = h.run("get", "owned", "@r6", "--json")
        assert json.loads(result.stdout) == {"ref": "@r6", "owned": 1}
        text, _ = h.run("get", "owned", "@r6")
        assert text.stdout == "@r6 owned\n"

    def test_one_ref_keeps_the_single_document(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch, _Account())
        result, sent = h.run("get", "owned", "2", "--json")
        assert json.loads(result.stdout) == {
            "ref": "@r2",
            "owned": 1,
            "folders": ["Uncategorized"],
        }
        assert len(sent) == 3
        text, _ = h.run("get", "owned", "2")
        assert text.stdout == "@r2 owned: Uncategorized\n"

    def test_full_gives_the_instances(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _Harness(monkeypatch, _Account())
        result, sent = h.run("get", "owned", "@r3", "--json", "--full")
        document = json.loads(result.stdout)
        assert [i["instance_id"] for i in document["instances"]] == [20, 150]
        assert f"{_USER}/collection/folders" not in sent

    def test_a_failing_ref_is_its_own_block(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch, _Account())
        result, _ = h.run("get", "owned", "@r2", "@a1", "--json")
        assert result.exit_code == 1
        first, second = json.loads(result.stdout)
        assert first["owned"] == 1
        assert second["error"]["code"] == "invalid_argument"
