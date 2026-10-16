"""Tests for the `get collection` views that read the whole collection in
place of the listing (`--count-by`, `--duplicates`), against the real SDK
client over a mock transport."""

from __future__ import annotations

from typing import Any

import pytest

from tests.conftest import (
    _FIELDS,
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


def _copy(n: int, release: int, folder: int, **information: Any) -> dict[str, Any]:
    """Record `n` as a copy of release `release` in folder `folder`, with
    `information` replacing parts of its `basic_information`."""
    record = _record(n, id=release, **information)
    return {**record, "id": release, "folder_id": folder}


def _groups(result: Any) -> list[tuple[str, int]]:
    return [(g["ref"], g["copies"]) for g in _document(result)["groups"]]


# Record 50 is release 1050 of master 60, beside release 7 (records 3 and
# 120), and a Jazz record; records 60 and 61 name master 0, which is none.
_MASTER_ODD = {
    50: _record(50, master_id=60, genres=["Jazz"]),
    60: _record(60, master_id=0),
    61: _record(61, master_id=0),
}

_RELEASE_7 = {
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
    "copies": 2,
    "folders": ["Vinyl", "Uncategorized"],
}
_ROW_7 = '@r7 "Artist - Record 3" 2001 · Label CAT3 · Vinyl (LP) · owned \u00d72'
_PARTIAL = "Copies and groups may be missing: only part of the collection was read."


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

    def test_a_title_filter_counts_all_of_a_kept_record(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        odd = {
            0: _record(0, title="Blue Train", genres=["Jazz", "Blues"]),
            1: _record(1, title="Blue Monday", genres=["Electronic"]),
            2: _record(2, title="Train", genres=["Rock"]),
        }
        h = _Harness(monkeypatch, _Collection(4, odd=odd))
        argv = ["get", "collection", "--title", "blue*", "--count-by", "genre"]
        result, _ = h.run(*argv, "--json")
        assert _counts(result) == [("Blues", 1), ("Electronic", 1), ("Jazz", 1)]
        assert _document(result)["records"] == 2
        text, _ = h.run(*argv)
        assert text.stdout.splitlines()[0] == (
            "Collection: me title=blue* by genre (2 of 4 records)"
        )

    def test_one_folder_with_a_filter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Techno record 5 is moved to To Sell, so Uncategorized holds 248
        # records, 11 of them Techno, on three pages.
        moved = {**_Collection.record(5), "folder_id": 7}
        h = _Harness(monkeypatch, _Collection(odd={5: moved}))
        argv = ["get", "collection", "--folder", "Uncategorized", "--style", "Techno"]
        result, sent = h.run(*argv, "--count-by", "label", "--json")
        assert _paths(sent) == [
            "/oauth/identity",
            _FOLDER_LIST,
            _folder(1),
            _folder(1),
            _folder(1),
        ]
        assert [params["page"] for _, params in sent[2:]] == ["1", "2", "3"]
        document = _document(result)
        assert _counts(result) == [("Tresor", 11)]
        assert document["records"] == 11
        assert document["collection"] == {
            "complete": True,
            "records_read": 248,
            "total_records": 248,
        }
        text, _ = h.run(*argv, "--count-by", "label")
        assert text.stdout.splitlines()[0] == (
            "Collection: me folder=Uncategorized style=Techno by label "
            "(11 of 248 records)"
        )

    def test_a_folder_and_a_cap_spent_before_the_scan(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch)
        argv = ["get", "collection", "--folder", "Vinyl", "--count-by", "label"]
        text, sent = h.run(*argv, "--max-requests", "2")
        assert _paths(sent) == ["/oauth/identity", _FOLDER_LIST]
        assert text.exit_code == 0, text.output
        assert text.stdout == (
            "Collection: me folder=Vinyl by label "
            "(--max-requests 2 ran out before the scan started)\n"
        )
        result, sent = h.run("--fresh", *argv, "--max-requests", "2", "--json")
        assert _paths(sent) == ["/oauth/identity", _FOLDER_LIST]
        assert _document(result)["collection"] == {
            "complete": False,
            "records_read": 0,
            "total_records": None,
            "stop_reason": "max_requests",
        }

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


class TestDuplicates:
    def test_release_duplicates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _Harness(monkeypatch)
        argv = ["get", "collection", "--duplicates", "release"]
        result, sent = h.run(*argv, "--json")
        assert _paths(sent) == ["/oauth/identity", _FOLDER_LIST, *[_RECORDS] * 3]
        assert _document(result) == {
            "duplicates": "release",
            "groups": [_RELEASE_7],
            "records": 250,
            "collection": _COMPLETE,
        }
        text, sent = h.run(*argv)
        assert sent == []
        assert text.stdout == (
            "Duplicates: me by release (1 release owned more than once, 2 copies, "
            "in 250 records)\n\n"
            f"{_ROW_7} · folders Vinyl, Uncategorized\n"
        )

    def test_largest_group_first_and_folders_once(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Release 9: three copies, two in Uncategorized and one in To Sell.
        odd = {20: _copy(20, 9, 1), 21: _copy(21, 9, 1), 22: _copy(22, 9, 7)}
        h = _Harness(monkeypatch, _Collection(odd=odd))
        argv = ["get", "collection", "--duplicates", "release"]
        result, _ = h.run(*argv, "--json")
        assert _groups(result) == [("@r9", 3), ("@r7", 2)]
        assert _document(result)["groups"][0]["folders"] == ["Uncategorized", "To Sell"]
        text, _ = h.run(*argv)
        lines = text.stdout.splitlines()
        assert lines[0] == (
            "Duplicates: me by release (2 releases owned more than once, 5 copies, "
            "in 250 records)"
        )
        assert lines[2].endswith("· owned \u00d73 · folders Uncategorized, To Sell")
        assert lines[3] == f"{_ROW_7} · folders Vinyl, Uncategorized"
        assert len(lines) == 4

    def test_ties_follow_the_collection_not_the_ids(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Release 2 (records 5 and 6) ties release 7 (records 3 and 120),
        # whose first copy comes first; master 70 lists release 9000
        # (record 4) before release 8000 (record 8).
        odd = {
            4: _copy(4, 9000, 1, master_id=70),
            5: _copy(5, 2, 1),
            6: _copy(6, 2, 1),
            8: _copy(8, 8000, 1, master_id=70),
        }
        h = _Harness(monkeypatch, _Collection(odd=odd))
        argv = ["get", "collection", "--json", "--duplicates"]
        releases, _ = h.run(*argv, "release")
        assert _groups(releases) == [("@r7", 2), ("@r2", 2)]
        masters, _ = h.run(*argv, "master")
        groups = _document(masters)["groups"]
        assert [g["ref"] for g in groups] == ["@m60", "@m70", "@m61"]
        assert [r["ref"] for r in groups[1]["releases"]] == ["@r9000", "@r8000"]

    def test_master_duplicates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _Harness(monkeypatch, _Collection(odd=_MASTER_ODD))
        argv = ["get", "collection", "--duplicates", "master"]
        result, _ = h.run(*argv, "--json")
        document = _document(result)
        assert _groups(result) == [("@m60", 3), ("@m61", 2)]
        m60, m61 = document["groups"]
        assert [(r["ref"], r["copies"]) for r in m60["releases"]] == [
            ("@r7", 2),
            ("@r1050", 1),
        ]
        assert m60["releases"][0] == _RELEASE_7
        assert m60["releases"][1]["folders"] == ["Uncategorized"]
        assert [(r["ref"], r["copies"]) for r in m61["releases"]] == [
            ("@r1011", 1),
            ("@r1012", 1),
        ]
        assert document["records"] == 250
        # Records 60 and 61 name master 0: no master, and no group.
        assert document["without_master"] == 245
        assert list(document) == [
            "duplicates",
            "groups",
            "records",
            "without_master",
            "collection",
        ]

        text, _ = h.run(*argv)
        lines = text.stdout.splitlines()
        assert lines[0] == (
            "Duplicates: me by master (2 masters owned more than once, 5 copies, "
            "in 250 records)"
        )
        assert lines[2] == "@m60 owned \u00d73 in 2 releases"
        assert lines[3] == f"  {_ROW_7} · folders Vinyl, Uncategorized"
        assert lines[4] == (
            '  @r1050 "Artist - Record 50" 2001 · Label CAT50 · Vinyl (LP) '
            "· owned · folder Uncategorized"
        )
        assert lines[5] == "@m61 owned \u00d72 in 2 releases"
        assert lines[-2:] == [
            "",
            (
                "245 records have no master and are not checked (--duplicates "
                "release looks at their copies)."
            ),
        ]

    def test_filters_choose_the_records_checked(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch, _Collection(odd=_MASTER_ODD))
        base = ["get", "collection", "--json"]
        result, _ = h.run(*base, "--genre", "Jazz", "--duplicates", "master")
        assert _document(result)["groups"] == []
        assert _document(result)["records"] == 1
        result, _ = h.run(*base, "--genre", "Electronic", "--duplicates", "master")
        assert _groups(result) == [("@m60", 2), ("@m61", 2)]

        result, sent = h.run(
            "--fresh", *base, "--style", "Techno", "--duplicates", "release"
        )
        assert len(sent) == 5
        document = _document(result)
        assert document["records"] == 12
        assert document["collection"]["records_read"] == 250
        text, _ = h.run(
            "get", "collection", "--style", "Techno", "--duplicates", "release"
        )
        assert text.stdout == (
            "Duplicates: me style=Techno by release (none in 12 of 250 records)\n"
        )

    def test_one_record_line(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _Harness(monkeypatch, _Collection(odd=_MASTER_ODD))
        argv = ["get", "collection", "--title", "Record 60", "--duplicates", "master"]
        text, _ = h.run(*argv)
        # The one record checked, record 60, names master 0: no master.
        assert text.stdout == (
            'Duplicates: me title="Record 60" by master (none in 1 of 250 records)\n'
            "\n"
            "1 record has no master and is not checked (--duplicates release "
            "looks at their copies).\n"
        )

    def test_no_duplicates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Record 120, the second copy of release 7, is past a 100-record collection.
        h = _Harness(monkeypatch, _Collection(100))
        result, _ = h.run("get", "collection", "--duplicates", "release", "--json")
        assert _document(result)["groups"] == []
        text, _ = h.run("get", "collection", "--duplicates", "release")
        assert text.stdout == "Duplicates: me by release (none in 100 records)\n"

    def test_a_capped_read(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _Harness(monkeypatch)
        argv = ["get", "collection", "--duplicates", "release", "--max-requests", "3"]
        result, sent = h.run(*argv, "--json")
        assert _paths(sent) == ["/oauth/identity", _FOLDER_LIST, _RECORDS]
        document = _document(result)
        assert document["groups"] == []
        assert document["collection"] == {
            "complete": False,
            "records_read": 100,
            "total_records": 250,
            "stop_reason": "max_requests",
        }
        # The same cap again: the three cached requests are free.
        result, sent = h.run(*argv, "--json")
        assert _pages(sent) == [2, 3]
        assert _groups(result) == [("@r7", 2)]
        assert _document(result)["collection"] == _COMPLETE

        text, _ = h.run("--fresh", *argv)
        assert text.stdout == (
            "Duplicates: me by release (none found so far in 100 of 250 records; "
            "scan stopped at --max-requests 3 after 100 records)\n\n"
            f"{_PARTIAL}\n"
        )

    @pytest.mark.parametrize(
        ("cap", "paths"),
        [("2", ["/oauth/identity", _FOLDER_LIST]), ("1", ["/oauth/identity"])],
    )
    def test_a_cap_spent_before_the_scan(
        self, monkeypatch: pytest.MonkeyPatch, cap: str, paths: list[str]
    ) -> None:
        h = _Harness(monkeypatch)
        argv = ["get", "collection", "--duplicates", "release", "--max-requests", cap]
        text, sent = h.run(*argv)
        assert _paths(sent) == paths
        assert text.stdout == (
            "Duplicates: me by release "
            f"(--max-requests {cap} ran out before the scan started)\n\n{_PARTIAL}\n"
        )

    def test_a_429_after_the_first_page(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _Harness(monkeypatch, _Collection(throttle=("page:2",), odd=_MASTER_ODD))
        result, _ = h.run("get", "collection", "--duplicates", "release", "--json")
        assert result.exit_code == 0, result.output
        document = _document(result)
        assert document["groups"] == []
        assert document["collection"] == {
            "complete": False,
            "records_read": 100,
            "total_records": 250,
            "stop_reason": "rate_limited",
            "retry_after": 30,
        }
        text, _ = h.run("--fresh", "get", "collection", "--duplicates", "release")
        assert "none in" not in text.stdout
        assert text.stdout.splitlines()[0].endswith(
            "scan stopped: rate limited, retry in 30s, after 100 records)"
        )
        assert text.stdout.splitlines()[-1] == _PARTIAL

        # Master 60: records 3 and 50 are read, record 120 is not.
        result, _ = h.run("get", "collection", "--duplicates", "master", "--json")
        assert _groups(result) == [("@m60", 2), ("@m61", 2)]
        m60 = _document(result)["groups"][0]
        assert [(r["ref"], r["copies"]) for r in m60["releases"]] == [
            ("@r7", 1),
            ("@r1050", 1),
        ]

    @pytest.mark.parametrize("throttle", ["identity", "folders", "page:1"])
    def test_a_429_before_any_record_fails(
        self, monkeypatch: pytest.MonkeyPatch, throttle: str
    ) -> None:
        h = _Harness(monkeypatch, _Collection(throttle=(throttle,)))
        result, _ = h.run("get", "collection", "--duplicates", "release", "--json")
        assert json_error(result)["code"] == "rate_limited"

    def test_full(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _Harness(monkeypatch, _Collection(odd=_MASTER_ODD))
        argv = ["get", "collection", "--json", "--full", "--duplicates"]
        result, sent = h.run(*argv, "release")
        assert _FOLDER_LIST not in _paths(sent)
        document = _document(result)
        assert list(document) == ["duplicates", "groups", "records", "collection"]
        (group,) = document["groups"]
        assert group["ref"] == "@r7"
        assert [i["instance_id"] for i in group["instances"]] == [10_003, 10_120]
        assert group["instances"][0]["folder_id"] == 5

        result, _ = h.run(*argv, "master")
        document = _document(result)
        assert document["without_master"] == 245
        assert [
            (g["ref"], [i["instance_id"] for i in g["instances"]])
            for g in document["groups"]
        ] == [("@m60", [10_003, 10_050, 10_120]), ("@m61", [10_011, 10_012])]

    def test_one_folder(self, monkeypatch: pytest.MonkeyPatch) -> None:
        odd = {20: _copy(20, 9, 7), 21: _copy(21, 9, 7), 22: _copy(22, 7, 7)}
        h = _Harness(monkeypatch, _Collection(odd=odd))
        argv = ["get", "collection", "--folder", "to sell", "--duplicates", "release"]
        result, sent = h.run(*argv, "--json", "--full")
        assert _paths(sent) == ["/oauth/identity", _FOLDER_LIST, _folder(7)]
        # Release 7's copy in To Sell has no partner there.
        assert [g["ref"] for g in _document(result)["groups"]] == ["@r9"]
        assert _document(result)["records"] == 3
        text, _ = h.run(*argv)
        assert text.stdout.splitlines()[0] == (
            'Duplicates: me folder="to sell" by release (1 release owned more than '
            "once, 2 copies, in 3 records)"
        )
        result, sent = h.run(
            "get", "collection", "--folder", "Jazz", "--duplicates", "release", "--json"
        )
        assert json_error(result)["code"] == "invalid_argument"
        assert _paths(sent) == []

    def test_fields(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _Harness(monkeypatch)
        argv = ["get", "collection", "--duplicates", "release", "--fields"]
        result, sent = h.run(*argv, "--json")
        assert _paths(sent) == [
            "/oauth/identity",
            _FOLDER_LIST,
            _FIELDS,
            *[_RECORDS] * 3,
        ]
        (group,) = _document(result)["groups"]
        assert group["fields"] == {"Media Condition": "VG+", "Notes": "Shelf A3"}
        text, _ = h.run(*argv)
        assert text.stdout.splitlines()[2] == (
            f"{_ROW_7} · folders Vinyl, Uncategorized "
            "· Media Condition: VG+ · Notes: Shelf A3"
        )
        # The fields list comes after the folder list, inside the cap.
        result, sent = h.run("--fresh", *argv, "--max-requests", "3", "--json")
        assert _paths(sent) == ["/oauth/identity", _FOLDER_LIST, _FIELDS]
        assert _document(result)["collection"]["records_read"] == 0

    def test_an_artist_filter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Release 9: records 20 and 21 credit Guest (21 with Artist too),
        # record 22 Artist alone; release 7's copies (3, 120) credit Artist.
        guest = [{"id": 5, "name": "Guest", "join": ""}]
        both = [{"id": 4242, "name": "Artist", "join": "&"}, *guest]
        odd = {
            20: _copy(20, 9, 1, artists=guest),
            21: _copy(21, 9, 1, artists=both),
            22: _copy(22, 9, 1),
        }
        h = _Harness(monkeypatch, _Collection(odd=odd))
        base = ["get", "collection", "--duplicates", "release", "--artist"]
        result, _ = h.run(*base, "guest", "--json")
        assert _groups(result) == [("@r9", 2)]
        assert _document(result)["records"] == 2
        assert _document(result)["collection"] == _COMPLETE
        result, _ = h.run(*base, "artist", "--json")
        assert _groups(result) == [("@r7", 2), ("@r9", 2)]
        assert _document(result)["records"] == 249
        text, _ = h.run(*base, "guest")
        assert text.stdout.splitlines()[0] == (
            "Duplicates: me artist=guest by release (1 release owned more than "
            "once, 2 copies, in 2 of 250 records)"
        )

    def test_one_folder_with_a_filter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # To Sell holds release 9 twice as Techno (records 20, 21) and
        # release 8 twice as House (23, 24); a third Techno copy of release
        # 9 (record 22) is in Uncategorized.
        odd = {
            20: _copy(20, 9, 7, styles=["Techno"]),
            21: _copy(21, 9, 7, styles=["Techno"]),
            22: _copy(22, 9, 1, styles=["Techno"]),
            23: _copy(23, 8, 7),
            24: _copy(24, 8, 7),
        }
        h = _Harness(monkeypatch, _Collection(odd=odd))
        argv = ["get", "collection", "--folder", "To Sell", "--style", "techno"]
        result, sent = h.run(*argv, "--duplicates", "release", "--json")
        assert _paths(sent) == ["/oauth/identity", _FOLDER_LIST, _folder(7)]
        document = _document(result)
        assert _groups(result) == [("@r9", 2)]
        assert document["groups"][0]["folders"] == ["To Sell"]
        assert document["records"] == 2
        assert document["collection"] == {
            "complete": True,
            "records_read": 4,
            "total_records": 4,
        }
        text, _ = h.run(*argv, "--duplicates", "release")
        assert text.stdout.splitlines()[0] == (
            'Duplicates: me folder="To Sell" style=techno by release (1 release '
            "owned more than once, 2 copies, in 2 of 4 records)"
        )

    def test_one_folder_with_fields(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Record 120, release 7's second copy, moves to Vinyl beside record 3.
        h = _Harness(monkeypatch, _Collection(odd={120: _copy(120, 7, 5)}))
        argv = ["get", "collection", "--folder", "vinyl", "--duplicates", "release"]
        result, sent = h.run(*argv, "--fields", "--json")
        assert _paths(sent) == ["/oauth/identity", _FOLDER_LIST, _FIELDS, _folder(5)]
        (group,) = _document(result)["groups"]
        assert (group["ref"], group["copies"]) == ("@r7", 2)
        assert group["folders"] == ["Vinyl"]
        assert group["fields"] == {"Media Condition": "VG+", "Notes": "Shelf A3"}
        assert _document(result)["collection"]["total_records"] == 2
        text, _ = h.run(*argv, "--fields")
        assert text.stdout.splitlines()[2] == (
            f"{_ROW_7} · folder Vinyl · Media Condition: VG+ · Notes: Shelf A3"
        )

    def test_the_cache(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _Harness(monkeypatch)
        argv = ["get", "collection", "--duplicates", "release"]
        result, sent = h.run("--cached", *argv, "--json")
        assert json_error(result)["code"] == "cache_miss"
        assert sent == []
        h.run("search", "release", "x", "--release-type", "all", "--owned")
        result, sent = h.run(*argv)
        assert result.exit_code == 0, result.output
        assert _paths(sent) == [_FOLDER_LIST]

    def test_an_empty_collection(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _Harness(monkeypatch, _Collection(0))
        result, _ = h.run("get", "collection", "--duplicates", "master", "--json")
        document = _document(result)
        assert (document["groups"], document["records"]) == ([], 0)
        assert document["without_master"] == 0

    def test_an_unknown_kind_is_a_usage_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch)
        result, sent = h.run("get", "collection", "--duplicates", "folder")
        assert result.exit_code == 2
        assert sent == []

    def test_max_requests_alone_names_duplicates(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch)
        result, sent = h.run("get", "collection", "--max-requests", "5", "--json")
        assert "--duplicates" in json_error(result)["message"]
        assert sent == []
