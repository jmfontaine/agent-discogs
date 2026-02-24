"""Tests for pagination helpers."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

from pydantic import BaseModel

from agent_discogs.pagination import PageResult, fetch_filtered_page, fetch_page


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


class _FakeModel(BaseModel):
    id: int
    name: str


def _make_fake_client(
    body: dict[str, object],
    status_code: int = 200,
) -> Any:
    """Build a fake Discogs client that returns a canned response."""
    response = SimpleNamespace(
        json=lambda: body,
        status_code=status_code,
        headers={},
    )
    return SimpleNamespace(
        _build_url=lambda path: f"https://api.discogs.com{path}",
        _send=lambda method, url, params=None: response,
        _maybe_raise=lambda *_a, **_kw: None,
    )


class TestFetchFilteredPage:
    def test_filters_items(self) -> None:
        """Keeps only items matching the predicate."""
        body = {
            "pagination": {"page": 1, "items": 3, "pages": 1},
            "results": [
                {"id": 1, "name": "keep"},
                {"id": 2, "name": "skip"},
                {"id": 3, "name": "keep-too"},
            ],
        }
        client = _make_fake_client(body)

        result = fetch_filtered_page(
            client,
            "/test",
            {},
            _FakeModel,
            "results",
            user_page=1,
            limit=5,
            keep=lambda item: "keep" in item.name,
        )

        assert len(result.items) == 2
        assert result.items[0].id == 1
        assert result.items[1].id == 3
        assert result.page == 1

    def test_skips_previous_pages(self) -> None:
        """User page 2 skips items from page 1."""
        body = {
            "pagination": {"page": 1, "items": 4, "pages": 1},
            "results": [
                {"id": 1, "name": "a"},
                {"id": 2, "name": "b"},
                {"id": 3, "name": "c"},
                {"id": 4, "name": "d"},
            ],
        }
        client = _make_fake_client(body)

        result = fetch_filtered_page(
            client,
            "/test",
            {},
            _FakeModel,
            "results",
            user_page=2,
            limit=2,
            keep=lambda _: True,
        )

        assert len(result.items) == 2
        assert result.items[0].id == 3
        assert result.items[1].id == 4
        assert result.page == 2

    def test_fetches_multiple_api_pages(self) -> None:
        """Fetches additional API pages to fill the requested limit."""
        call_count = 0

        def _send(_method: str, _url: str, params: Any = None) -> SimpleNamespace:
            nonlocal call_count
            call_count += 1
            # Each API page has 1 matching + 1 non-matching
            items = [
                {"id": call_count * 10, "name": "yes"},
                {"id": call_count * 10 + 1, "name": "no"},
            ]
            body = {
                "pagination": {"page": call_count, "items": 6, "pages": 3},
                "results": items,
            }
            return SimpleNamespace(
                json=lambda: body,
                status_code=200,
                headers={},
            )

        client = SimpleNamespace(
            _build_url=lambda path: f"https://api.discogs.com{path}",
            _send=_send,
            _maybe_raise=lambda *_a, **_kw: None,
        )

        result = fetch_filtered_page(
            cast(Any, client),
            "/test",
            {},
            _FakeModel,
            "results",
            user_page=1,
            limit=3,
            keep=lambda item: item.name == "yes",
        )

        assert len(result.items) == 3
        assert call_count == 3


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
