"""Tests for `get owned`: a release's copies and their folders, and any
pressing of a master from one collection read, against the real SDK client
over a mock transport."""

from __future__ import annotations

import json

import pytest

from tests.conftest import (
    _FOLDER_LIST,
    _RECORDS,
    _USER,
    _Account,
    _AccountHarness,
)


class TestGetOwned:
    def test_counts_and_folders_per_ref(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _AccountHarness(monkeypatch, _Account())
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
        h = _AccountHarness(monkeypatch, _Account())
        result, _ = h.run("get", "owned", "@r6", "--json")
        assert json.loads(result.stdout) == {"ref": "@r6", "owned": 1}
        text, _ = h.run("get", "owned", "@r6")
        assert text.stdout == "@r6 owned\n"

    def test_one_ref_keeps_the_single_document(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _AccountHarness(monkeypatch, _Account())
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
        h = _AccountHarness(monkeypatch, _Account())
        result, sent = h.run("get", "owned", "@r3", "--json", "--full")
        document = json.loads(result.stdout)
        assert [i["instance_id"] for i in document["instances"]] == [20, 150]
        assert f"{_USER}/collection/folders" not in sent

    def test_a_failing_ref_is_its_own_block(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _AccountHarness(monkeypatch, _Account())
        result, _ = h.run("get", "owned", "@r2", "@a1", "--json")
        assert result.exit_code == 1
        first, second = json.loads(result.stdout)
        assert first["owned"] == 1
        assert second["error"]["code"] == "invalid_argument"


_ALL_READ = {"complete": True, "records_read": 250, "total_records": 250}
_PAGE_1_READ = {
    "complete": False,
    "records_read": 100,
    "total_records": 250,
    "stop_reason": "rate_limited",
    "retry_after": 30,
}
_NOTE = (
    "Owned: the collection read was rate limited, retry in 30s (100 of 250 "
    'records read): "owned ?" rows are not in the part read, and a count may '
    "be short."
)


class TestGetOwnedMaster:
    """`get owned @m...` answers from one read of the whole collection,
    matching each record's `basic_information.master_id`."""

    def test_copies_per_release_with_their_folders(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _AccountHarness(monkeypatch, _Account())
        result, sent = h.run("get", "owned", "@m77", "--json")
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout) == {
            "ref": "@m77",
            "owned": 3,
            "releases": [
                {"ref": "@r2", "owned": 1, "folders": ["Uncategorized"]},
                {"ref": "@r3", "owned": 2, "folders": ["Vinyl", "Uncategorized"]},
            ],
            "collection": _ALL_READ,
        }
        assert sent == ["/oauth/identity", *[_RECORDS] * 3, _FOLDER_LIST]
        assert h.pages() == [1, 2, 3]

        text, sent = h.run("get", "owned", "@m77")
        assert sent == []
        assert text.stdout == (
            "@m77 owned \u00d73 in 2 releases\n"
            "  @r2 owned: Uncategorized\n"
            "  @r3 owned \u00d72: Vinyl, Uncategorized\n"
        )

    def test_one_copy_reads_in_one_release(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        account = _Account()
        account.masters[2] = 78
        h = _AccountHarness(monkeypatch, account)
        text, _ = h.run("get", "owned", "@m78")
        assert text.stdout == "@m78 owned in 1 release\n  @r2 owned: Uncategorized\n"

    def test_a_master_not_in_the_collection_is_not_owned(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _AccountHarness(monkeypatch, _Account())
        result, sent = h.run("get", "owned", "@m99", "--json")
        assert json.loads(result.stdout) == {
            "ref": "@m99",
            "owned": 0,
            "collection": _ALL_READ,
        }
        assert _FOLDER_LIST not in sent
        text, _ = h.run("get", "owned", "@m99")
        assert text.stdout == "@m99 not owned\n"

    def test_a_record_without_a_master_matches_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Release 6's record has master id 0, the others' none at all."""
        h = _AccountHarness(monkeypatch, _Account())
        result, _ = h.run("get", "owned", "@m0", "@r6", "--json")
        master, release = json.loads(result.stdout)
        assert master["owned"] == 0
        assert release == {"ref": "@r6", "owned": 1}

    def test_mixed_refs_share_the_username_and_the_folder_list(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _AccountHarness(monkeypatch, _Account())
        result, sent = h.run("get", "owned", "@r3", "@m77", "@r1")
        assert result.exit_code == 0, result.output
        assert sent == [
            "/oauth/identity",
            f"{_USER}/collection/releases/3",
            _FOLDER_LIST,
            *[_RECORDS] * 3,
            f"{_USER}/collection/releases/1",
        ]

    def test_several_masters_read_the_collection_once(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _AccountHarness(monkeypatch, _Account())
        result, sent = h.run("--fresh", "get", "owned", "@m77", "@m99", "--json")
        assert [doc["owned"] for doc in json.loads(result.stdout)] == [3, 0]
        assert sent.count("/oauth/identity") == 1
        assert sent.count(_FOLDER_LIST) == 1
        assert h.pages() == [1, 2, 3]

    def test_full_gives_the_matching_records(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _AccountHarness(monkeypatch, _Account())
        result, sent = h.run("get", "owned", "@m77", "--json", "--full")
        document = json.loads(result.stdout)
        assert [i["instance_id"] for i in document["instances"]] == [10, 20, 150]
        assert document["collection"] == _ALL_READ
        assert _FOLDER_LIST not in sent

    def test_rate_limit_mid_read_answers_from_the_part_read(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _AccountHarness(monkeypatch, _Account(throttle=("collection:2",)))
        result, _ = h.run("get", "owned", "@m77", "@m99", "--json")
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout) == [
            {
                "ref": "@m77",
                "owned": 2,
                "releases": [
                    {"ref": "@r2", "owned": 1, "folders": ["Uncategorized"]},
                    {"ref": "@r3", "owned": 1, "folders": ["Vinyl"]},
                ],
                "collection": _PAGE_1_READ,
            },
            {"ref": "@m99", "owned": None, "collection": _PAGE_1_READ},
        ]

        text, _ = h.run("get", "owned", "@m77", "@m99")
        assert text.exit_code == 0, text.output
        assert text.stdout == (
            "@m77 owned \u00d72 in 2 releases\n"
            "  @r2 owned: Uncategorized\n"
            "  @r3 owned: Vinyl\n"
            f"\n{_NOTE}\n\n"
            f"@m99 owned ?\n\n{_NOTE}\n"
        )

    def test_rate_limited_folder_list_names_folders_by_id(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        account = _Account(throttle=("collection:2", "folders"))
        h = _AccountHarness(monkeypatch, account)
        text, sent = h.run("get", "owned", "@m77", "@m77", "@r2")
        assert text.exit_code == 0, text.output
        assert text.stdout.splitlines()[:3] == [
            "@m77 owned \u00d72 in 2 releases",
            "  @r2 owned: folder 1",
            "  @r3 owned: folder 5",
        ]
        assert "  @r3 owned: folder 5" in text.stdout.split("\n\n@m77")[1]
        assert text.stdout.rstrip().endswith("@r2 owned: folder 1"), text.stdout
        assert sent.count(f"{_USER}/collection/folders") == 1

    @pytest.mark.parametrize("throttle", ["identity", "collection:1"])
    def test_rate_limit_before_any_record_is_undecided(
        self, monkeypatch: pytest.MonkeyPatch, throttle: str
    ) -> None:
        h = _AccountHarness(monkeypatch, _Account(throttle=(throttle,)))
        result, _ = h.run("get", "owned", "@m77", "--json")
        assert result.exit_code == 0, result.output
        document = json.loads(result.stdout)
        assert document["owned"] is None
        assert document["collection"]["records_read"] == 0
        assert document["collection"]["stop_reason"] == "rate_limited"

    def test_cached_answers_only_from_the_cache(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _AccountHarness(monkeypatch, _Account())
        result, sent = h.run("--cached", "get", "owned", "@m77", "@m99", "--json")
        assert result.exit_code == 1
        assert [b["error"]["code"] for b in json.loads(result.stdout)] == [
            "cache_miss",
            "cache_miss",
        ]
        assert sent == []

        first, _ = h.run("get", "owned", "@m77")
        again, sent = h.run("--cached", "get", "owned", "@m77")
        assert sent == []
        assert again.stdout == first.stdout

    def test_another_ref_type_names_both_accepted_types(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _AccountHarness(monkeypatch, _Account())
        result, _ = h.run("get", "owned", "@a1", "@m77", "--json")
        assert result.exit_code == 1
        first, second = json.loads(result.stdout)
        assert first["error"] == {
            "code": "invalid_argument",
            "message": "@a1 is an artist, not a release or a master. Use a "
            "release or master ref or ID with 'get owned'.",
        }
        assert second["owned"] == 3

    def test_a_raw_id_is_a_release(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _AccountHarness(monkeypatch, _Account())
        result, sent = h.run("get", "owned", "77", "--json")
        assert json.loads(result.stdout) == {"ref": "@r77", "owned": 0}
        assert sent == ["/oauth/identity", f"{_USER}/collection/releases/77"]
