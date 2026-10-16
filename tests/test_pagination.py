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
from discogs_sdk import Discogs, DiscogsError, RateLimitError
from discogs_sdk import PageResult as SdkPageResult
from pydantic import BaseModel

from agent_discogs import trace
from agent_discogs.json_output import dumps, page_document
from agent_discogs.pagination import (
    DropRepeats,
    PageResult,
    ScanStop,
    fetch_filtered_page,
    fetch_page,
    keep_all,
    parse_cursor,
    without_repeats,
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
            next_cursor="4:103",
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
        """A cursor names an absolute row: row 107 is row 7 of 100-row page 2."""
        assert parse_cursor("3:107") == (3, 2, 7)
        assert parse_cursor("3:100") == (3, 2, 0)

    @pytest.mark.parametrize("bad", ["2", "2:x", "0:1", "2:-1", "2:", "2:1.0", "2:3.4"])
    def test_invalid(self, bad: str) -> None:
        """Including the `<page>:<api_page>.<offset>` cursors older versions
        printed, which named pages of another size."""
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
    `page`/`per_page`, it names that page, and `fetch()` returns it, or raises
    a 429 for the pages in `throttle`. `calls` lists the pages fetched and
    `per_pages` the page sizes asked for."""

    def __init__(self, total: int, *, throttle: tuple[int, ...] = ()) -> None:
        self.total = total
        self.throttle = throttle
        self.calls: list[int] = []
        self.per_pages: list[int] = []

    def __call__(self, *, page: int, per_page: int) -> SimpleNamespace:
        def fetch() -> SdkPageResult[_FakeModel]:
            if page in self.throttle:
                raise RateLimitError(
                    "slow",
                    status_code=429,
                    response_body={},
                    retry_after="12.5",
                    retry_after_seconds=12.5,
                )
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
    listing: Any,
    *,
    limit: int,
    keep: Callable[[Any], bool],
    cursor: str | None,
    max_requests: int | None = None,
) -> PageResult:
    return fetch_filtered_page(
        listing, limit=limit, keep=keep, cursor=cursor, max_requests=max_requests
    )


class TestFetchFilteredPage:
    def test_filters_items(self) -> None:
        items = [
            _FakeModel(id=1, name="keep"),
            _FakeModel(id=2, name="skip"),
            _FakeModel(id=3, name="keep-too"),
        ]
        page = SdkPageResult(
            items=items, page=1, per_page=100, total_items=3, total_pages=1, urls={}
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
        assert first.next_cursor == "2:5"
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
        listing = _PagedListing(200)  # 100 rows a page -> 2 API pages
        result = _scan(listing, limit=5, keep=lambda i: i.id >= 95, cursor=None)
        assert [i.id for i in result.items] == [95, 96, 97, 98, 99]
        assert result.next_cursor == "2:100"

    def test_fill_at_last_raw_row_ends_without_cursor(self) -> None:
        """Filling the page on the final row of the final API page is the end."""
        result = _scan(_PagedListing(5), limit=5, keep=lambda _: True, cursor=None)
        assert len(result.items) == 5
        assert result.next_cursor is None
        assert result.has_next is False

    def test_sparse_filter_scans_to_the_end(self) -> None:
        """No cap by default: every API page is read once, and a page the
        listing runs out on is the end, however short."""
        listing = _PagedListing(1000)
        result = _scan(listing, limit=5, keep=lambda i: i.id in {50, 950}, cursor=None)
        assert listing.calls == list(range(1, 11))
        assert [i.id for i in result.items] == [50, 950]
        assert result.capped is False
        assert result.next_cursor is None

    @pytest.mark.parametrize("limit", [1, 5, 100])
    def test_requests_full_pages(self, limit: int) -> None:
        listing = _PagedListing(250)
        _scan(listing, limit=limit, keep=lambda i: i.id == 249, cursor=None)
        assert listing.per_pages == [100, 100, 100]

    def test_max_requests_stops_and_resumes_without_gaps(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The cap counts the command's sends; here every page read is one."""
        listing = _PagedListing(1000)
        monkeypatch.setattr(trace, "sends", lambda: len(listing.calls))
        keep = lambda i: i.id % 200 == 50  # noqa: E731  # pages 1, 3, 5, 7, 9

        first = _scan(listing, limit=5, keep=keep, cursor=None, max_requests=3)
        assert listing.calls == [1, 2, 3]
        assert [i.id for i in first.items] == [50, 250]
        assert first.stop == ScanStop("max_requests", max_requests=3)
        assert first.capped is True
        assert first.next_cursor == "2:300"
        assert (first.total_items, first.total_pages) == (1000, 10)

        listing.calls.clear()
        second = _scan(listing, limit=5, keep=keep, cursor="2:300", max_requests=3)
        assert listing.calls == [4, 5, 6]
        assert [i.id for i in second.items] == [450]
        assert second.next_cursor == "3:600"

    @pytest.mark.parametrize(
        ("cursor", "page", "continuation"),
        [(None, 1, "1:0"), ("3:307", 3, "3:307")],
    )
    def test_budget_spent_before_the_scan(
        self,
        monkeypatch: pytest.MonkeyPatch,
        cursor: str | None,
        page: int,
        continuation: str,
    ) -> None:
        """Header lookups used the whole budget: nothing is read, nothing is
        known about the listing, and the cursor starts where this scan would."""
        monkeypatch.setattr(trace, "sends", lambda: 1)
        listing = _PagedListing(1000)
        result = _scan(
            listing, limit=5, keep=lambda _: True, cursor=cursor, max_requests=1
        )
        assert listing.calls == []
        assert result.items == []
        assert (result.total_items, result.total_pages) == (None, None)
        assert result.next_cursor == continuation

        document = json.loads(dumps(page_document(result, lambda i: i, full=False)))
        assert document["pagination"] == {
            "page": page,
            "total_items": None,
            "total_pages": None,
            "filtered": True,
            "capped": True,
            "next_cursor": continuation,
            "stop_reason": "max_requests",
        }

    def test_rate_limit_after_a_page_returns_what_was_read(self) -> None:
        listing = _PagedListing(1000, throttle=(4,))
        keep = lambda i: i.id % 100 == 0  # noqa: E731
        result = _scan(listing, limit=5, keep=keep, cursor=None)
        assert listing.calls == [1, 2, 3]  # page 4 raised
        assert [i.id for i in result.items] == [0, 100, 200]
        assert result.stop == ScanStop("rate_limited", retry_after=13)  # 12.5 up
        assert result.next_cursor == "2:300"

    def test_rate_limit_on_the_first_page_raises(self) -> None:
        listing = _PagedListing(1000, throttle=(2,))
        with pytest.raises(RateLimitError):
            _scan(listing, limit=5, keep=lambda _: True, cursor="1:100")

    def test_records_a_scan_note(self, monkeypatch: pytest.MonkeyPatch) -> None:
        notes: list[trace.ScanNote] = []
        monkeypatch.setattr(trace, "note_scan", notes.append)

        _scan(_PagedListing(30), limit=5, keep=lambda i: i.id >= 10, cursor=None)
        assert notes == [trace.ScanNote(api_calls=1, rows=30, kept=5, stop=None)]

        listing = _PagedListing(600)
        monkeypatch.setattr(trace, "sends", lambda: len(listing.calls))
        _scan(listing, limit=5, keep=lambda i: i.id == 599, cursor=None, max_requests=2)
        assert notes[-1] == trace.ScanNote(
            api_calls=2, rows=200, kept=0, stop="max_requests"
        )


class TestDropRepeats:
    """Rows are `_FakeModel`s built separately, so a repeat is `==` by value,
    as two rows Discogs sent alike are."""

    @staticmethod
    def _rows(*names: str) -> list[_FakeModel]:
        return [_FakeModel(id=ord(name[0]), name=name) for name in names]

    def test_a_run_keeps_its_first_row(self) -> None:
        keep = DropRepeats(keep_all)
        decided = [keep(row) for row in self._rows("a", "a", "a", "b", "a")]
        assert decided == [True, False, False, True, True]
        assert keep.dropped == 2

    def test_the_row_before_counts_even_when_not_kept(self) -> None:
        """A repeat of a row `keep` refuses is not counted, and a row `keep`
        refused still separates two equal rows."""
        keep = DropRepeats(lambda row: row.name.startswith("k"))
        rows = self._rows("skip", "skip", "keep", "keep", "kept", "s", "kept")
        assert [keep(row) for row in rows] == [
            *(False, False, True, False, True, False, True)
        ]
        assert keep.dropped == 1

    def test_without_repeats_keeps_the_totals(self) -> None:
        result = PageResult(self._rows("a", "a", "b"), 3, 40, 8)
        dropped = without_repeats(result)
        assert [row.name for row in dropped.items] == ["a", "b"]
        assert dropped.duplicates_dropped == 1
        assert (dropped.page, dropped.total_items, dropped.total_pages) == (3, 40, 8)
        pagination = page_document(dropped, dict, full=False)["pagination"]
        assert pagination["duplicates_dropped"] == 1

    def test_a_scan_compares_across_its_pages(self) -> None:
        """The scan shows `keep` every row in order, so the last row of one
        100-row page and the first of the next are compared too."""
        listing = _PagedListing(150)
        keep = DropRepeats(lambda row: row.id >= 99)
        original = listing.__call__

        def same_edge(*, page: int, per_page: int) -> SimpleNamespace:
            fetched = original(page=page, per_page=per_page)
            result = fetched.fetch()
            if page == 2:
                result.items[0] = _FakeModel(id=99, name="row99")
            return SimpleNamespace(fetch=lambda: result)

        result = _scan(same_edge, limit=3, keep=keep, cursor=None)
        assert [row.id for row in result.items] == [99, 101, 102]
        assert keep.dropped == 1
        assert result.next_cursor == "2:103"


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
