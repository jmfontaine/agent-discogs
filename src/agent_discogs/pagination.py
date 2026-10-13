"""Pagination helpers that expose full pagination metadata from Discogs API."""

from __future__ import annotations

import math
import shlex
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

import click
from discogs_sdk import RateLimitError, SyncPage

from agent_discogs import trace

DEFAULT_LIMIT = 5
MAX_LIMIT = 100  # Discogs' largest per_page

Listing = Callable[..., SyncPage[Any]]
"""A paginated SDK resource method with its filters bound, such as
`functools.partial(client.search, q="x")` or `artist.releases.list`. It is
called with `page` and `per_page` keywords and returns the listing they name."""


def paging_error(limit: int, page: int | None) -> str | None:
    """Why `--limit`/`--page` cannot be sent, or None. Checked before any
    request: a non-positive limit can never fill a page, so a filtered scan
    would read the whole listing for it."""
    if not 1 <= limit <= MAX_LIMIT:
        return f"--limit must be between 1 and {MAX_LIMIT} ({limit} given)."
    if page is not None and page < 1:
        return f"--page must be 1 or more ({page} given)."
    return None


def max_requests_error(max_requests: int | None) -> str | None:
    """Why `--max-requests` cannot cap a scan, or None. Checked before any
    request: a cap below one could never send the first page."""
    if max_requests is not None and max_requests < 1:
        return f"--max-requests must be 1 or more ({max_requests} given)."
    return None


def sort_params(sort: str | None, desc: bool) -> dict[str, str]:
    """Server-side ordering; `--desc` only means something with `--sort`."""
    if not sort:
        return {}
    return {"sort": sort, "sort_order": "desc" if desc else "asc"}


@dataclass(frozen=True)
class ScanStop:
    """Why a client-side scan returned before filling its page or reaching the
    end of the listing: the command's `--max-requests` budget ran out
    (`max_requests` is that budget), or Discogs answered 429 after the scan
    had read at least one page (`retry_after`, in whole seconds rounded up,
    when Discogs said)."""

    reason: Literal["max_requests", "rate_limited"]
    max_requests: int | None = None
    retry_after: int | None = None


@dataclass
class PageResult:
    """A single page of results with pagination metadata.

    For client-side filtered pages (`filtered=True`), `total_items` is the
    unfiltered API total (an upper bound), and continuation is expressed by
    `next_cursor` rather than by page arithmetic. Both totals are None when
    the scan stopped before it read any listing page, so nothing is known
    about the listing yet. `stop` says why the scan stopped short; the caller
    then presents the cursor as "continue scanning" rather than "next page".
    """

    items: list[Any]
    page: int
    total_items: int | None
    total_pages: int | None
    filtered: bool = False
    stop: ScanStop | None = None
    next_cursor: str | None = None

    @property
    def capped(self) -> bool:
        """The scan stopped before filling the page or reaching the end."""
        return self.stop is not None

    @property
    def has_next(self) -> bool:
        if self.filtered:
            return self.next_cursor is not None
        return self.total_pages is not None and self.page < self.total_pages


def parse_cursor(cursor: str | None) -> tuple[int, int, int]:
    """Decode a continuation cursor into (display_page, api_page, offset).

    The cursor is opaque to agents: `"<display_page>:<row>"`, where `row` is
    the 0-based position in the listing of the first row not scanned yet,
    copied verbatim from a previous `Next page:`/`Continue scan:` line. It
    names a row rather than an API page, so it does not depend on the scan's
    page size; a cursor from an older version (`2:3.4`) is rejected rather
    than read at another size.
    """
    if cursor is None:
        return 1, 1, 0
    try:
        page_str, row_str = cursor.split(":", 1)
        page, row = int(page_str), int(row_str)
    except ValueError:
        page = row = -1
    if page < 1 or row < 0:
        raise ValueError(
            f"Invalid --after cursor {cursor!r}. "
            "Copy it from the previous Next page / Continue scan line."
        )
    api_page, offset = divmod(row, MAX_LIMIT)
    return page, api_page + 1, offset


def _cursor(page: int, api_page: int, offset: int) -> str:
    """The cursor naming row `offset` of 100-row API page `api_page`."""
    return f"{page}:{(api_page - 1) * MAX_LIMIT + offset}"


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
        return _cursor(page + 1, api_page, next_idx)
    if api_page < total_pages:
        return _cursor(page + 1, api_page + 1, 0)
    return None


def rate_limited_stop(exc: RateLimitError) -> ScanStop:
    """The stop for a 429, with Discogs' Retry-After in whole seconds, rounded
    up as the error envelope rounds it, so a retry is never suggested before
    the server asked."""
    seconds = exc.retry_after_seconds
    return ScanStop(
        "rate_limited", retry_after=None if seconds is None else math.ceil(seconds)
    )


def fetch_filtered_page(
    listing: Listing,
    *,
    limit: int | None,
    keep: Callable[[Any], bool],
    cursor: str | None = None,
    max_requests: int | None = None,
) -> PageResult:
    """Fetch one page of client-side filtered results, resuming from `cursor`.

    The API has no server-side filter for these cases, so we scan raw API
    pages of Discogs' largest size (100 rows) and keep matching items until
    the page is full or the listing ends. Scanning resumes exactly where the
    previous call stopped, never rescanning from page 1. A `limit` of None
    reads the whole listing (the `--owned` collection read).

    It stops short, with a `stop` and a cursor to continue from, in two
    cases. `max_requests` caps the HTTP sends of the whole command, header
    lookups included: once the trace counts that many, no further page is
    asked for, so the page can be short or empty. A 429 after at least one
    page returns the rows read so far; a 429 on the first page propagates.

    A returned cursor guarantees unscanned raw rows remain, not that they
    match `keep`.
    """
    page, api_page, offset = parse_cursor(cursor)
    collected: list[Any] = []
    next_cursor: str | None = None
    stop: ScanStop | None = None
    api_calls = rows = 0
    total_items: int | None = None
    total_pages: int | None = None

    while True:
        if max_requests is not None and trace.sends() >= max_requests:
            stop = ScanStop("max_requests", max_requests=max_requests)
            break
        try:
            result = fetch_page(listing, page=api_page, per_page=MAX_LIMIT)
        except RateLimitError as exc:
            if not api_calls:
                raise
            stop = rate_limited_stop(exc)
            break
        api_calls += 1
        rows += len(result.items)
        # fetch_page always reports both totals.
        total_items, last_page = result.total_items, result.total_pages or 0
        total_pages = last_page

        for idx in range(offset, len(result.items)):
            item = result.items[idx]
            if not keep(item):
                continue
            collected.append(item)
            if len(collected) == limit:  # never with no limit
                next_cursor = _advance(
                    page, api_page, idx + 1, len(result.items), last_page
                )
                break

        if next_cursor or api_page >= last_page:
            break
        api_page, offset = api_page + 1, 0

    if stop is not None:
        # `api_page` and `offset` name the first row not scanned yet, and the
        # loop only went on because that row exists. A scan that read nothing
        # continues on the same page number, from where it was asked to start.
        next_cursor = _cursor(page + 1 if api_calls else page, api_page, offset)

    reason = None if stop is None else stop.reason
    trace.note_scan(trace.ScanNote(api_calls, rows, len(collected), reason))
    return PageResult(
        items=collected,
        page=page,
        total_items=total_items,
        total_pages=total_pages,
        filtered=True,
        stop=stop,
        next_cursor=next_cursor,
    )
