"""Tests for the `get collection` filters, their patterns and `--folder`,
against the real SDK client over a mock transport."""

from __future__ import annotations

from typing import Any

import pytest

from tests.conftest import (
    _FOLDER_LIST,
    _FOLDERS,
    _RECORDS,
    _TECHNO,
    _TECHNO_REFS,
    _Collection,
    _continuation,
    _document,
    _folder,
    _Harness,
    _pages,
    _paths,
    _refs,
    json_error,
    text_error,
)


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
