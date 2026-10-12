"""Tests for pagination helpers."""

from __future__ import annotations

import contextlib
import functools
import json
from collections.abc import Callable, Generator
from types import SimpleNamespace
from typing import Any

import httpx2
import pytest
from discogs_sdk import Discogs, DiscogsError
from discogs_sdk import PageResult as SdkPageResult
from pydantic import BaseModel

from agent_discogs import trace
from agent_discogs.config import MAX_API_CALLS
from agent_discogs.pagination import (
    PageResult,
    fetch_filtered_page,
    fetch_page,
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


class _FakeModel(BaseModel):
    id: int
    name: str


def _listing_of(result: SdkPageResult[_FakeModel]) -> Any:
    """A fake SDK listing method whose every page fetches as `result`."""
    return lambda **_params: SimpleNamespace(fetch=lambda: result)


class _PagedListing:
    """A fake SDK listing method over `total` rows (ids 0..total-1). Called with
    `page`/`per_page`, it names that page, and `fetch()` returns it. `calls`
    lists the pages fetched and `per_pages` the page sizes asked for."""

    def __init__(self, total: int) -> None:
        self.total = total
        self.calls: list[int] = []
        self.per_pages: list[int] = []

    def __call__(self, *, page: int, per_page: int) -> SimpleNamespace:
        def fetch() -> SdkPageResult[_FakeModel]:
            self.calls.append(page)
            self.per_pages.append(per_page)
            start = (page - 1) * per_page
            end = min(start + per_page, self.total)
            return SdkPageResult(
                items=[_FakeModel(id=i, name=f"row{i}") for i in range(start, end)],
                page=page,
                per_page=per_page,
                total_items=self.total,
                total_pages=-(-self.total // per_page),
                urls={},
            )

        return SimpleNamespace(fetch=fetch)


def _scan(
    listing: Any, *, limit: int, keep: Callable[[Any], bool], cursor: str | None
) -> PageResult:
    return fetch_filtered_page(listing, limit=limit, keep=keep, cursor=cursor)


class TestFetchFilteredPage:
    def test_filters_items(self) -> None:
        items = [
            _FakeModel(id=1, name="keep"),
            _FakeModel(id=2, name="skip"),
            _FakeModel(id=3, name="keep-too"),
        ]
        page = SdkPageResult(
            items=items, page=1, per_page=15, total_items=3, total_pages=1, urls={}
        )
        result = _scan(
            _listing_of(page), limit=5, keep=lambda i: "keep" in i.name, cursor=None
        )
        assert [i.id for i in result.items] == [1, 3]
        assert result.filtered is True
        assert result.capped is False
        assert result.next_cursor is None  # raw rows exhausted: no footer

    def test_cursor_resumes_mid_api_page_without_rescanning(self) -> None:
        """Page 2 starts exactly after the row that filled page 1."""
        listing = _PagedListing(6)
        first = _scan(listing, limit=5, keep=lambda _: True, cursor=None)
        assert [i.id for i in first.items] == [0, 1, 2, 3, 4]
        assert first.next_cursor == "2:1.5"
        assert listing.calls == [1]

        second = _scan(listing, limit=5, keep=lambda _: True, cursor=first.next_cursor)
        assert [i.id for i in second.items] == [5]
        assert second.page == 2
        assert second.next_cursor is None
        # Resumed on API page 1 at offset 5, no page 2 fetch.
        assert listing.calls == [1, 1]

    def test_five_of_six_gets_a_cursor(self) -> None:
        """Regression: the old has_next compared user pages to API pages and withheld
        the footer here, making the 6th result unreachable."""
        result = _scan(_PagedListing(6), limit=5, keep=lambda _: True, cursor=None)
        assert result.has_next is True

    def test_fill_at_end_of_api_page_advances_to_next_api_page(self) -> None:
        listing = _PagedListing(30)  # per_page = limit*3 = 15 -> 2 API pages
        result = _scan(listing, limit=5, keep=lambda i: i.id >= 10, cursor=None)
        assert [i.id for i in result.items] == [10, 11, 12, 13, 14]
        assert result.next_cursor == "2:2.0"

    def test_fill_at_last_raw_row_ends_without_cursor(self) -> None:
        """Filling the page on the final row of the final API page is the end."""
        result = _scan(_PagedListing(5), limit=5, keep=lambda _: True, cursor=None)
        assert len(result.items) == 5
        assert result.next_cursor is None
        assert result.has_next is False

    def test_sparse_filter_is_capped_and_resumes_without_gaps(self) -> None:
        """Every raw API page is scanned exactly once across capped windows."""
        listing = _PagedListing(600)
        keep = lambda i: i.id % 40 == 0  # noqa: E731
        cursor: str | None = None
        windows: list[PageResult] = []
        for _ in range(20):
            windows.append(_scan(listing, limit=5, keep=keep, cursor=cursor))
            cursor = windows[-1].next_cursor
            if cursor is None:
                break

        assert listing.calls == list(range(1, 41))
        assert all(w.capped for w in windows[:-1])
        assert [w.next_cursor for w in windows[:2]] == ["2:6.0", "3:11.0"]
        assert [i.id for w in windows for i in w.items] == list(range(0, 600, 40))
        assert windows[-1].next_cursor is None
        assert windows[-1].capped is False

    def test_capped_window_can_be_empty_but_never_claims_the_end(self) -> None:
        listing = _PagedListing(600)
        result = _scan(listing, limit=5, keep=lambda i: i.id == 599, cursor=None)
        assert result.items == []
        assert result.capped is True
        assert result.next_cursor == "2:6.0"
        assert len(listing.calls) == MAX_API_CALLS

    @pytest.mark.parametrize(("limit", "per_page"), [(4, 12), (34, 100), (100, 100)])
    def test_overfetches_three_times_the_limit_up_to_a_full_page(
        self, limit: int, per_page: int
    ) -> None:
        listing = _PagedListing(1)
        _scan(listing, limit=limit, keep=lambda _: True, cursor=None)
        assert listing.per_pages == [per_page]

    def test_records_a_scan_note(self, monkeypatch: pytest.MonkeyPatch) -> None:
        notes: list[trace.ScanNote] = []
        monkeypatch.setattr(trace, "note_scan", notes.append)

        _scan(_PagedListing(30), limit=5, keep=lambda i: i.id >= 10, cursor=None)
        assert notes == [trace.ScanNote(api_calls=1, rows=15, kept=5, capped=False)]

        _scan(_PagedListing(600), limit=5, keep=lambda i: i.id == 599, cursor=None)
        assert notes[-1] == trace.ScanNote(
            api_calls=MAX_API_CALLS, rows=75, kept=0, capped=True
        )


@contextlib.contextmanager
def _search_client(
    respond: Callable[[httpx2.Request], httpx2.Response], sends: list[str]
) -> Generator[Discogs]:
    """A real client whose every request gets `respond`'s answer, with an
    in-memory cache; `sends` records each request that reached the network."""

    def _handle(request: httpx2.Request) -> httpx2.Response:
        sends.append(request.url.path)
        return respond(request)

    with (
        httpx2.Client(transport=httpx2.MockTransport(_handle)) as http,
        Discogs(
            # The SDK skips its cache for an injected client with no auth.
            token="fake",  # noqa: S106  # placeholder, never reaches Discogs
            cache=True,
            http_client=http,
            max_retries=0,
        ) as client,
    ):
        yield client


class TestFetchPage:
    def test_fetch_page(self) -> None:
        sends: list[str] = []
        body = {
            "pagination": {"page": 2, "items": 12, "pages": 3, "per_page": 5},
            "results": [{"id": 1, "title": "one"}, {"id": 2, "title": "two"}],
        }
        params: list[dict[str, str]] = []

        def _respond(request: httpx2.Request) -> httpx2.Response:
            params.append(dict(request.url.params))
            return httpx2.Response(200, json=body)

        with _search_client(_respond, sends) as client:
            listing = functools.partial(client.search, q="x", type="release")
            result = fetch_page(listing, page=2, per_page=5)

        assert params == [{"q": "x", "type": "release", "page": "2", "per_page": "5"}]
        assert (result.page, result.total_items, result.total_pages) == (2, 12, 3)
        assert [(i.id, i.title) for i in result.items] == [(1, "one"), (2, "two")]
        assert sends == ["/database/search"]

    def test_absent_pagination_reads_as_one_page(self) -> None:
        """A body without `pagination` is one page holding the rows it sent."""
        sends: list[str] = []
        body = {"results": [{"id": 1, "title": "one"}, {"id": 2, "title": "two"}]}
        with _search_client(lambda _r: httpx2.Response(200, json=body), sends) as c:
            result = fetch_page(functools.partial(c.search, q="x"), page=3, per_page=5)
        assert (result.page, result.total_items, result.total_pages) == (1, 2, 1)
        assert [i.id for i in result.items] == [1, 2]

    @pytest.mark.parametrize(
        "body",
        [
            {"pagination": {"page": 1}, "results": {}},
            {"pagination": [], "results": []},
            ["not", "an", "object"],
        ],
        ids=["results-object", "pagination-list", "body-list"],
    )
    def test_malformed_envelope_raises_and_is_never_cached(self, body: object) -> None:
        """The SDK's envelope checks apply, and a page that fails them is not
        cached, so the next call asks the API again."""
        sends: list[str] = []
        with _search_client(lambda _r: httpx2.Response(200, json=body), sends) as c:
            for _ in range(2):
                with pytest.raises(DiscogsError, match="expected an"):
                    fetch_page(functools.partial(c.search, q="x"), page=1, per_page=5)
        assert sends == ["/database/search", "/database/search"]

    def test_body_that_is_not_json_is_never_cached(self) -> None:
        """A 200 whose body is not JSON (a gateway page) fails every time, so a
        later call must reach the API again instead of reading it from cache."""
        sends: list[str] = []
        gateway = httpx2.Response(200, text="<html>Bad gateway</html>")
        with _search_client(lambda _r: gateway, sends) as client:
            for _ in range(2):
                with pytest.raises(json.JSONDecodeError):
                    fetch_page(functools.partial(client.search), page=1, per_page=5)
        assert sends == ["/database/search", "/database/search"]
