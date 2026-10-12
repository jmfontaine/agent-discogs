"""Pagination helpers that expose full pagination metadata from Discogs API."""

from __future__ import annotations

import shlex
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import click
from discogs_sdk import SyncPage

from agent_discogs import trace
from agent_discogs.config import MAX_API_CALLS

DEFAULT_LIMIT = 5
MAX_LIMIT = 100  # Discogs' largest per_page

Listing = Callable[..., SyncPage[Any]]
"""A paginated SDK resource method with its filters bound, such as
`functools.partial(client.search, q="x")` or `artist.releases.list`. It is
called with `page` and `per_page` keywords and returns the listing they name."""


def paging_error(limit: int, page: int | None) -> str | None:
    """Why `--limit`/`--page` cannot be sent, or None. Checked before any
    request: a non-positive limit can never fill a page, so a filtered scan
    would spend its whole request budget on it."""
    if not 1 <= limit <= MAX_LIMIT:
        return f"--limit must be between 1 and {MAX_LIMIT} ({limit} given)."
    if page is not None and page < 1:
        return f"--page must be 1 or more ({page} given)."
    return None


def sort_params(sort: str | None, desc: bool) -> dict[str, str]:
    """Server-side ordering; `--desc` only means something with `--sort`."""
    if not sort:
        return {}
    return {"sort": sort, "sort_order": "desc" if desc else "asc"}


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


def next_page_cmd(
    argv: list[str],
    result: PageResult,
    *,
    limit: int,
    positionals: tuple[str, ...] = (),
    **flags: object,
) -> str | None:
    """The shell-safe command that continues `result`, or None at the end.

    It continues by cursor (`--after`) after a client-side filtered page and
    by `--page N+1` otherwise, keeps a non-default `--limit`, and repeats this
    invocation's global `--cached`/`--fresh` before the subcommand, so the
    next page is read under the same cache policy. `positionals` (user text,
    such as a search query) follow an explicit `--`, so a query starting with
    `-` is never parsed as an option.

    Flags whose value is None/""/False are omitted; `True` renders as a bare
    switch; underscores become dashes. Every token is quoted by `shlex.join`,
    so titles with quotes and labels like `R & S Records` paste back unchanged.
    """
    if not result.has_next:
        return None
    flags.update(
        limit=limit if limit != DEFAULT_LIMIT else None,
        after=result.next_cursor,
        page=None if result.filtered else result.page + 1,
    )
    parts = ["agent-discogs", *_cache_flags(), *argv]
    for flag, value in flags.items():
        if value in (None, "", False):
            continue
        parts.append(f"--{flag.replace('_', '-')}")
        if value is not True:
            parts.append(str(value))
    if positionals:
        parts += ["--", *positionals]
    return shlex.join(parts)


def _cache_flags() -> list[str]:
    """The global cache flag (`--cached`/`--fresh`) this invocation runs under.

    Every caller builds a footer inside a click command, so there is always a
    current context.
    """
    params = click.get_current_context().find_root().params
    return [f"--{name}" for name in ("cached", "fresh") if params.get(name)]


def fetch_page(listing: Listing, *, page: int, per_page: int) -> PageResult:
    """Fetch a single page from the Discogs API with full pagination metadata.

    `SyncPage.fetch()` sends exactly one request, where iterating the listing
    would fetch the next page as soon as this one runs out. The SDK checks the
    envelope and validates the items, and keeps a page that fails either out
    of the cache. Metadata Discogs leaves out reads as one page holding the
    items it sent.
    """
    fetched = listing(page=page, per_page=per_page).fetch()
    return PageResult(
        items=fetched.items,
        page=1 if fetched.page is None else fetched.page,
        total_items=(
            len(fetched.items) if fetched.total_items is None else fetched.total_items
        ),
        total_pages=1 if fetched.total_pages is None else fetched.total_pages,
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
    listing: Listing,
    *,
    limit: int,
    keep: Callable[[Any], bool],
    cursor: str | None = None,
) -> PageResult:
    """Fetch one page of client-side filtered results, resuming from `cursor`.

    The API has no server-side filter for these cases, so we scan raw API
    pages (overfetching 3x, at most Discogs' 100 rows per page) and keep
    matching items. Scanning resumes exactly
    where the previous call stopped, never rescanning from page 1. At most
    `MAX_API_CALLS` requests per call: a sparse filter can return a short (even
    empty) page with `capped=True` and a cursor to continue from.

    A returned cursor guarantees unscanned raw rows remain, not that they
    match `keep`.
    """
    page, api_page, offset = parse_cursor(cursor)
    api_per_page = min(limit * 3, MAX_LIMIT)
    collected: list[Any] = []
    next_cursor: str | None = None
    capped = False
    api_calls = rows = 0
    result = PageResult(items=[], page=page, total_items=0, total_pages=1)

    for _ in range(MAX_API_CALLS):
        result = fetch_page(listing, page=api_page, per_page=api_per_page)
        api_calls += 1
        rows += len(result.items)

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

    trace.note_scan(trace.ScanNote(api_calls, rows, len(collected), capped))
    return PageResult(
        items=collected,
        page=page,
        total_items=result.total_items,
        total_pages=result.total_pages,
        filtered=True,
        capped=capped,
        next_cursor=next_cursor,
    )
