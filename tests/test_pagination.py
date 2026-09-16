"""Tests for pagination helpers."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel

from agent_discogs.pagination import (
    MAX_API_CALLS,
    PageResult,
    fetch_filtered_page,
    fetch_page,
    next_page_cmd,
    parse_cursor,
)


class TestPageResult:
    def test_has_next_true(self) -> None:
        pr = PageResult(items=[], page=1, total_items=10, total_pages=3)
        assert pr.has_next is True

    def test_has_next_false(self) -> None:
        pr = PageResult(items=[], page=3, total_items=10, total_pages=3)
        assert pr.has_next is False

    def test_has_next_single_page(self) -> None:
        pr = PageResult(items=[], page=1, total_items=2, total_pages=1)
        assert pr.has_next is False

    def test_filtered_has_next_follows_cursor_not_page_arithmetic(self) -> None:
        """Filtered pages mix units (user page vs API page); only the cursor counts."""
        pr = PageResult(
            items=[],
            page=3,
            total_items=23,
            total_pages=2,
            filtered=True,
            next_cursor="4:2.3",
        )
        assert pr.has_next is True
        pr = PageResult(
            items=[],
            page=1,
            total_items=500,
            total_pages=34,
            filtered=True,
            next_cursor=None,
        )
        assert pr.has_next is False


class TestParseCursor:
    def test_none_is_start(self) -> None:
        assert parse_cursor(None) == (1, 1, 0)

    def test_round_trip(self) -> None:
        assert parse_cursor("3:2.7") == (3, 2, 7)

    @pytest.mark.parametrize("bad", ["2", "2:x.0", "0:1.0", "2:0.0", "2:1.-1", "2:1"])
    def test_invalid(self, bad: str) -> None:
        with pytest.raises(ValueError, match="Invalid --after cursor"):
            parse_cursor(bad)


class TestNextPageCmd:
    def test_quotes_and_orders_flags(self) -> None:
        cmd = next_page_cmd(
            ["search", "release", "Plastic Dreams"],
            label="R & S Records",
            year=None,
            release_type="",
            limit=10,
            after="2:1.5",
        )
        assert cmd == (
            "agent-discogs search release 'Plastic Dreams' "
            "--label 'R & S Records' --limit 10 --after 2:1.5"
        )

    def test_quotes_survive_a_shell_round_trip(self) -> None:
        import shlex

        cmd = next_page_cmd(["search", 'He said "hi" $HOME'], label="R & S")
        assert shlex.split(cmd) == [
            "agent-discogs",
            "search",
            'He said "hi" $HOME',
            "--label",
            "R & S",
        ]


class _FakeModel(BaseModel):
    id: int
    name: str


def _make_fake_client(
    body: Mapping[str, object],
    status_code: int = 200,
) -> Any:
    """Build a fake Discogs client that returns a canned response.

    The real `_send()` is the SDK's HTTP-error boundary: it raises before
    returning, so nothing downstream of it needs to re-check the status.
    """
    response = SimpleNamespace(
        json=lambda: body,
        status_code=status_code,
        headers={},
    )
    return SimpleNamespace(
        _build_url=lambda path: f"https://api.discogs.com{path}",
        _send=lambda method, url, params=None: response,
    )


def _paged_client(
    total: int, per_page_hook: Callable[[int], None] | None = None
) -> Any:
    """Fake client serving `total` rows (ids 0..total-1) across API pages."""
    calls: list[int] = []

    def _send(_method: str, _url: str, params: Any = None) -> SimpleNamespace:
        page, per = params["page"], params["per_page"]
        calls.append(page)
        if per_page_hook:
            per_page_hook(per)
        start = (page - 1) * per
        rows = [
            {"id": i, "name": f"row{i}"} for i in range(start, min(start + per, total))
        ]
        body = {
            "pagination": {"page": page, "items": total, "pages": -(-total // per)},
            "results": rows,
        }
        return SimpleNamespace(json=lambda: body, status_code=200, headers={})

    client = SimpleNamespace(
        _build_url=lambda path: f"https://api.discogs.com{path}", _send=_send
    )
    client.calls = calls
    return client


def _scan(
    client: Any, *, limit: int, keep: Callable[[Any], bool], cursor: str | None
) -> PageResult:
    return fetch_filtered_page(
        client,
        "/test",
        {},
        _FakeModel,
        "results",
        limit=limit,
        keep=keep,
        cursor=cursor,
    )


class TestFetchFilteredPage:
    def test_filters_items(self) -> None:
        body = {
            "pagination": {"page": 1, "items": 3, "pages": 1},
            "results": [
                {"id": 1, "name": "keep"},
                {"id": 2, "name": "skip"},
                {"id": 3, "name": "keep-too"},
            ],
        }
        result = _scan(
            _make_fake_client(body),
            limit=5,
            keep=lambda i: "keep" in i.name,
            cursor=None,
        )
        assert [i.id for i in result.items] == [1, 3]
        assert result.filtered is True
        assert result.capped is False
        assert result.next_cursor is None  # raw rows exhausted: no footer

    def test_cursor_resumes_mid_api_page_without_rescanning(self) -> None:
        """Page 2 starts exactly after the row that filled page 1."""
        client = _paged_client(6)
        first = _scan(client, limit=5, keep=lambda _: True, cursor=None)
        assert [i.id for i in first.items] == [0, 1, 2, 3, 4]
        assert first.next_cursor == "2:1.5"
        assert client.calls == [1]

        second = _scan(client, limit=5, keep=lambda _: True, cursor=first.next_cursor)
        assert [i.id for i in second.items] == [5]
        assert second.page == 2
        assert second.next_cursor is None
        assert client.calls == [
            1,
            1,
        ]  # resumed on API page 1 at offset 5, no page 2 fetch

    def test_five_of_six_gets_a_cursor(self) -> None:
        """Regression: the old has_next compared user pages to API pages and withheld
        the footer here, making the 6th result unreachable."""
        result = _scan(_paged_client(6), limit=5, keep=lambda _: True, cursor=None)
        assert result.has_next is True

    def test_fill_at_end_of_api_page_advances_to_next_api_page(self) -> None:
        client = _paged_client(30)  # per_page = limit*3 = 15 -> 2 API pages
        result = _scan(client, limit=5, keep=lambda i: i.id >= 10, cursor=None)
        assert [i.id for i in result.items] == [10, 11, 12, 13, 14]
        assert result.next_cursor == "2:2.0"

    def test_fill_at_last_raw_row_ends_without_cursor(self) -> None:
        """Filling the page on the final row of the final API page is the end."""
        result = _scan(_paged_client(5), limit=5, keep=lambda _: True, cursor=None)
        assert len(result.items) == 5
        assert result.next_cursor is None
        assert result.has_next is False

    def test_sparse_filter_is_capped_and_resumes_without_gaps(self) -> None:
        """Every raw API page is scanned exactly once across capped windows."""
        client = _paged_client(600)
        keep = lambda i: i.id % 40 == 0  # noqa: E731
        cursor: str | None = None
        windows: list[PageResult] = []
        for _ in range(20):
            windows.append(_scan(client, limit=5, keep=keep, cursor=cursor))
            cursor = windows[-1].next_cursor
            if cursor is None:
                break

        assert client.calls == list(range(1, 41))
        assert all(w.capped for w in windows[:-1])
        assert [w.next_cursor for w in windows[:2]] == ["2:6.0", "3:11.0"]
        assert [i.id for w in windows for i in w.items] == list(range(0, 600, 40))
        assert windows[-1].next_cursor is None
        assert windows[-1].capped is False

    def test_capped_window_can_be_empty_but_never_claims_the_end(self) -> None:
        client = _paged_client(600)
        result = _scan(client, limit=5, keep=lambda i: i.id == 599, cursor=None)
        assert result.items == []
        assert result.capped is True
        assert result.next_cursor == "2:6.0"
        assert len(client.calls) == MAX_API_CALLS

    def test_overfetches_three_times_the_limit(self) -> None:
        seen: list[int] = []
        _scan(_paged_client(1, seen.append), limit=4, keep=lambda _: True, cursor=None)
        assert seen == [12]


class TestFetchPage:
    def test_fetch_page(self) -> None:
        body = {
            "pagination": {"page": 1, "items": 2, "pages": 5},
            "results": [
                {"id": 1, "name": "one"},
                {"id": 2, "name": "two"},
            ],
        }
        client = _make_fake_client(body)

        result = fetch_page(client, "/test", {"page": 1}, _FakeModel, "results")

        assert result.page == 1
        assert result.total_items == 2
        assert result.total_pages == 5
        assert len(result.items) == 2
        assert result.items[0].id == 1
        assert result.items[0].name == "one"

    def test_fetch_page_empty(self) -> None:
        client = _make_fake_client({})

        result = fetch_page(client, "/test", {}, _FakeModel, "results")

        assert result.page == 1
        assert result.total_items == 0
        assert result.total_pages == 1
        assert result.items == []
