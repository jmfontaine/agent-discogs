"""Tests for `get collection`: its rows, server-side pages, arguments and
`--value`, against the real SDK client over a mock transport. The filters
and `--folder` are in test_cli_collection_filters.py."""

from __future__ import annotations

from typing import Any

import pytest

from tests.conftest import (
    _FIELDS,
    _FOLDER_LIST,
    _RECORDS,
    _USER,
    _VALUE,
    _VALUE_PATH,
    _Collection,
    _continuation,
    _document,
    _Harness,
    _paths,
    _refs,
    json_error,
    text_error,
)


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

    @pytest.mark.parametrize("title", [None, ""])
    def test_a_record_without_a_title_is_titled_by_its_artist(
        self, monkeypatch: pytest.MonkeyPatch, title: str | None
    ) -> None:
        record = _Collection.record(0)
        record["basic_information"]["title"] = title
        h = _Harness(monkeypatch, _Collection(1, odd={0: record}))
        result, _ = h.run("get", "collection")
        assert result.exit_code == 0, result.output
        row = result.stdout.splitlines()[2]
        assert row.startswith('@r1000 "Artist" 2001 · ')
        rows = _document(h.run("get", "collection", "--json")[0])["results"]
        assert rows[0]["title"] == "Artist"

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
            ("fields", ["--fields"]),
            ("fields", ["--fields", "--style", "Techno"]),
        ],
    )
    def test_a_429_before_the_listing_fails(
        self, monkeypatch: pytest.MonkeyPatch, throttle: str, flags: list[str]
    ) -> None:
        h = _Harness(monkeypatch, _Collection(throttle=(throttle,)))
        result, sent = h.run("get", "collection", *flags, "--json")
        assert json_error(result)["code"] == "rate_limited"
        assert not [path for path in _paths(sent) if path.endswith("/releases")]


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
            "get collection --fields --value",
            "get collection --fields --full",
            "get release @r1 --fields",
            "get versions @m1 --fields",
            "get collection --count-by genre --limit 5",
            "get collection --count-by genre --page 2",
            "get collection --count-by genre --after 1:0",
            "get collection --count-by genre --sort year",
            "get collection --count-by genre --desc",
            "get collection --count-by genre --value",
            "get collection --count-by genre --full",
            "get collection --count-by genre --fields",
            "get collection --top 3",
            "get collection --count-by genre --top 0",
            "get release @r1 --count-by genre",
            "get versions @m1 --top 3",
            "get collection --duplicates release --limit 5",
            "get collection --duplicates release --page 2",
            "get collection --duplicates release --after 1:0",
            "get collection --duplicates release --sort year",
            "get collection --duplicates release --desc",
            "get collection --duplicates release --value",
            "get collection --duplicates release --count-by genre",
            "get collection --duplicates release --top 3",
            "get collection --duplicates release --fields --full",
            "get release @r1 --duplicates release",
            "get collection --random 0",
            "get collection --random 101",
            "get collection --random --limit 5",
            "get collection --random --page 2",
            "get collection --random --after 1:0",
            "get collection --random --sort year",
            "get collection --random --desc",
            "get collection --random --max-requests 5",
            "get collection --random --folder Vinyl --max-requests 5",
            "get collection --random --value",
            "get collection --random --count-by genre",
            "get collection --random --duplicates release",
            "get collection --random --top 3",
            "get collection --random --fields --full",
            "get release @r1 --random",
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

    @pytest.mark.parametrize("desc", [[], ["--desc"]])
    def test_an_empty_sort_is_rejected(
        self, monkeypatch: pytest.MonkeyPatch, desc: list[str]
    ) -> None:
        h = _Harness(monkeypatch)
        result, sent = h.run("get", "collection", "--sort", "", *desc, "--json")
        assert json_error(result) == {
            "code": "invalid_argument",
            "message": "--sort '' is not valid for 'get collection'. Keys: label, "
            "artist, title, catno, format, rating, added, year.",
        }
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


def _note(field_id: int | None, value: str | None) -> dict[str, Any]:
    return {"field_id": field_id, "value": value}


class TestFields:
    def test_rows_name_the_fields(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _Harness(monkeypatch)
        plain, _ = h.run("get", "collection")
        h = _Harness(monkeypatch)  # a cold cache again
        result, sent = h.run("get", "collection", "--fields")
        assert result.exit_code == 0, result.output
        assert _paths(sent) == ["/oauth/identity", _FOLDER_LIST, _FIELDS, _RECORDS]
        lines = result.stdout.splitlines()
        assert lines[5] == (
            '@r7 "Artist - Record 3" 2001 · Label CAT3 · Vinyl (LP) '
            "· folder Vinyl · added 2024-01-04 · rating 4 "
            "· Media Condition: VG+ · Notes: Shelf A3"
        )
        expected = plain.stdout.splitlines()
        assert lines[:5] == expected[:5]
        assert lines[6:-1] == expected[6:-1]
        assert lines[-1] == "Next page: agent-discogs get collection --fields --page 2"
        again, sent = h.run("get", "collection", "--fields")
        assert sent == []
        assert again.stdout == result.stdout

    def test_json_rows_carry_fields(self, monkeypatch: pytest.MonkeyPatch) -> None:
        odd = {
            0: {**_Collection.record(0), "notes": []},
            1: {**_Collection.record(1), "notes": [_note(1, ""), _note(3, None)]},
        }
        h = _Harness(monkeypatch, _Collection(odd=odd))
        result, sent = h.run("get", "collection", "--fields", "--json")
        assert _FIELDS in _paths(sent)
        rows = _document(result)["results"]
        assert rows[3]["fields"] == {"Media Condition": "VG+", "Notes": "Shelf A3"}
        assert list(rows[3])[-2:] == ["rating", "fields"]
        assert [row.get("fields") for row in (rows[0], rows[1], rows[2])] == [None] * 3

        plain, sent = h.run("get", "collection", "--json")
        assert sent == []
        assert "fields" not in _document(plain)["results"][3]

    def test_fields_by_position_and_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Ordered by the fields list's position, then id, whatever the order
        of the copy's notes; a grade name is shortened only in a dropdown."""
        fields = [
            {"id": 3, "name": "Notes", "type": "textarea", "position": 3},
            {"id": 8, "name": "Box", "type": "textarea"},
            {"id": 1, "name": "Media Condition", "type": "dropdown", "position": 1},
            {"id": 4, "name": "Location", "type": "textarea", "position": 2},
            {"id": 7, "name": "Box", "type": "textarea"},
            {"id": 2, "name": "Sleeve Condition", "type": "dropdown", "position": 2},
        ]
        notes = [
            _note(3, "Line 1\nLine 2"),
            _note(9, "Very Good (VG)"),
            _note(None, "no field"),
            _note(8, "b"),
            _note(7, "a"),
            _note(4, "Good (G)"),
            _note(2, "Generic"),
            _note(1, "Mint (M)"),
        ]
        odd = {0: {**_Collection.record(0), "notes": notes}}
        h = _Harness(monkeypatch, _Collection(1, odd=odd, fields=fields))
        result, _ = h.run("get", "collection", "--fields", "--json")
        assert _document(result)["results"][0]["fields"] == {
            "Media Condition": "M",
            "Sleeve Condition": "Generic",
            "Location": "Good (G)",
            "Notes": "Line 1\nLine 2",
            "Box (field 7)": "a",
            "Box (field 8)": "b",
            "field 9": "Very Good (VG)",
        }
        text, _ = h.run("get", "collection", "--fields")
        assert text.stdout.splitlines()[2].endswith(
            "· added 2024-01-01 · Media Condition: M · Sleeve Condition: Generic "
            "· Location: Good (G) · Notes: Line 1 Line 2 · Box (field 7): a "
            "· Box (field 8): b · field 9: Very Good (VG)"
        )

    def test_every_value_keeps_its_own_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A suffixed name that is another field's own name, and an unknown
        field id whose `field N` is a field's name, are suffixed again."""
        fields = [
            {"id": 7, "name": "Box", "type": "textarea"},
            {"id": 8, "name": "Box", "type": "textarea"},
            {"id": 10, "name": "Box (field 7)", "type": "textarea"},
            {"id": 5, "name": "field 9", "type": "textarea"},
        ]
        notes = [_note(n, f"v{n}") for n in (5, 7, 8, 9, 10)]
        odd = {0: {**_Collection.record(0), "notes": notes}}
        h = _Harness(monkeypatch, _Collection(1, odd=odd, fields=fields))
        result, _ = h.run("get", "collection", "--fields", "--json")
        assert _document(result)["results"][0]["fields"] == {
            "field 9": "v5",
            "Box (field 7)": "v7",
            "Box (field 8)": "v8",
            "Box (field 7) (field 10)": "v10",
            "field 9 (field 9)": "v9",
        }

    def test_max_requests_counts_the_fields_list(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        argv = ["get", "collection", "--fields", "--style", "Techno"]
        h = _Harness(monkeypatch)
        result, sent = h.run(*argv, "--max-requests", "3")
        assert _paths(sent) == ["/oauth/identity", _FOLDER_LIST, _FIELDS]
        assert result.stdout.splitlines()[0] == (
            "Collection: me style=Techno "
            "(--max-requests 3 ran out before the scan started)"
        )
        assert _continuation(result) == [
            *("get", "collection", "--fields", "--style", "Techno"),
            *("--max-requests", "3", "--after", "1:0"),
        ]

        h = _Harness(monkeypatch)
        result, sent = h.run(*argv, "--max-requests", "4", "--json")
        assert _paths(sent) == ["/oauth/identity", _FOLDER_LIST, _FIELDS, _RECORDS]
        assert _document(result)["pagination"]["next_cursor"] == "2:100"

        for cap, paths in (("2", [_FOLDER_LIST]), ("1", [])):
            h = _Harness(monkeypatch)
            result, sent = h.run(*argv, "--max-requests", cap, "--json")
            assert _paths(sent) == ["/oauth/identity", *paths]
            assert _document(result)["pagination"]["capped"] is True

    def test_cache_flags(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _Harness(monkeypatch)
        result, sent = h.run("--cached", "get", "collection", "--fields", "--json")
        assert json_error(result)["code"] == "cache_miss"
        assert sent == []
        h.run("get", "collection")  # caches all but the fields list
        result, sent = h.run("--cached", "get", "collection", "--fields")
        assert "Not cached" in text_error(result)
        assert sent == []
        for _ in range(2):
            result, sent = h.run("--fresh", "get", "collection", "--fields")
            assert result.exit_code == 0, result.output
            assert _FIELDS in _paths(sent)

    def test_full_rejects_fields(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _Harness(monkeypatch)
        result, sent = h.run("get", "collection", "--fields", "--json", "--full")
        error = json_error(result)
        assert error["code"] == "invalid_argument"
        assert error["message"].startswith("--fields does not apply with --json --full")
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

        for argv in (
            ["--style", "Techno"],
            ["--value"],
            ["--count-by", "genre"],
            ["--duplicates", "release"],
            ["--random"],
        ):
            result, sent = h.run("get", "collection", *argv)
            assert "DISCOGS_TOKEN" in text_error(result)
            assert sent == []
