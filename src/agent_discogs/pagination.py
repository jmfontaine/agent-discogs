"""Pagination helpers that expose full pagination metadata from Discogs API."""

from __future__ import annotations

import shlex
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from discogs_sdk import Discogs
from pydantic import BaseModel

MAX_API_CALLS = 5
DEFAULT_LIMIT = 5


@dataclass
class PageResult:
    """A single page of results with pagination metadata.

    For client-side filtered pages (`filtered=True`), `total_items` is the
    unfiltered API total (an upper bound), and continuation is expressed by
    `next_cursor` rather than by page arithmetic. `capped` means the scan hit
    `MAX_API_CALLS` before filling the page, so the caller should present the
    cursor as "continue scanning" rather than "next page".
    """

    items: list[Any]
    page: int
    total_items: int
    total_pages: int
    filtered: bool = False
    capped: bool = False
    next_cursor: str | None = None

    @property
    def has_next(self) -> bool:
        if self.filtered:
            return self.next_cursor is not None
        return self.page < self.total_pages


def parse_cursor(cursor: str | None) -> tuple[int, int, int]:
    """Decode a continuation cursor into (display_page, api_page, offset).

    The cursor is opaque to agents: `"<display_page>:<api_page>.<offset>"`,
    copied verbatim from a previous `Next page:`/`Continue scan:` line.
    """
    if cursor is None:
        return 1, 1, 0
    try:
        page_str, rest = cursor.split(":", 1)
        api_str, offset_str = rest.split(".", 1)
        page, api_page, offset = int(page_str), int(api_str), int(offset_str)
    except ValueError:
        page = api_page = offset = 0
    if page < 1 or api_page < 1 or offset < 0:
        raise ValueError(
            f"Invalid --after cursor {cursor!r}. "
            "Copy it from the previous Next page / Continue scan line."
        )
    return page, api_page, offset


def next_page_cmd(argv: list[str], **flags: object) -> str:
    """Build a shell-safe continuation command from argv and CLI flags.

    Flags whose value is None/""/False are omitted; `True` renders as a bare
    switch; underscores become dashes. Every token is quoted by `shlex.join`,
    so titles with quotes and labels like `R & S Records` paste back unchanged.
    """
    parts = ["agent-discogs", *argv]
    for flag, value in flags.items():
        if value in (None, "", False):
            continue
        parts.append(f"--{flag.replace('_', '-')}")
        if value is not True:
            parts.append(str(value))
    return shlex.join(parts)


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

    `_send()` is the SDK's HTTP-error boundary: it maps a failing response to
    the matching DiscogsAPIError subclass before returning, so the body parsed
    below is always a success payload.
    """
    url = client._build_url(path)  # noqa: SLF001
    response = client._send("GET", url, params=params)  # noqa: SLF001
    body = response.json()

    pagination = body.get("pagination", {})
    raw_items = body.get(items_key, [])
    items = [model_cls.model_validate(item) for item in raw_items]

    return PageResult(
        items=items,
        page=pagination.get("page", 1),
        total_items=pagination.get("items", len(items)),
        total_pages=pagination.get("pages", 1),
    )


def _advance(
    page: int, api_page: int, next_idx: int, n_items: int, total_pages: int
) -> str | None:
    """Cursor for the row after the one that filled the page, or None at the end."""
    if next_idx < n_items:
        return f"{page + 1}:{api_page}.{next_idx}"
    if api_page < total_pages:
        return f"{page + 1}:{api_page + 1}.0"
    return None


def fetch_filtered_page(
    client: Discogs,
    path: str,
    params: dict[str, Any],
    model_cls: type[BaseModel],
    items_key: str,
    *,
    limit: int,
    keep: Callable[[Any], bool],
    cursor: str | None = None,
) -> PageResult:
    """Fetch one page of client-side filtered results, resuming from `cursor`.

    The API has no server-side filter for these cases, so we scan raw API
    pages (over-fetching 3x) and keep matching items. Scanning resumes exactly
    where the previous call stopped, never rescanning from page 1. At most
    `MAX_API_CALLS` requests per call: a sparse filter can return a short (even
    empty) page with `capped=True` and a cursor to continue from.

    A returned cursor guarantees unscanned raw rows remain, not that they
    match `keep`.
    """
    page, api_page, offset = parse_cursor(cursor)
    api_per_page = limit * 3
    collected: list[Any] = []
    next_cursor: str | None = None
    capped = False
    result = PageResult(items=[], page=page, total_items=0, total_pages=1)

    for _ in range(MAX_API_CALLS):
        fetch_params = {**params, "page": api_page, "per_page": api_per_page}
        result = fetch_page(client, path, fetch_params, model_cls, items_key)

        for idx in range(offset, len(result.items)):
            item = result.items[idx]
            if not keep(item):
                continue
            collected.append(item)
            if len(collected) == limit:
                next_cursor = _advance(
                    page, api_page, idx + 1, len(result.items), result.total_pages
                )
                break

        if next_cursor or api_page >= result.total_pages:
            break
        api_page, offset = api_page + 1, 0
    else:
        # Cap hit with the page unfilled. `api_page` already names the first
        # unscanned API page (the loop body advanced it), and the loop only
        # continued because that page exists.
        capped = True
        next_cursor = f"{page + 1}:{api_page}.0"

    return PageResult(
        items=collected,
        page=page,
        total_items=result.total_items,
        total_pages=result.total_pages,
        filtered=True,
        capped=capped,
        next_cursor=next_cursor,
    )
