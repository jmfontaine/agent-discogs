"""Tests for `get collection --random`, which draws records from the
collection in place of the listing, against the real SDK client over a mock
transport."""

from __future__ import annotations

import random

import pytest

from tests.conftest import (
    _FIELDS,
    _FOLDER_LIST,
    _RECORDS,
    _TECHNO,
    _TECHNO_REFS,
    _Collection,
    _document,
    _folder,
    _Harness,
    _pages,
    _paths,
    _refs,
    json_error,
    text_error,
)


def _draw(monkeypatch: pytest.MonkeyPatch, *positions: int) -> list[tuple[int, int]]:
    """Make the draw pick `positions`, in that order. Returns the population
    size and the count of each draw."""
    calls: list[tuple[int, int]] = []

    def sample(population: range, k: int) -> list[int]:
        calls.append((len(population), k))
        return list(positions)

    monkeypatch.setattr(random, "sample", sample)
    return calls


def _ref(n: int) -> str:
    """The ref of record `n` (records 3 and 120 are release 7)."""
    return f"@r{_Collection.record(n)['id']}"


def _with(n: int, **information: object) -> dict[str, object]:
    """Record `n`, with `information` replacing parts of its
    `basic_information`."""
    record = _Collection.record(n)
    return {**record, "basic_information": record["basic_information"] | information}


class TestUnfiltered:
    def test_reads_page_one_then_the_page_of_the_pick(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = _draw(monkeypatch, 120)
        h = _Harness(monkeypatch)
        result, sent = h.run("get", "collection", "--random", "--json")
        assert _paths(sent) == ["/oauth/identity", _FOLDER_LIST, _RECORDS, _RECORDS]
        assert _pages(sent) == [1, 2]
        for _, params in sent[2:]:
            assert params["per_page"] == "100"
            assert "sort" not in params
        assert calls == [(250, 1)]
        document = _document(result)
        assert list(document) == ["random", "results"]
        assert document["random"] == {"requested": 1, "population": 250}
        [row] = document["results"]
        assert row["ref"] == "@r7"
        assert row["title"] == "Artist - Record 120"
        assert row["folder"] == "Uncategorized"

        text, _ = h.run("get", "collection", "--random")
        lines = text.stdout.splitlines()
        assert lines[0] == "Collection: me random 1 (1 of 250 records)"
        assert lines[1] == ""
        assert lines[2].startswith('@r7 "Artist - Record 120" 2001 · Label CAT120')
        assert len(lines) == 3

    @pytest.mark.parametrize("argv", [["--random"], ["--random", "1"]])
    def test_a_pick_on_page_one_reads_no_other_page(
        self, monkeypatch: pytest.MonkeyPatch, argv: list[str]
    ) -> None:
        _draw(monkeypatch, 5)
        h = _Harness(monkeypatch)
        result, sent = h.run("get", "collection", *argv, "--json")
        assert _pages(sent) == [1]
        assert _refs(result) == [_ref(5)]

    def test_rows_in_collection_order_and_each_page_once(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = _draw(monkeypatch, 130, 5, 7, 140)
        h = _Harness(monkeypatch)
        result, sent = h.run("get", "collection", "--random", "4", "--json")
        assert calls == [(250, 4)]
        assert _pages(sent) == [1, 2]
        assert _refs(result) == [_ref(5), _ref(7), _ref(130), _ref(140)]

        _draw(monkeypatch, 205, 140, 120)
        result, sent = h.run("--fresh", "get", "collection", "--random", "3", "--json")
        assert _pages(sent) == [1, 2, 3]
        assert _refs(result) == ["@r7", _ref(140), _ref(205)]

    def test_fewer_records_than_asked_prints_them_all(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch, _Collection(size=10))
        text, sent = h.run("get", "collection", "--random", "50")
        assert _paths(sent) == ["/oauth/identity", _FOLDER_LIST, _RECORDS]
        lines = text.stdout.splitlines()
        assert lines[0] == "Collection: me random 50 (10 of 10 records)"
        assert len(lines) == 12

    def test_the_real_draw(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _Harness(monkeypatch)
        result, _ = h.run("get", "collection", "--random", "5", "--json")
        rows = _document(result)["results"]
        assert len({row["title"] for row in rows}) == 5
        numbers = [int(row["title"].rsplit(" ", 1)[1]) for row in rows]
        assert numbers == sorted(numbers)

    def test_an_empty_collection(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _Harness(monkeypatch, _Collection(size=0))
        result, sent = h.run("get", "collection", "--random", "--json")
        assert _paths(sent) == ["/oauth/identity", _FOLDER_LIST, _RECORDS]
        assert _document(result) == {
            "random": {"requested": 1, "population": 0},
            "results": [],
        }
        text, _ = h.run("get", "collection", "--random")
        assert text.stdout == "Collection: me random 1 (0 of 0 records)\n"

    @pytest.mark.parametrize("throttle", ["identity", "folders", "page:1", "page:2"])
    def test_a_429_on_any_read_fails(
        self, monkeypatch: pytest.MonkeyPatch, throttle: str
    ) -> None:
        _draw(monkeypatch, 120)
        h = _Harness(monkeypatch, _Collection(throttle=(throttle,)))
        result, _ = h.run("get", "collection", "--random", "--json")
        assert json_error(result)["code"] == "rate_limited"
        text, _ = h.run("get", "collection", "--random")
        text_error(text)

    def test_full(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _draw(monkeypatch, 3)
        h = _Harness(monkeypatch)
        result, sent = h.run("get", "collection", "--random", "--json", "--full")
        assert _FOLDER_LIST not in _paths(sent)
        [row] = _document(result)["results"]
        assert row["instance_id"] == 10_003
        assert row["folder_id"] == 5

    def test_the_cache(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _Harness(monkeypatch)
        result, sent = h.run("--cached", "get", "collection", "--random", "--json")
        assert json_error(result)["code"] == "cache_miss"
        assert sent == []

        h.run("search", "release", "x", "--release-type", "all", "--owned")
        _draw(monkeypatch, 249)
        result, sent = h.run("get", "collection", "--random", "--json")
        assert _paths(sent) == [_FOLDER_LIST]
        assert _refs(result) == [_ref(249)]

    def test_a_cached_draw_needs_its_page(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch)
        _draw(monkeypatch, 120)
        h.run("get", "collection", "--random", "--json")
        _draw(monkeypatch, 205)
        result, sent = h.run("--cached", "get", "collection", "--random", "--json")
        assert json_error(result)["code"] == "cache_miss"
        assert sent == []
        _draw(monkeypatch, 5)
        result, sent = h.run("--cached", "get", "collection", "--random", "--json")
        assert _refs(result) == [_ref(5)]
        assert sent == []

    def test_a_page_shorter_than_its_total_drops_the_pick(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Page 1 is cached at 250 records; the collection then shrinks to
        # 150, so page 3 comes back empty and the pick on it is dropped.
        collection = _Collection()
        h = _Harness(monkeypatch, collection)
        _draw(monkeypatch, 5)
        h.run("get", "collection", "--random", "--json")
        collection.size = 150
        _draw(monkeypatch, 5, 220)
        result, sent = h.run("get", "collection", "--random", "2", "--json")
        assert _pages(sent) == [3]
        assert _document(result)["random"] == {"requested": 2, "population": 250}
        assert _refs(result) == [_ref(5)]


class TestFiltered:
    def test_draws_from_the_whole_scan(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = _draw(monkeypatch, 0, 7)
        h = _Harness(monkeypatch)
        argv = ["get", "collection", "--style", "Techno", "--random", "2"]
        result, sent = h.run(*argv, "--json")
        assert _paths(sent) == ["/oauth/identity", _FOLDER_LIST, *[_RECORDS] * 3]
        assert calls == [(12, 2)]
        document = _document(result)
        assert list(document) == ["random", "results", "collection"]
        assert document["random"] == {"requested": 2, "population": 12}
        assert document["collection"] == {
            "complete": True,
            "records_read": 250,
            "total_records": 250,
        }
        assert _refs(result) == [_TECHNO_REFS[0], _TECHNO_REFS[7]]

        text, _ = h.run(*argv)
        lines = text.stdout.splitlines()
        assert lines[0] == (
            "Collection: me style=Techno random 2 (2 of 12 matching records)"
        )
        assert lines[1] == ""
        assert len(lines) == 4

    def test_fewer_matches_than_asked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _Harness(monkeypatch)
        argv = ["get", "collection", "--style", "Techno", "--random", "50", "--json"]
        result, _ = h.run(*argv)
        assert _refs(result) == _TECHNO_REFS

    def test_no_match(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _Harness(monkeypatch)
        argv = ["get", "collection", "--genre", "Jazz", "--random"]
        result, _ = h.run(*argv, "--json")
        document = _document(result)
        assert document["random"] == {"requested": 1, "population": 0}
        assert document["results"] == []
        text, _ = h.run(*argv)
        assert text.stdout == (
            "Collection: me genre=Jazz random 1 (0 of 0 matching records)\n"
        )

    def test_a_capped_scan_draws_from_the_part_read(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = _draw(monkeypatch, 1)
        h = _Harness(monkeypatch)
        argv = ["get", "collection", "--style", "Techno", "--random"]
        result, sent = h.run(*argv, "--max-requests", "3", "--json")
        assert _paths(sent) == ["/oauth/identity", _FOLDER_LIST, _RECORDS]
        assert calls == [(4, 1)]
        document = _document(result)
        assert document["random"] == {"requested": 1, "population": 4}
        assert document["results"][0]["ref"] == _ref(_TECHNO[1])
        assert document["collection"] == {
            "complete": False,
            "records_read": 100,
            "total_records": 250,
            "stop_reason": "max_requests",
        }
        text, _ = h.run("--fresh", *argv, "--max-requests", "3")
        assert text.stdout.splitlines()[0] == (
            "Collection: me style=Techno random 1 (1 of 4 matching records; "
            "scan stopped at --max-requests 3 after 100 records)"
        )

    @pytest.mark.parametrize(
        ("cap", "paths"),
        [("2", ["/oauth/identity", _FOLDER_LIST]), ("1", ["/oauth/identity"])],
    )
    def test_a_cap_spent_before_the_scan(
        self, monkeypatch: pytest.MonkeyPatch, cap: str, paths: list[str]
    ) -> None:
        h = _Harness(monkeypatch)
        argv = ["get", "collection", "--style", "Techno", "--random"]
        text, sent = h.run(*argv, "--max-requests", cap)
        assert _paths(sent) == paths
        assert text.stdout == (
            "Collection: me style=Techno random 1 "
            f"(--max-requests {cap} ran out before the scan started)\n"
        )
        result, _ = h.run("--fresh", *argv, "--max-requests", cap, "--json")
        document = _document(result)
        assert document["results"] == []
        assert document["collection"]["total_records"] is None

    def test_a_429_after_the_first_page(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _draw(monkeypatch, 0)
        h = _Harness(monkeypatch, _Collection(throttle=("page:2",)))
        argv = ["get", "collection", "--style", "Techno", "--random"]
        result, _ = h.run(*argv, "--json")
        document = _document(result)
        assert document["random"] == {"requested": 1, "population": 4}
        assert document["collection"]["stop_reason"] == "rate_limited"
        assert document["collection"]["retry_after"] == 30
        text, _ = h.run("--fresh", *argv)
        assert text.stdout.splitlines()[0].endswith(
            "(1 of 4 matching records; scan stopped: rate limited, retry in 30s, "
            "after 100 records)"
        )

    @pytest.mark.parametrize("throttle", ["identity", "folders", "page:1"])
    def test_a_429_before_any_record_fails(
        self, monkeypatch: pytest.MonkeyPatch, throttle: str
    ) -> None:
        h = _Harness(monkeypatch, _Collection(throttle=(throttle,)))
        argv = ["get", "collection", "--style", "Techno", "--random", "--json"]
        assert json_error(h.run(*argv)[0])["code"] == "rate_limited"

    @pytest.mark.parametrize(
        ("argv", "kept"),
        [(["--artist", "guest"], [20, 150]), (["--title", "blue*"], [30, 140, 160])],
    )
    def test_a_filter_on_artist_or_title(
        self, monkeypatch: pytest.MonkeyPatch, argv: list[str], kept: list[int]
    ) -> None:
        # Records 20 and 150 credit Guest beside Artist; records 30, 140
        # and 160 have a Blue title.
        guest = [
            {"id": 4242, "name": "Artist", "join": "&"},
            {"id": 5, "name": "Guest", "join": ""},
        ]
        odd = {n: _with(n, artists=guest) for n in (20, 150)}
        odd |= {n: _with(n, title=f"Blue {n}") for n in (30, 140, 160)}
        calls = _draw(monkeypatch, 1)
        h = _Harness(monkeypatch, _Collection(odd=odd))
        result, sent = h.run("get", "collection", *argv, "--random", "--json")
        assert _paths(sent) == ["/oauth/identity", _FOLDER_LIST, *[_RECORDS] * 3]
        assert calls == [(len(kept), 1)]
        document = _document(result)
        assert document["random"] == {"requested": 1, "population": len(kept)}
        assert _refs(result) == [_ref(kept[1])]
        assert document["collection"]["complete"] is True

        _draw(monkeypatch, *range(len(kept)))
        result, _ = h.run("get", "collection", *argv, "--random", "50", "--json")
        assert _refs(result) == [_ref(n) for n in kept]


class TestFolderAndFields:
    def test_one_folder(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Records 20 to 22 are in To Sell (folder 7).
        odd = {n: {**_Collection.record(n), "folder_id": 7} for n in (20, 21, 22)}
        calls = _draw(monkeypatch, 1)
        h = _Harness(monkeypatch, _Collection(odd=odd))
        argv = ["get", "collection", "--folder", "to sell", "--random"]
        result, sent = h.run(*argv, "--json", "--full")
        assert _paths(sent) == ["/oauth/identity", _FOLDER_LIST, _folder(7)]
        assert calls == [(3, 1)]
        assert [row["id"] for row in _document(result)["results"]] == [1021]
        text, _ = h.run(*argv)
        assert text.stdout.splitlines()[0] == (
            'Collection: me folder="to sell" random 1 (1 of 3 records)'
        )

        result, sent = h.run(
            "--fresh", "get", "collection", "--folder", "Jazz", "--random", "--json"
        )
        assert json_error(result)["code"] == "invalid_argument"
        assert _paths(sent) == ["/oauth/identity", _FOLDER_LIST]

    def test_one_folder_with_a_filter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Records 0 to 149 are in To Sell: two pages, 6 of them Techno.
        odd = {n: {**_Collection.record(n), "folder_id": 7} for n in range(150)}
        techno = [n for n in _TECHNO if n < 150]
        calls = _draw(monkeypatch, 4)
        h = _Harness(monkeypatch, _Collection(odd=odd))
        argv = ["get", "collection", "--folder", "To Sell", "--style", "Techno"]
        result, sent = h.run(*argv, "--random", "--json")
        # 2 + ceil(150 / 100) requests: the username, the folder list and
        # the folder's two pages, nothing of the other folders.
        assert _paths(sent) == [
            "/oauth/identity",
            _FOLDER_LIST,
            _folder(7),
            _folder(7),
        ]
        assert calls == [(len(techno), 1)]
        document = _document(result)
        assert document["random"] == {"requested": 1, "population": 6}
        assert _refs(result) == [_ref(techno[4])]
        assert document["collection"] == {
            "complete": True,
            "records_read": 150,
            "total_records": 150,
        }
        text, _ = h.run(*argv, "--random")
        assert text.stdout.splitlines()[0] == (
            'Collection: me folder="To Sell" style=Techno random 1 '
            "(1 of 6 matching records)"
        )

    def test_fields(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _draw(monkeypatch, 3)
        h = _Harness(monkeypatch)
        result, sent = h.run("get", "collection", "--random", "--fields", "--json")
        assert _paths(sent) == ["/oauth/identity", _FOLDER_LIST, _FIELDS, _RECORDS]
        [row] = _document(result)["results"]
        assert row["fields"] == {"Media Condition": "VG+", "Notes": "Shelf A3"}
        text, _ = h.run("get", "collection", "--random", "--fields")
        assert text.stdout.splitlines()[2].endswith(
            "· Media Condition: VG+ · Notes: Shelf A3"
        )

    def test_a_filtered_draw_with_fields_counts_the_fields_list(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch)
        argv = ["get", "collection", "--style", "Techno", "--random", "--fields"]
        result, sent = h.run(*argv, "--max-requests", "3", "--json")
        assert _paths(sent) == ["/oauth/identity", _FOLDER_LIST, _FIELDS]
        assert _document(result)["results"] == []


def test_a_ref_after_random_is_a_usage_error(monkeypatch: pytest.MonkeyPatch) -> None:
    h = _Harness(monkeypatch)
    result, sent = h.run("get", "collection", "--random", "@r1")
    assert result.exit_code == 2
    assert sent == []


@pytest.mark.parametrize(
    ("argv", "given"),
    [
        (["--random", "0"], "0"),
        (["--random", "-1"], "-1"),
        (["--random=-5"], "-5"),
        (["--random", "101"], "101"),
    ],
)
def test_a_bound_names_the_value(
    monkeypatch: pytest.MonkeyPatch, argv: list[str], given: str
) -> None:
    h = _Harness(monkeypatch)
    result, sent = h.run("get", "collection", *argv)
    assert text_error(result).startswith(
        f"✗ --random must be between 1 and 100 ({given} given)."
    )
    assert sent == []


def test_a_negative_number_after_another_flag_is_left_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = _Harness(monkeypatch)
    result, _ = h.run("get", "collection", "--limit", "-1", "--random")
    assert "--limit" in text_error(result)


def test_a_negative_count_after_an_option_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `--` is the value of --title here, not the end of the options.
    h = _Harness(monkeypatch)
    result, sent = h.run("get", "collection", "--title", "--", "--random", "-1")
    assert "(-1 given)" in text_error(result)
    assert sent == []


def test_an_option_value_is_never_rewritten(monkeypatch: pytest.MonkeyPatch) -> None:
    h = _Harness(monkeypatch)
    result, _ = h.run("get", "collection", "--label", "--random=-1", "--limit", "1")
    assert result.exit_code == 0, result.output
    assert result.stdout.startswith("Collection: me label=--random=-1 ")
