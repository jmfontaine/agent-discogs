"""Tests for the budget footer and the --debug panel."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Literal

import pytest
from discogs_sdk import RateLimit, RequestEvent

from agent_discogs.trace import (
    LOW_BUDGET,
    ScanNote,
    Trace,
    _CountingWriter,
    render_footer,
    render_panel,
)


def _event(
    *,
    source: Literal["network", "cache"] = "network",
    ratelimit: RateLimit | None = None,
    url: str = "https://api.discogs.com/releases/847868",
    status_code: int = 200,
    elapsed_ms: float = 12.0,
    attempts: int = 1,
) -> RequestEvent:
    return RequestEvent(
        method="GET",
        url=url,
        status_code=status_code,
        source=source,
        elapsed_ms=elapsed_ms,
        attempts=attempts,
        stored=False,
        ratelimit=ratelimit,
    )


class TestRenderFooter:
    def test_no_events_is_silent(self) -> None:
        assert render_footer([], authenticated=True) is None

    def test_only_cache_hits(self) -> None:
        events = [_event(source="cache"), _event(source="cache")]
        assert render_footer(events, authenticated=True) == "api: 0 requests · cached"

    def test_cache_miss_without_events_reports_zero_spend(self) -> None:
        # A `--cached` miss on the first lookup raises before any event exists.
        assert (
            render_footer([], authenticated=True, cache_misses=1)
            == "api: 0 requests · cached"
        )

    def test_singular_and_plural(self) -> None:
        rl = RateLimit(60, 1, 59)
        one = [_event(ratelimit=rl)]
        assert (
            render_footer(one, authenticated=True)
            == "api: 1 request · 59/60 left this minute"
        )
        three = [_event(ratelimit=RateLimit(60, 17, 43)) for _ in range(3)]
        assert (
            render_footer(three, authenticated=True)
            == "api: 3 requests · 43/60 left this minute"
        )

    def test_budget_comes_from_last_network_event_with_headers(self) -> None:
        events = [
            _event(ratelimit=RateLimit(60, 10, 50)),
            _event(ratelimit=RateLimit(60, 11, 49)),
            _event(ratelimit=None),  # documented "some exceptions" response
            _event(source="cache"),
        ]
        assert (
            render_footer(events, authenticated=True)
            == "api: 3 requests · 49/60 left this minute"
        )

    def test_no_headers_at_all_is_a_bare_count(self) -> None:
        events = [_event(), _event()]
        assert render_footer(events, authenticated=True) == "api: 2 requests"

    def test_warns_at_threshold_only(self) -> None:
        at = [_event(ratelimit=RateLimit(60, 50, LOW_BUDGET))]
        above = [_event(ratelimit=RateLimit(60, 49, LOW_BUDGET + 1))]
        assert render_footer(at, authenticated=True) == (
            f"api: 1 request · {LOW_BUDGET}/60 left this minute"
            " ⚠ pause ~60s before uncached calls"
        )
        assert render_footer(above, authenticated=True) == (
            f"api: 1 request · {LOW_BUDGET + 1}/60 left this minute"
        )

    def test_unauthenticated_warning_suggests_a_token(self) -> None:
        events = [_event(ratelimit=RateLimit(25, 22, 3))]
        assert render_footer(events, authenticated=False) == (
            "api: 1 request · 3/25 left this minute"
            " ⚠ set DISCOGS_TOKEN for 60/min, or pause ~60s"
        )


class TestCountingWriter:
    def test_counts_across_writes_and_forwards(self) -> None:
        target = io.StringIO()
        writer = _CountingWriter(target)
        writer.write("hello\n")
        writer.write("wor")
        writer.write("ld\n\n")
        writer.flush()
        assert (writer.chars, writer.lines) == (13, 3)
        assert target.getvalue() == "hello\nworld\n\n"
        assert writer.encoding is None  # attribute lookup falls through


class TestRenderPanel:
    def test_rows(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        cache_dir = tmp_path / ".cache" / "agent-discogs"
        cache_dir.mkdir(parents=True)
        (cache_dir / "cache.db").write_bytes(b"x" * 2048)
        (cache_dir / "cache.db-wal").write_bytes(b"x" * 1024)

        stdout = _CountingWriter(io.StringIO())
        stdout.write("a" * 842 + "\n" * 12)
        trace = Trace(
            events=[
                _event(
                    url="https://api.discogs.com/database/search?q=nin&type=artist",
                    elapsed_ms=190.4,
                ),
                _event(url="https://api.discogs.com/masters/3719", source="cache"),
                _event(
                    url="https://api.discogs.com/masters/3719/versions",
                    status_code=503,
                    elapsed_ms=8.0,
                    attempts=2,
                ),
            ],
            scans=[ScanNote(2, 30, 5, capped=False), ScanNote(5, 75, 1, capped=True)],
            stdout=stdout,
        )
        panel = render_panel(trace, cache_dir=cache_dir, elapsed_ms=412.6)
        assert panel.splitlines() == [
            "── debug ───────────────────────────────────────────────",
            "total     413ms",
            "GET /database/search?q=nin&type=artist   200   190ms",
            "GET /masters/3719                        200   cache",
            "GET /masters/3719/versions               503   8ms  ×2 attempts",  # noqa: RUF001
            "scan      2 API calls, 30 rows fetched, 5 kept, not capped",
            "scan      5 API calls, 75 rows fetched, 1 kept, capped at 5 API calls",
            "cache     ~/.cache/agent-discogs  3.0 KB  ttl 1h",
            "output    854 chars, 12 lines, ≈213 tokens",
        ]

    def test_missing_cache_dir_and_no_output_counter(self, tmp_path: Path) -> None:
        panel = render_panel(
            Trace(), cache_dir=tmp_path / "missing" / "cache", elapsed_ms=0
        )
        lines = panel.splitlines()
        assert lines[1] == "total     0ms"
        assert lines[-1].startswith("cache     ")
        assert lines[-1].endswith("  0.0 KB  ttl 1h")
        assert not any(line.startswith("output") for line in lines)

    def test_megabytes(self, tmp_path: Path) -> None:
        (tmp_path / "cache.db").write_bytes(b"\0" * (3 * 1024 * 1024 + 100_000))
        panel = render_panel(Trace(), cache_dir=tmp_path, elapsed_ms=0)
        assert "  3.1 MB  " in panel
