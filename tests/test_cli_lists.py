"""Tests for `get lists` and `get list`, against the real SDK client over a
mock transport."""

from __future__ import annotations

import json
from typing import Any

import httpx2
import pytest
from click.testing import CliRunner, Result

from agent_discogs import cli
from tests.conftest import _FakeAPI, json_error, text_error

_LISTS = "/users/me/lists"

# A comment as Discogs may send it: an escape sequence and a line break.
_HOSTILE = "check the matrix\x1b[31m\nsecond line"


def _summary(list_id: int) -> dict[str, Any]:
    return {
        "id": list_id,
        "name": f"List {list_id}",
        "description": f"About [b]list[/b] {list_id}",
        "public": list_id % 2 == 1,
        "date_added": "2016-05-31T10:30:51-07:00",
        "date_changed": "2016-06-01T20:00:00-07:00",
        "uri": f"https://www.discogs.com/lists/List-{list_id}/{list_id}",
        "resource_url": f"https://api.discogs.com/lists/{list_id}",
    }


def _item(type_: str, item_id: int, title: str, comment: str = "") -> dict[str, Any]:
    return {
        "type": type_,
        "id": item_id,
        "display_title": title,
        "comment": comment,
        "uri": f"https://www.discogs.com/{type_}/{item_id}",
        "resource_url": f"https://api.discogs.com/{type_}s/{item_id}",
    }


# List 123 holds one item of each type agent-discogs has a ref for, and one
# of a type it has none for.
_LIST_123 = {
    "list_id": 123,
    "name": "Pressings",
    "description": "The [b]best[/b] ones, by [a3857].\nSecond paragraph.",
    "public": False,
    "url": "https://www.discogs.com/lists/Pressings/123",
    "created_ts": "2016-05-31T10:36:30-07:00",
    "modified_ts": "2016-05-31T13:46:12-07:00",
    "items": [
        _item("release", 847868, "Nine Inch Nails - The Downward Spiral", _HOSTILE),
        _item("master", 3719, "Nine Inch Nails - The Downward Spiral"),
        _item("artist", 3857, "Nine Inch Nails", "the band"),
        _item("label", 647, "Nothing Records", "the label"),
        _item("collection", 9, "Something else", "odd one"),
    ],
}


class _Lists:
    """The token's user `me` keeps lists 1 to `size`; list 123 is
    `_LIST_123`, list 1 a list of one release, and any other list is
    missing (404)."""

    def __init__(self, size: int = 7) -> None:
        self.size = size

    def __call__(self, path: str, params: dict[str, str]) -> object:
        if path == "/oauth/identity":
            return {"id": 7, "username": "me"}
        if path == _LISTS:
            page, per = int(params.get("page", 1)), int(params.get("per_page", 50))
            last = min(page * per, self.size)
            pagination = {
                "page": page,
                "pages": -(-self.size // per),
                "items": self.size,
                "per_page": per,
            }
            lists = [_summary(i) for i in range((page - 1) * per + 1, last + 1)]
            return {"pagination": pagination, "lists": lists}
        if path == "/lists/123":
            return _LIST_123
        if path == "/lists/1":
            # As Discogs answers today, unlike its docs: the page is `uri`.
            return {
                "id": 1,
                "name": "One",
                "public": True,
                "uri": "https://www.discogs.com/lists/One/1",
                "items": [_item("release", 352665, "Nine Inch Nails - Closer")],
            }
        return httpx2.Response(404, json={"message": "Not found."})


class _Harness:
    """`_FakeAPI` over `_Lists`. With `token`, the environment holds the
    credentials of a user, as `get lists` needs."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, *, token: bool = True) -> None:
        if token:
            monkeypatch.setenv("DISCOGS_TOKEN", "test-token")
        self.api = _FakeAPI(monkeypatch, _Lists())

    def run(self, *args: str) -> tuple[Result, list[str]]:
        start = len(self.api.sends)
        result = CliRunner().invoke(cli, list(args))
        return result, [path for path, _ in self.api.sends[start:]]


def _document(result: Result) -> Any:
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


class TestLists:
    def test_one_identity_request_and_one_page(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch)
        result, sent = h.run("get", "lists", "--json")
        document = _document(result)
        assert sent == ["/oauth/identity", _LISTS]
        assert h.api.sends[1][1] == {"page": "1", "per_page": "5"}
        assert document["pagination"] == {
            "page": 1,
            "total_items": 7,
            "total_pages": 2,
        }
        assert [row["id"] for row in document["results"]] == [1, 2, 3, 4, 5]
        assert document["results"][0] == {
            "id": 1,
            "name": "List 1",
            "description": "About list 1",
            "public": True,
            "added": "2016-05-31T10:30:51-07:00",
            "changed": "2016-06-01T20:00:00-07:00",
            "url": "https://www.discogs.com/lists/List-1/1",
        }
        assert document["results"][1]["public"] is False

        second, sent = h.run("get", "lists", "--limit", "5", "--page", "2", "--json")
        assert sent == [_LISTS]  # the username is cached
        assert [row["id"] for row in _document(second)["results"]] == [6, 7]

    def test_text_rows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _Harness(monkeypatch)
        result, _ = h.run("get", "lists")
        assert result.exit_code == 0, result.output
        lines = result.stdout.splitlines()
        assert lines[0] == "Lists: me (page 1, 5 of 7 lists)"
        assert lines[1] == ""
        # The day is the UTC one: 20:00 at -07:00 is the next day in UTC.
        assert lines[2] == '1 "List 1" · public · changed 2016-06-02 → get list 1'
        assert lines[3] == '2 "List 2" · private · changed 2016-06-02 → get list 2'
        assert lines[-1] == "Next page: agent-discogs get lists --page 2"

        last, _ = h.run("get", "lists", "--page", "2")
        assert last.stdout.splitlines()[0] == "Lists: me (page 2, 2 of 7 lists)"
        assert "Next page" not in last.stdout

    def test_full_gives_the_list_records(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _Harness(monkeypatch)
        result, _ = h.run("get", "lists", "--json", "--full")
        row = _document(result)["results"][0]
        assert row["description"] == "About [b]list[/b] 1"
        assert row["resource_url"] == "https://api.discogs.com/lists/1"

    @pytest.mark.parametrize(
        "argv",
        [
            "get lists 123",
            "get lists @r1",
            "get lists --limit 0",
            "get lists --page 0",
            "get lists --after 2:3",
            "get lists --max-requests 5",
            "get lists --for-sale",
        ],
    )
    def test_misplaced_arguments_fail_before_any_request(
        self, monkeypatch: pytest.MonkeyPatch, argv: str
    ) -> None:
        h = _Harness(monkeypatch)
        result, sent = h.run(*argv.split(), "--json")
        assert json_error(result)["code"] == "invalid_argument"
        assert sent == []

    @pytest.mark.parametrize("mode", ["none", "consumer"])
    def test_needs_a_user(self, monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
        if mode == "consumer":
            monkeypatch.setenv("DISCOGS_CONSUMER_KEY", "key")
            monkeypatch.setenv("DISCOGS_CONSUMER_SECRET", "secret")
        h = _Harness(monkeypatch, token=False)
        result, sent = h.run("get", "lists", "--json")
        error = json_error(result)
        assert error["code"] == "auth_required"
        assert "get lists" in error["message"]
        assert sent == []

        result, sent = h.run("get", "lists")
        assert "DISCOGS_TOKEN" in text_error(result)
        assert sent == []


class TestList:
    def test_one_request_maps_each_item_to_its_ref(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch)
        result, sent = h.run("get", "list", "123", "--json")
        assert sent == ["/lists/123"]
        assert _document(result) == {
            "id": 123,
            "name": "Pressings",
            "description": "The best ones, by @a3857.\nSecond paragraph.",
            "public": False,
            "url": "https://www.discogs.com/lists/Pressings/123",
            "items": [
                {
                    "ref": "@r847868",
                    "title": "Nine Inch Nails - The Downward Spiral",
                    "comment": _HOSTILE,
                },
                {"ref": "@m3719", "title": "Nine Inch Nails - The Downward Spiral"},
                {"ref": "@a3857", "title": "Nine Inch Nails", "comment": "the band"},
                {"ref": "@l647", "title": "Nothing Records", "comment": "the label"},
                {
                    "id": 9,
                    "type": "collection",
                    "title": "Something else",
                    "comment": "odd one",
                },
            ],
        }

        text, sent = h.run("get", "list", "123")
        assert sent == []  # cached
        assert text.exit_code == 0, text.output
        assert text.stdout.splitlines() == [
            'List: 123 "Pressings" · private',
            "Description: The best ones, by @a3857.",
            "  Second paragraph.",
            "",
            (
                '@r847868 "Nine Inch Nails - The Downward Spiral" · comment: check '
                "the matrix\\x1b[31m second line"
            ),
            '@m3719 "Nine Inch Nails - The Downward Spiral"',
            '@a3857 "Nine Inch Nails" · comment: the band',
            '@l647 "Nothing Records" · comment: the label',
            'id 9 [collection] "Something else" · comment: odd one',
        ]

    def test_full_gives_the_list_record(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _Harness(monkeypatch)
        result, sent = h.run("get", "list", "123", "--json", "--full")
        assert sent == ["/lists/123"]
        document = _document(result)
        assert document["description"] == _LIST_123["description"]
        assert document["items"][0]["resource_url"] == (
            "https://api.discogs.com/releases/847868"
        )

    def test_a_typed_ref_is_an_invalid_argument(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch)
        result, sent = h.run("get", "list", "@r123", "--json")
        error = json_error(result)
        assert error["code"] == "invalid_argument"
        assert "list id" in error["message"]
        assert sent == []

    def test_several_ids_keep_going_past_a_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch)
        result, sent = h.run("get", "list", "1", "2")
        assert sent == ["/lists/1", "/lists/2"]
        assert result.exit_code == 1
        blocks = result.stdout.split("\n\n")
        assert blocks[0] == 'List: 1 "One" · public'
        assert blocks[1] == '@r352665 "Nine Inch Nails - Closer"'
        assert blocks[2] == (
            "✗ List 2 not found.\n  get lists shows your own list ids; another "
            "user's private list cannot be read.\n"
        )

        result, sent = h.run("get", "list", "1", "2", "--json")
        assert sent == ["/lists/2"]  # list 1 is cached; a 404 is not
        assert result.exit_code == 1
        documents = json.loads(result.stdout)
        assert documents[0]["name"] == "One"
        assert documents[0]["url"] == "https://www.discogs.com/lists/One/1"
        assert documents[1]["ref"] == "2"
        assert documents[1]["error"]["code"] == "not_found"

    def test_no_credentials_still_send_the_request(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch, token=False)
        result, sent = h.run("get", "list", "123", "--json")
        assert sent == ["/lists/123"]
        assert _document(result)["name"] == "Pressings"

    def test_an_empty_list_says_so(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def respond(_path: str, _params: dict[str, str]) -> object:
            return {"list_id": 5, "name": "Empty", "items": []}

        monkeypatch.setenv("DISCOGS_TOKEN", "test-token")
        _FakeAPI(monkeypatch, respond)
        result = CliRunner().invoke(cli, ["get", "list", "5"])
        assert result.exit_code == 0, result.output
        assert result.stdout.splitlines() == ['List: 5 "Empty"', "", "No items."]

    @pytest.mark.parametrize("argv", ["get list 1 --limit 10", "get list 1 --page 2"])
    def test_takes_no_paging_flags(
        self, monkeypatch: pytest.MonkeyPatch, argv: str
    ) -> None:
        h = _Harness(monkeypatch)
        result, sent = h.run(*argv.split(), "--json")
        assert json_error(result)["code"] == "invalid_argument"
        assert sent == []
