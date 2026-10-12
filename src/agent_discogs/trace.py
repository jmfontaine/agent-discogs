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

from agent_discogs.config import CACHE_TTL_LABEL, MAX_API_CALLS, cache_dir

if TYPE_CHECKING:
    import click
    from discogs_sdk import RequestEvent

# Remaining requests at which the footer warns. One client-side scan costs up
# to MAX_API_CALLS requests; two back-to-back scans can spend twice that.
LOW_BUDGET = 2 * MAX_API_CALLS
# Discogs' per-minute limit for requests carrying any credentials (25 without).
# An observed limit below it means Discogs counted the request as anonymous.
_AUTHENTICATED_LIMIT = 60

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
    """SDK `on_request` hook: one event per logical request, whether it was
    served from the cache, answered by the network, failed in transport
    (`status_code` None, `source == "network"`), or refused by `cache_only()`
    (`source == "miss"`)."""
    _trace.events.append(event)


def note_scan(note: ScanNote) -> None:
    """Record the cost of one client-side filtered scan."""
    _trace.scans.append(note)


def render_footer(events: Sequence[RequestEvent]) -> str | None:
    """The budget line, or None when nothing was requested.

    Counts HTTP sends, failed transport sends included: each network event
    carries its `attempts`. Cache hits and `--cached` misses send nothing, so
    a run of only those reports zero. The budget comes from the last network
    response that reported rate-limit headers, so cache hits never move it,
    and so does the warning: a token is suggested only when the limit Discogs
    reported is below the authenticated one, whatever credentials are set.
    """
    if not events:
        return None
    network = [e for e in events if e.source == "network"]
    n = sum(e.attempts for e in network)
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
            if rl.limit >= _AUTHENTICATED_LIMIT
            else f" ⚠ set DISCOGS_TOKEN for {_AUTHENTICATED_LIMIT}/min, or pause ~60s"
        )
    return line


def _request_path(url: str) -> str:
    split = urlsplit(url)
    return split.path + (f"?{split.query}" if split.query else "")


_KB = 1024


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except FileNotFoundError:  # gone since the glob, e.g. a WAL checkpointed away
        return 0


def _cache_size(cache_dir: Path) -> str:
    # SQLite may leave -wal/-shm beside the database file.
    size = sum(_file_size(p) for p in cache_dir.glob("cache.db*"))
    if size < _KB:
        return f"{size} B"
    if size < _KB * _KB:
        return f"{size / _KB:.1f} KB"
    return f"{size / (_KB * _KB):.1f} MB"


def _tilde(path: Path) -> str:
    try:
        return f"~/{path.relative_to(Path.home())}"
    except (RuntimeError, ValueError):  # no home directory, or not under it
        return str(path)


def _cache_line(cache_dir: Path | None) -> str:
    where = (
        f"{_tilde(cache_dir)}  {_cache_size(cache_dir)}"
        if cache_dir is not None
        else "unknown (no home directory)"
    )
    return f"cache     {where}  ttl {CACHE_TTL_LABEL}"


def render_panel(trace: Trace, *, cache_dir: Path | None, elapsed_ms: float) -> str:
    """The `--debug` panel: one row per response, from the cache or the
    network, then scan, cache and output statistics. A `--cached` miss and a
    transport failure have no response and get no row. Takes plain values;
    never touches the client or the network. `cache_dir` is None when it
    could not be resolved."""
    lines = ["── debug ".ljust(56, "─"), f"total     {elapsed_ms:.0f}ms"]
    responses = [e for e in trace.events if e.status_code is not None]
    paths = [_request_path(e.url) for e in responses]
    width = max((len(p) for p in paths), default=0)
    for event, path in zip(responses, paths, strict=True):
        cost = "cache" if event.source == "cache" else f"{event.elapsed_ms:.0f}ms"
        lines.append(f"{event.method} {path:<{width}}   {event.status_code}   {cost}")
    for scan in trace.scans:
        capped = (
            f"capped at {scan.api_calls} API calls" if scan.capped else "not capped"
        )
        lines.append(
            f"scan      {scan.api_calls} API calls, {scan.rows} rows fetched, "
            f"{scan.kept} kept, {capped}"
        )
    lines.append(_cache_line(cache_dir))
    if trace.stdout is not None:
        out = trace.stdout
        lines.append(
            f"output    {out.chars} chars, {out.lines} lines, ≈{out.chars // 4} tokens"
        )
    return "\n".join(lines)


def _resolve_cache_dir() -> Path | None:
    try:
        return cache_dir()
    except RuntimeError:  # Path.home() found no home directory
        return None


def finish() -> None:
    """Print the footer (and the panel under --debug) to stderr and undo
    whatever `begin()` installed. The SDK logger is restored even if printing
    fails."""
    try:
        if _trace.stdout is not None:
            sys.stdout = _trace.stdout.target
        footer = render_footer(_trace.events)
        if footer:
            print(footer, file=sys.stderr)
        if _trace.debug:
            elapsed_ms = (time.perf_counter() - _trace.started) * 1000
            panel = render_panel(
                _trace, cache_dir=_resolve_cache_dir(), elapsed_ms=elapsed_ms
            )
            print(panel, file=sys.stderr)
    finally:
        if _trace.log_handler is not None:
            logger = logging.getLogger(_SDK_LOGGER)
            logger.removeHandler(_trace.log_handler)
            logger.setLevel(_trace.log_level)
            _trace.log_handler = None
