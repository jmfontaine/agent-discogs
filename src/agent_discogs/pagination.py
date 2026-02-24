"""Pagination helpers that expose full pagination metadata from Discogs API."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from discogs_sdk import Discogs
from pydantic import BaseModel

MAX_API_CALLS = 5


@dataclass
class PageResult:
    """A single page of results with pagination metadata."""

    items: list[Any]
    page: int
    total_items: int
    total_pages: int

    @property
    def has_next(self) -> bool:
        return self.page < self.total_pages


def fetch_page(
    client: Discogs,
    path: str,
    params: dict[str, Any],
    model_cls: type[BaseModel],
    items_key: str,
) -> PageResult:
    """Fetch a single page from the Discogs API with full pagination metadata.

    This bypasses SyncPage because SyncPage is an auto-paging iterator:
    consuming all items on a page triggers a fetch of the next page.
    We need exactly one page of items without triggering additional requests.
    SyncPage exposes pagination metadata (total_items, total_pages, etc.)
    but no way to access the current page's items without iterating.
    """
    url = client._build_url(path)
    response = client._send("GET", url, params=params)
    body = response.json()
    client._maybe_raise(
        response.status_code,
        body,
        retry_after=response.headers.get("Retry-After"),
    )

    pagination = body.get("pagination", {})
    raw_items = body.get(items_key, [])
    items = [model_cls.model_validate(item) for item in raw_items]

    return PageResult(
        items=items,
        page=pagination.get("page", 1),
        total_items=pagination.get("items", len(items)),
        total_pages=pagination.get("pages", 1),
    )


def fetch_filtered_page(
    client: Discogs,
    path: str,
    params: dict[str, Any],
    model_cls: type[BaseModel],
    items_key: str,
    user_page: int,
    limit: int,
    keep: Callable[[Any], bool],
) -> PageResult:
    """Fetch results with client-side filtering, over-fetching as needed.

    For user page N with limit L, we need filtered results at indices
    (N-1)*L through N*L. Since the API has no server-side filter,
    we process from API page 1 and collect matching items.
    """
    items_to_skip = (user_page - 1) * limit
    collected: list[Any] = []
    skipped = 0
    api_page = 1
    api_per_page = limit * 3  # overfetch to reduce API calls
    api_total_items = 0
    api_total_pages = 1

    for _ in range(MAX_API_CALLS):
        fetch_params = {**params, "page": api_page, "per_page": api_per_page}
        result = fetch_page(client, path, fetch_params, model_cls, items_key)
        api_total_items = result.total_items
        api_total_pages = result.total_pages

        if not result.items:
            break

        for item in result.items:
            if not keep(item):
                continue
            if skipped < items_to_skip:
                skipped += 1
                continue
            collected.append(item)
            if len(collected) >= limit:
                break

        if len(collected) >= limit:
            break
        if api_page >= api_total_pages:
            break
        api_page += 1

    return PageResult(
        items=collected[:limit],
        page=user_page,
        total_items=api_total_items,
        total_pages=api_total_pages,
    )
