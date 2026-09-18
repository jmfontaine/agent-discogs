"""Per-invocation request trace: the budget footer and the --debug panel.

One process-wide collector, reset by `begin()` at the top of every invocation
and drained by `finish()` when the click context closes. `record()` is the
SDK's `on_request` hook; `note_scan()` is called by client-side scans.
Everything printed here goes to stderr so stdout stays a pure data channel.
"""

from __future__ import annotations

import logging
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

if TYPE_CHECKING:
    import click
    from discogs_sdk import RequestEvent

# Remaining requests at which the footer warns. One client-side scan costs up
# to MAX_API_CALLS (5) requests; two back-to-back scans can spend 10.
LOW_BUDGET = 10
# Passed to Discogs(cache_ttl=...) and shown by --debug.
CACHE_TTL = 3600.0

_SDK_LOGGER = "discogs_sdk"


@dataclass
class ScanNote:
    """What one `fetch_filtered_page` call spent and kept."""

    api_calls: int
    rows: int
    kept: int
    capped: bool


class _CountingWriter:
    """Proxy around a text stream that counts what was written to it."""

    def __init__(self, target: Any) -> None:
        self.target: Any = target
        self.chars: int = 0
        self.lines: int = 0

    def write(self, s: str) -> int:
        n = self.target.write(s)
        self.chars += len(s)
        self.lines += s.count("\n")
        return n

    def flush(self) -> None:
        self.target.flush()

    def __getattr__(self, name: str) -> Any:
        return getattr(self.target, name)


@dataclass
class Trace:
    events: list[RequestEvent] = field(default_factory=list)
    scans: list[ScanNote] = field(default_factory=list)
    started: float = 0.0
    debug: bool = False
    cache_misses: int = 0
    stdout: _CountingWriter | None = None
    log_handler: logging.Handler | None = None
    log_level: int = logging.NOTSET


_trace = Trace()


def begin(ctx: click.Context, *, debug: bool) -> None:
    """Reset the trace for this invocation and arrange for `finish()` to run
    when `ctx` closes, i.e. after the subcommand, success or failure."""
    global _trace  # noqa: PLW0603  # one invocation per process; reset for tests
    _trace = Trace(started=time.perf_counter(), debug=debug)
    if debug:
        _trace.stdout = _CountingWriter(sys.stdout)
        sys.stdout = _trace.stdout  # type: ignore[assignment]
        handler = logging.StreamHandler(sys.stderr)
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(logging.Formatter("[sdk] %(message)s"))
        logger = logging.getLogger(_SDK_LOGGER)
        _trace.log_level = logger.level
        logger.setLevel(logging.DEBUG)
        logger.addHandler(handler)
        _trace.log_handler = handler
    ctx.call_on_close(finish)


def record(event: RequestEvent) -> None:
    """SDK `on_request` hook: one event per request, cache hit or network."""
    _trace.events.append(event)


def note_scan(note: ScanNote) -> None:
    """Record the cost of one client-side filtered scan."""
    _trace.scans.append(note)


def note_cache_miss() -> None:
    """Record a `--cached` lookup the cache could not serve. The SDK emits no
    event for it, so this is the only trace such a request leaves."""
    _trace.cache_misses += 1


def render_footer(
    events: Sequence[RequestEvent], *, authenticated: bool, cache_misses: int = 0
) -> str | None:
    """The budget line, or None when nothing was requested.

    Counts network requests only; the budget comes from the last network
    response that reported rate-limit headers, so cache hits never move it.
    A `--cached` miss counts as a request that cost nothing.
    """
    if not events and not cache_misses:
        return None
    network = [e for e in events if e.source == "network"]
    n = len(network)
    if n == 0:
        return "api: 0 requests · cached"
    line = f"api: {n} request" + ("" if n == 1 else "s")
    rl = next((e.ratelimit for e in reversed(network) if e.ratelimit), None)
    if rl is None:
        return line
    line += f" · {rl.remaining}/{rl.limit} left this minute"
    if rl.remaining <= LOW_BUDGET:
        line += (
            " ⚠ pause ~60s before uncached calls"
            if authenticated
            else " ⚠ set DISCOGS_TOKEN for 60/min, or pause ~60s"
        )
    return line


def _request_path(url: str) -> str:
    split = urlsplit(url)
    return split.path + (f"?{split.query}" if split.query else "")


_KB = 1024


def _cache_size(cache_dir: Path) -> str:
    # SQLite may leave -wal/-shm beside the database file.
    size = sum(p.stat().st_size for p in cache_dir.glob("cache.db*"))
    if size < _KB:
        return f"{size} B"
    if size < _KB * _KB:
        return f"{size / _KB:.1f} KB"
    return f"{size / (_KB * _KB):.1f} MB"


def _tilde(path: Path) -> str:
    home = Path.home()
    try:
        return f"~/{path.relative_to(home)}"
    except ValueError:
        return str(path)


def render_panel(trace: Trace, *, cache_dir: Path, elapsed_ms: float) -> str:
    """The `--debug` panel: one row per request, then scan, cache and output
    statistics. Takes plain values; never touches the client or the network."""
    lines = ["── debug ".ljust(56, "─"), f"total     {elapsed_ms:.0f}ms"]
    paths = [_request_path(e.url) for e in trace.events]
    width = max((len(p) for p in paths), default=0)
    for event, path in zip(trace.events, paths, strict=True):
        if event.source == "cache":
            cost = "cache"
        else:
            cost = f"{event.elapsed_ms:.0f}ms"
            if event.attempts > 1:
                cost += f"  \u00d7{event.attempts} attempts"  # multiplication sign
        lines.append(f"{event.method} {path:<{width}}   {event.status_code}   {cost}")
    for scan in trace.scans:
        capped = (
            f"capped at {scan.api_calls} API calls" if scan.capped else "not capped"
        )
        lines.append(
            f"scan      {scan.api_calls} API calls, {scan.rows} rows fetched, "
            f"{scan.kept} kept, {capped}"
        )
    lines.append(
        f"cache     {_tilde(cache_dir)}  {_cache_size(cache_dir)}  "
        f"ttl {CACHE_TTL / 3600:g}h"
    )
    if trace.stdout is not None:
        out = trace.stdout
        lines.append(
            f"output    {out.chars} chars, {out.lines} lines, ≈{out.chars // 4} tokens"
        )
    return "\n".join(lines)


def finish() -> None:
    """Print the footer (and the panel under --debug) to stderr and undo
    whatever `begin()` installed."""
    from agent_discogs.client import CACHE_DIR, has_token

    if _trace.stdout is not None:
        sys.stdout = _trace.stdout.target
    footer = render_footer(
        _trace.events, authenticated=has_token(), cache_misses=_trace.cache_misses
    )
    if footer:
        print(footer, file=sys.stderr)
    if _trace.debug:
        elapsed_ms = (time.perf_counter() - _trace.started) * 1000
        panel = render_panel(_trace, cache_dir=CACHE_DIR, elapsed_ms=elapsed_ms)
        print(panel, file=sys.stderr)
    if _trace.log_handler is not None:
        logger = logging.getLogger(_SDK_LOGGER)
        logger.removeHandler(_trace.log_handler)
        logger.setLevel(_trace.log_level)
        _trace.log_handler = None
