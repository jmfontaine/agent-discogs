"""Tests for the `get collection` views that read the whole collection in
place of the listing (`--count-by`), against the real SDK client over a
mock transport."""

from __future__ import annotations

from typing import Any

import pytest

from tests.conftest import (
    _FOLDER_LIST,
    _RECORDS,
    _Collection,
    _document,
    _folder,
    _Harness,
    _pages,
    _paths,
    json_error,
)

_COMPLETE = {"complete": True, "records_read": 250, "total_records": 250}


def _record(n: int, **information: Any) -> dict[str, Any]:
    """Record `n`, with `information` replacing parts of its
    `basic_information`."""
    record = _Collection.record(n)
    basic = {**record["basic_information"], **information}
    return {**record, "basic_information": basic}


def _counts(result: Any) -> list[tuple[str, int]]:
    return [(c["value"], c["count"]) for c in _document(result)["counts"]]


class TestCountBy:
    def test_counts_the_whole_collection(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _Harness(monkeypatch)
        result, sent = h.run("get", "collection", "--count-by", "style", "--json")
        assert _paths(sent) == ["/oauth/identity", _RECORDS, _RECORDS, _RECORDS]
        assert [params for _, params in sent[1:]] == [
            {"page": str(page), "per_page": "100"} for page in (1, 2, 3)
        ]
        assert _document(result) == {
            "count_by": "style",
            "records": 250,
            "without_value": 0,
            "distinct": 2,
            "counts": [
                {"value": "House", "count": 238},
                {"value": "Techno", "count": 12},
            ],
            "collection": _COMPLETE,
        }

        text, sent = h.run("get", "collection", "--count-by", "style")
        assert sent == []
        assert text.exit_code == 0, text.output
        assert text.stdout == (
            "Collection: me by style (250 records)\n\nHouse: 238\nTechno: 12\n"
        )

    @pytest.mark.parametrize(
        ("key", "counts"),
        [
            ("genre", [("Electronic", 250)]),
            ("label", [("Label", 238), ("Tresor", 12)]),
            ("year", [("2001", 238), ("1992", 12)]),
            ("decade", [("2000s", 238), ("1990s", 12)]),
            ("artist", [("Artist", 250)]),
            ("format", [("Vinyl", 250)]),
        ],
    )
    def test_each_key(
        self,
        monkeypatch: pytest.MonkeyPatch,
        key: str,
        counts: list[tuple[str, int]],
    ) -> None:
        h = _Harness(monkeypatch)
        result, _ = h.run("get", "collection", "--count-by", key, "--json")
        assert _counts(result) == counts

    def test_artists_carry_their_ref(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _Harness(monkeypatch)
        result, _ = h.run("get", "collection", "--count-by", "artist", "--json")
        assert _document(result)["counts"] == [
            {"value": "Artist", "ref": "@a4242", "count": 250}
        ]
        text, _ = h.run("get", "collection", "--count-by", "artist")
        assert text.stdout.splitlines()[2:] == ["Artist [@a4242]: 250"]

    def test_a_record_counts_once_per_value(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        odd = {
            0: _record(0, genres=["Rock", "Jazz"]),
            1: _record(1, genres=None, year=0),
            2: _record(2, genres=[], year=None),
            3: _record(3, genres=["Jazz", "jazz"]),
        }
        h = _Harness(monkeypatch, _Collection(5, odd=odd))
        result, _ = h.run("get", "collection", "--count-by", "genre", "--json")
        document = _document(result)
        assert _counts(result) == [("Jazz", 2), ("Electronic", 1), ("Rock", 1)]
        assert document["records"] == 5
        assert document["without_value"] == 2
        assert sum(c["count"] for c in document["counts"]) == 4
        text, _ = h.run("get", "collection", "--count-by", "genre")
        assert text.stdout.splitlines()[-2:] == ["", "2 records have no genre"]

        for key in ("year", "decade"):
            result, _ = h.run("get", "collection", "--count-by", key, "--json")
            assert _document(result)["without_value"] == 2

        h = _Harness(monkeypatch, _Collection(2, odd={0: _record(0, styles=None)}))
        text, _ = h.run("get", "collection", "--count-by", "style")
        assert text.stdout.splitlines()[-1] == "1 record has no style"

    def test_formats_count_by_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cd = {"name": "CD", "qty": "1", "descriptions": ["Album"]}
        two = {"name": "Vinyl", "qty": "2", "descriptions": ["LP"]}
        odd = {0: _record(0, formats=[two, cd]), 1: _record(1, formats=[two, two])}
        h = _Harness(monkeypatch, _Collection(3, odd=odd))
        result, _ = h.run("get", "collection", "--count-by", "format", "--json")
        assert _counts(result) == [("Vinyl", 3), ("CD", 1)]

    def test_labels_and_artists_group_by_id(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        nothing = {"id": 647, "name": "Nothing Records", "catno": "1"}
        odd = {
            0: _record(0, labels=[nothing, {**nothing, "catno": "2"}]),
            1: _record(1, labels=[{**nothing, "name": "Nothing"}]),
            2: _record(2, labels=[{"name": "Self-released"}]),
            3: _record(3, labels=[{"name": "SELF-RELEASED"}]),
            4: _record(4, labels=[{"name": "Self-released", "id": 9}, {}, {"id": 8}]),
        }
        h = _Harness(monkeypatch, _Collection(5, odd=odd))
        result, _ = h.run("get", "collection", "--count-by", "label", "--json")
        assert _document(result)["counts"] == [
            {"value": "Nothing Records", "ref": "@l647", "count": 2},
            {"value": "Self-released", "count": 2},
            {"value": "@l8", "ref": "@l8", "count": 1},
            {"value": "Self-released", "ref": "@l9", "count": 1},
        ]
        text, _ = h.run("get", "collection", "--count-by", "label")
        assert text.stdout.splitlines()[2:6] == [
            "Nothing Records [@l647]: 2",
            "Self-released: 2",
            "@l8: 1",
            "Self-released [@l9]: 1",
        ]

        split = [
            {"id": 1, "name": "One", "join": "&"},
            {"id": 2, "name": "Two", "join": ""},
        ]
        h = _Harness(monkeypatch, _Collection(2, odd={0: _record(0, artists=split)}))
        result, _ = h.run("get", "collection", "--count-by", "artist", "--json")
        assert [c.get("ref") for c in _document(result)["counts"]] == [
            "@a4242",
            "@a1",
            "@a2",
        ]

    def test_ties_by_value_and_top(self, monkeypatch: pytest.MonkeyPatch) -> None:
        odd = {0: _record(0, styles=["Techno"]), 1: _record(1, styles=["Ambient"])}
        h = _Harness(monkeypatch, _Collection(3, odd=odd))
        result, _ = h.run("get", "collection", "--count-by", "style", "--json")
        assert _counts(result) == [("Ambient", 1), ("House", 1), ("Techno", 1)]

        h = _Harness(monkeypatch)
        argv = ["get", "collection", "--count-by", "style", "--top", "1"]
        result, _ = h.run(*argv, "--json")
        document = _document(result)
        assert _counts(result) == [("House", 238)]
        assert document["distinct"] == 2
        text, _ = h.run(*argv)
        assert text.stdout.splitlines()[2:] == ["House: 238", "… 1 more value"]
        assert "distinct" not in text.stdout

    def test_filters_narrow_what_is_counted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch)
        argv = ["get", "collection", "--style", "Techno", "--count-by", "label"]
        result, sent = h.run(*argv, "--json")
        assert _paths(sent) == ["/oauth/identity", _RECORDS, _RECORDS, _RECORDS]
        document = _document(result)
        assert _counts(result) == [("Tresor", 12)]
        assert document["records"] == 12
        assert document["collection"] == _COMPLETE
        text, _ = h.run(*argv)
        assert text.stdout.splitlines()[0] == (
            "Collection: me style=Techno by label (12 of 250 records)"
        )

        # A kept record counts all of its values.
        guest = [
            {"id": 4242, "name": "Artist", "join": "&"},
            {"id": 5, "name": "Guest", "join": ""},
        ]
        other = [{"id": 6, "name": "Other", "join": ""}]
        odd = {0: _record(0, artists=guest), 1: _record(1, artists=other)}
        h = _Harness(monkeypatch, _Collection(2, odd=odd))
        result, _ = h.run(
            "get", "collection", "--artist", "artist", "--count-by", "artist", "--json"
        )
        assert _counts(result) == [("Artist", 1), ("Guest", 1)]
        assert _document(result)["records"] == 1

    def test_one_folder(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _Harness(monkeypatch)
        argv = ["get", "collection", "--folder", "vinyl", "--count-by", "label"]
        result, sent = h.run(*argv, "--json")
        assert _paths(sent) == ["/oauth/identity", _FOLDER_LIST, _folder(5)]
        document = _document(result)
        assert _counts(result) == [("Label", 1)]
        assert document["collection"] == {
            "complete": True,
            "records_read": 1,
            "total_records": 1,
        }
        text, _ = h.run(*argv)
        assert text.stdout.splitlines()[0] == (
            "Collection: me folder=vinyl by label (1 record)"
        )

        result, sent = h.run(
            "get", "collection", "--folder", "Jazz", "--count-by", "label", "--json"
        )
        assert json_error(result)["code"] == "invalid_argument"
        assert sent == []  # the username and the folder list are cached

        # A cap the username spends resolves no folder.
        h = _Harness(monkeypatch)
        result, sent = h.run(*argv, "--max-requests", "1", "--json")
        assert _paths(sent) == ["/oauth/identity"]
        assert _document(result)["collection"]["total_records"] is None

    def test_max_requests_gives_partial_counts(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch)
        argv = ["get", "collection", "--count-by", "style", "--max-requests", "3"]
        result, sent = h.run(*argv, "--json")
        assert _paths(sent) == ["/oauth/identity", _RECORDS, _RECORDS]
        document = _document(result)
        assert _counts(result) == [("House", 192), ("Techno", 8)]
        assert document["records"] == 200
        assert document["collection"] == {
            "complete": False,
            "records_read": 200,
            "total_records": 250,
            "stop_reason": "max_requests",
        }

        again, sent = h.run(*argv)
        assert _pages(sent) == [3]
        assert again.stdout.splitlines()[0] == "Collection: me by style (250 records)"

        h = _Harness(monkeypatch)
        for _ in range(2):
            text, sent = h.run("--fresh", *argv)
            assert _paths(sent) == ["/oauth/identity", _RECORDS, _RECORDS]
            lines = text.stdout.splitlines()
            assert lines[0] == (
                "Collection: me by style (200 of 250 records; scan stopped at "
                "--max-requests 3 after 200 records)"
            )
            assert not [line for line in lines if line.startswith(("Next", "Cont"))]

    def test_a_cap_spent_before_the_scan(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _Harness(monkeypatch)
        argv = ["get", "collection", "--count-by", "genre", "--max-requests", "1"]
        result, sent = h.run(*argv, "--json")
        assert _paths(sent) == ["/oauth/identity"]
        document = _document(result)
        assert document["counts"] == []
        assert document["collection"] == {
            "complete": False,
            "records_read": 0,
            "total_records": None,
            "stop_reason": "max_requests",
        }
        text, _ = h.run("--fresh", *argv)
        assert text.stdout == (
            "Collection: me by genre "
            "(--max-requests 1 ran out before the scan started)\n"
        )

    def test_a_429_after_the_first_page_keeps_the_counts(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch, _Collection(throttle=("page:2",)))
        argv = ["get", "collection", "--count-by", "style"]
        result, _ = h.run(*argv, "--json")
        document = _document(result)
        assert _counts(result) == [("House", 96), ("Techno", 4)]
        assert document["collection"] == {
            "complete": False,
            "records_read": 100,
            "total_records": 250,
            "stop_reason": "rate_limited",
            "retry_after": 30,
        }
        text, _ = h.run("--fresh", *argv)
        assert text.exit_code == 0, text.output
        assert text.stdout.splitlines()[0] == (
            "Collection: me by style (100 of 250 records; scan stopped: rate "
            "limited, retry in 30s, after 100 records)"
        )

    @pytest.mark.parametrize("throttle", ["identity", "page:1"])
    def test_a_429_before_any_record_fails(
        self, monkeypatch: pytest.MonkeyPatch, throttle: str
    ) -> None:
        h = _Harness(monkeypatch, _Collection(throttle=(throttle,)))
        result, _ = h.run("get", "collection", "--count-by", "genre", "--json")
        assert json_error(result)["code"] == "rate_limited"

    def test_an_empty_collection(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _Harness(monkeypatch, _Collection(0))
        result, _ = h.run("get", "collection", "--count-by", "genre", "--json")
        document = _document(result)
        assert document["counts"] == []
        assert document["records"] == 0
        assert document["collection"]["complete"] is True
        text, _ = h.run("get", "collection", "--count-by", "genre")
        assert text.stdout == "Collection: me by genre (0 records)\n"

    def test_the_cache(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _Harness(monkeypatch)
        argv = ["get", "collection", "--count-by", "genre"]
        result, sent = h.run("--cached", *argv, "--json")
        assert json_error(result)["code"] == "cache_miss"
        assert sent == []

        _, sent = h.run("search", "release", "x", "--release-type", "all", "--owned")
        assert _pages(sent) == [1, 2, 3]
        result, sent = h.run(*argv)
        assert result.exit_code == 0, result.output
        assert sent == []

    def test_an_unknown_key_is_a_usage_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch)
        result, sent = h.run("get", "collection", "--count-by", "price")
        assert result.exit_code == 2
        assert sent == []

    def test_max_requests_alone_names_count_by(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch)
        result, sent = h.run("get", "collection", "--max-requests", "5", "--json")
        error = json_error(result)
        assert error["code"] == "invalid_argument"
        assert "--count-by" in error["message"]
        assert sent == []
