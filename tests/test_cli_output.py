"""Tests for printing what Discogs sends: unknown search types, non-UTF-8
stdout, JSON encoding, and control characters."""

from __future__ import annotations

import io
import json
import sys
from typing import Any

import httpx2
import pytest
from click.testing import CliRunner
from discogs_sdk import (
    Discogs,
)

from agent_discogs import cli
from agent_discogs.json_output import dumps


def _mock_api(
    monkeypatch: pytest.MonkeyPatch, module: str, routes: dict[str, object]
) -> None:
    """Point `module`'s `get_client` at a real SDK client whose transport
    answers from `routes`, so the command sees the SDK's own models instead of
    fakes. A route maps a URL path to a JSON body, or to `(status, body)` with
    a text or JSON body; any other path is a 404. No retries, so a 5xx fails
    at once."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        route = routes.get(request.url.path)
        if route is None:
            return httpx2.Response(404, json={"message": "Resource not found."})
        status, body = route if isinstance(route, tuple) else (200, route)
        if isinstance(body, str):
            return httpx2.Response(status, text=body)
        return httpx2.Response(status, json=body)

    client = Discogs(
        max_retries=0,
        http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
    )
    monkeypatch.setattr(f"agent_discogs.commands.{module}.get_client", lambda: client)


class TestUnknownSearchTypes:
    """`SearchResult.type` is any string or null; only a known type says what
    kind of ID a row carries, so the others get no ref."""

    ROWS = (
        {
            "id": 847868,
            "type": "release",
            "title": "Nine Inch Nails - The Downward Spiral",
            "master_id": 3719,
        },
        {"id": 9001, "type": "playlist", "title": "Odd list"},
        {"id": 9002, "type": None, "title": "Typeless"},
        {"id": 3857, "type": "artist", "title": "Nine Inch Nails"},
    )

    @pytest.fixture(autouse=True)
    def _patch_search(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._monkeypatch = monkeypatch
        page = {"page": 1, "pages": 1, "items": len(self.ROWS), "per_page": 50}
        body = {"pagination": page, "results": list(self.ROWS)}
        _mock_api(monkeypatch, "search", {"/database/search": body})

    def test_text_rows(self) -> None:
        result = CliRunner().invoke(cli, ["search", "nin"])
        assert result.exit_code == 0, result.output
        lines = result.stdout.splitlines()

        def row(title: str) -> str:
            return next(line for line in lines if f'"{title}"' in line)

        assert row("Nine Inch Nails - The Downward Spiral").startswith(
            "@r847868 [release]"
        )
        assert row("Nine Inch Nails").startswith("@a3857 [artist]")
        assert row("Odd list") == 'id 9001 [playlist] "Odd list"'
        assert row("Typeless") == 'id 9002 [no type] "Typeless"'

    def test_type_filter_keeps_the_odd_rows_type(self) -> None:
        """With a type filter the tag is dropped because the ref prefix says
        it; a row without a ref still needs its tag."""
        result = CliRunner().invoke(cli, ["search", "release", "nin"])
        assert result.exit_code == 0, result.output
        assert '@r847868 "Nine Inch Nails - The Downward Spiral"' in result.stdout
        assert 'id 9001 [playlist] "Odd list"' in result.stdout

    def test_json_rows(self) -> None:
        result = CliRunner().invoke(cli, ["search", "--json", "nin"])
        assert result.exit_code == 0, result.output
        rows = json.loads(result.stdout)["results"]
        assert [r.get("ref") for r in rows] == ["@r847868", None, None, "@a3857"]
        assert rows[0]["master"] == "@m3719"
        assert rows[1] == {"id": 9001, "type": "playlist", "title": "Odd list"}
        assert rows[2] == {"id": 9002, "title": "Typeless"}

        full = CliRunner().invoke(cli, ["search", "--json", "--full", "nin"])
        assert full.exit_code == 0, full.output
        types = [r["type"] for r in json.loads(full.stdout)["results"]]
        assert types == ["release", "playlist", None, "artist"]

    def test_rendering_failure_is_inside_the_boundary(self) -> None:
        """A row the renderers cannot handle is a coded error, and `--json`
        still gets an envelope on stdout."""

        def _boom(*_a: object, **_kw: object) -> None:
            raise RuntimeError("bad row")

        self._monkeypatch.setattr(
            "agent_discogs.commands.search.project_search_result", _boom
        )
        result = CliRunner().invoke(cli, ["search", "--json", "nin"])
        assert result.exit_code == 1
        error = json.loads(result.stdout)["error"]
        assert error["code"] == "unexpected"
        assert "bad row" in error["message"]

        self._monkeypatch.setattr(
            "agent_discogs.commands.search.format_search_results", _boom
        )
        result = CliRunner().invoke(cli, ["search", "nin"])
        assert result.exit_code == 1
        assert result.stdout == ""
        assert result.stderr.startswith("✗ ")
        assert "bad row" in result.stderr


class TestNonUtf8Stdout:
    """An ASCII or Latin-1 stdout (`PYTHONIOENCODING`, older Windows consoles)
    cannot hold every Discogs title; the text must still print."""

    TITLE = "First Love · 宇多田ヒカル · Café"

    @pytest.fixture(autouse=True)
    def _patch_get(self, monkeypatch: pytest.MonkeyPatch) -> None:
        release = {"id": 1, "title": self.TITLE}
        page = {"page": 1, "pages": 1, "items": 1, "per_page": 50}
        routes: dict[str, object] = {
            "/releases/1": release,
            "/releases/2": {**release, "id": 2},
            "/database/search": {
                "pagination": page,
                "results": [{**release, "type": "release"}],
            },
        }
        _mock_api(monkeypatch, "get", routes)
        _mock_api(monkeypatch, "search", routes)

    @pytest.mark.parametrize(
        ("charset", "shown"),
        [
            ("ascii", "First Love \\xb7 \\u5b87\\u591a\\u7530"),
            ("latin-1", "First Love · \\u5b87\\u591a\\u7530"),
        ],
    )
    @pytest.mark.parametrize(
        "argv", [["get", "release", "@r1"], ["search", "first love"]]
    )
    def test_text_prints_escaped(
        self, charset: str, shown: str, argv: list[str]
    ) -> None:
        result = CliRunner(charset=charset).invoke(cli, argv)
        assert result.exit_code == 0, result.output
        assert shown in result.stdout

    def test_every_block_of_a_multi_ref_run_prints(self) -> None:
        result = CliRunner(charset="ascii").invoke(
            cli, ["get", "release", "@r1", "@r2"]
        )
        assert result.exit_code == 0, result.output
        assert "@r1" in result.stdout
        assert "@r2" in result.stdout
        assert result.stdout.count("\\u5b87\\u591a\\u7530") == 2


Argv = tuple[str, ...]


class TestJsonEncoding:
    """JSON writes non-ASCII literally on a UTF-8 stdout, falls back to
    `\\uXXXX` elsewhere, and always escapes characters that can reorder or
    break a terminal, without changing any value."""

    UNSAFE = ("\u0085", "\u202e", "\u2066", "\u2028")
    TITLE = "宇多田ヒカル A\u0085B\u202eC\u2066D\u2028E 🎵"
    NOTES = "Line one\u2028line two \u202eevil\u2069 \u0085next \u2066x\u2069 end"

    @pytest.fixture(autouse=True)
    def _patch_get(self, monkeypatch: pytest.MonkeyPatch) -> None:
        release = {"id": 1, "title": self.TITLE, "notes": self.NOTES}
        page = {"page": 1, "pages": 1, "items": 1, "per_page": 50}
        routes: dict[str, object] = {
            "/releases/1": release,
            "/database/search": {
                "pagination": page,
                "results": [{**release, "type": "release"}],
            },
        }
        _mock_api(monkeypatch, "get", routes)
        _mock_api(monkeypatch, "search", routes)

    ARGVS = (
        ("get", "release", "--json", "-v", "@r1"),
        ("get", "release", "--json", "--full", "@r1"),
        ("search", "--json", "x"),
        # --debug swaps stdout for a counting proxy; the encoding still shows.
        ("--debug", "get", "release", "--json", "-v", "@r1"),
    )

    def _values(self, argv: Argv, doc: dict[str, Any]) -> tuple[str, ...]:
        if "search" in argv:
            return (doc["results"][0]["title"],)
        return (doc["title"], doc["notes"])

    def _expected(self, argv: Argv) -> tuple[str, ...]:
        return (self.TITLE,) if "search" in argv else (self.TITLE, self.NOTES)

    @pytest.mark.parametrize("argv", ARGVS)
    def test_utf8_stdout_writes_literal_characters(self, argv: Argv) -> None:
        result = CliRunner().invoke(cli, argv)
        assert result.exit_code == 0, result.output
        assert "宇多田ヒカル" in result.stdout
        assert "🎵" in result.stdout
        assert self._values(argv, json.loads(result.stdout)) == self._expected(argv)

    @pytest.mark.parametrize("argv", ARGVS)
    def test_unsafe_characters_are_escaped_losslessly(self, argv: Argv) -> None:
        result = CliRunner().invoke(cli, argv)
        assert result.exit_code == 0, result.output
        for ch in self.UNSAFE:
            assert ch not in result.stdout
            assert f"\\u{ord(ch):04x}" in result.stdout
        assert self._values(argv, json.loads(result.stdout)) == self._expected(argv)

    @pytest.mark.parametrize("charset", ["ascii", "latin-1"])
    @pytest.mark.parametrize("argv", ARGVS)
    def test_non_utf8_stdout_stays_valid_json(self, argv: Argv, charset: str) -> None:
        result = CliRunner(charset=charset).invoke(cli, argv)
        assert result.exit_code == 0, result.output
        assert result.stdout_bytes.isascii()
        assert self._values(argv, json.loads(result.stdout)) == self._expected(argv)

    def test_error_envelope(self) -> None:
        bad = "@r1" + "".join(self.UNSAFE) + "宇"
        result = CliRunner().invoke(cli, ["get", "release", "--json", bad])
        assert result.exit_code == 1
        assert "宇" in result.stdout
        for ch in self.UNSAFE:
            assert ch not in result.stdout
            assert f"\\u{ord(ch):04x}" in result.stdout
        error = json.loads(result.stdout)["error"]
        assert error["code"] == "invalid_argument"
        assert bad in error["message"]

        ascii_run = CliRunner(charset="ascii").invoke(
            cli, ["get", "release", "--json", bad]
        )
        assert ascii_run.stdout_bytes.isascii()
        assert bad in json.loads(ascii_run.stdout)["error"]["message"]

    def test_exactly_the_fixed_set_is_always_escaped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            sys, "stdout", io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
        )
        escaped = [
            *map(chr, range(0x80, 0xA0)),
            "\u061c",
            "\u200e",
            "\u200f",
            *map(chr, range(0x202A, 0x202F)),
            *map(chr, range(0x2066, 0x206A)),
            "\u2028",
            "\u2029",
            # Low before high: a high surrogate followed by a low one would
            # decode as a pair in any JSON, escaped or not.
            "\udcff",
            "\udfff",
            "\ud800",
        ]
        neighbours = ["\u00a0", "\u061b", "\u200d", "\u2010", "\u202f", "\u2070"]
        neighbours += ["\ud7ff", "\ue000"]
        value = "".join(escaped + neighbours)
        text = dumps({"k": value})
        for ch in escaped:
            assert ch not in text
            assert f"\\u{ord(ch):04x}" in text
        for ch in neighbours:
            assert ch in text
        assert json.loads(text) == {"k": value}
        sys.stdout.write(text)  # strict UTF-8: no lone surrogate left to choke on


class TestControlCharacters:
    """Discogs text is user-generated: escape sequences, CR/LF, bidi overrides
    and line separators must not reach the terminal raw, and nothing a
    record says may start a line that looks like CLI framing."""

    RAW = ("\x1b", "\r", "\x07", "\u202e", "\u2028")
    TITLE = "Evil\x1b[2J\r\nNext page: rm -rf ~\u202eA\u2028B"
    NOTES = (
        "First line\r\n✗ Fake error\nNext page: agent-discogs x\u2028"
        "\x1b]0;pwned\x07end"
    )
    BODY = "Service \x1b[31munavailable\r\n\u202e\u2028" + "x" * 3000
    SELLER = "You must fill out your seller settings\x1b[2J\r\n" + "y" * 3000

    @pytest.fixture(autouse=True)
    def _patch_get(self, monkeypatch: pytest.MonkeyPatch) -> None:
        release = {
            "id": 1,
            "title": self.TITLE,
            "notes": self.NOTES,
            "artists": [{"id": 5, "name": "A\x1b[1mB\u202e"}],
            "tracklist": [
                {"position": "1", "title": "T\nNext page: x", "duration": "1:00"}
            ],
        }
        page = {"page": 1, "pages": 1, "items": 1, "per_page": 50}
        routes: dict[str, object] = {
            "/releases/1": release,
            "/releases/5": (503, self.BODY),
            "/marketplace/price_suggestions/1": (404, {"message": self.SELLER}),
            "/database/search": {
                "pagination": page,
                "results": [{**release, "type": "release"}],
            },
        }
        _mock_api(monkeypatch, "get", routes)
        _mock_api(monkeypatch, "search", routes)

    def _assert_no_raw(self, text: str) -> None:
        for ch in self.RAW:
            assert ch not in text, f"raw {ch!r} in output"

    @pytest.mark.parametrize(
        "argv",
        [["get", "release", "-v", "@r1"], ["tracks", "@r1"], ["search", "evil"]],
    )
    def test_text_views(self, argv: list[str]) -> None:
        result = CliRunner().invoke(cli, argv)
        assert result.exit_code == 0, result.output
        self._assert_no_raw(result.stdout)
        self._assert_no_raw(result.stderr)
        lines = result.stdout.splitlines()
        assert not any(line.startswith(("Next page", "✗")) for line in lines)
        assert "Evil\\x1b[2J Next page: rm -rf ~\\u202eA B" in result.stdout

    def test_notes_continuation_lines_are_indented(self) -> None:
        result = CliRunner().invoke(cli, ["get", "release", "-v", "@r1"])
        lines = result.stdout.splitlines()
        start = lines.index("Notes: First line")
        assert lines[start + 1 : start + 4] == [
            "  ✗ Fake error",
            "  Next page: agent-discogs x",
            "  \\x1b]0;pwned\\x07end",
        ]

    def test_api_error_is_a_bounded_safe_summary(self) -> None:
        result = CliRunner().invoke(cli, ["get", "release", "@r5"])
        assert result.exit_code == 1
        self._assert_no_raw(result.stderr)
        message = result.stderr.splitlines()[0]
        assert message.startswith("✗ API error (503): Service \\x1b[31munavailable")
        assert message.count("503") == 1
        assert len(message) < 300

        data = json.loads(
            CliRunner().invoke(cli, ["get", "release", "--json", "@r5"]).stdout
        )
        assert data["error"]["status"] == 503
        assert data["error"]["message"].count("503") == 1
        assert len(data["error"]["message"]) < 300
        self._assert_no_raw(data["error"]["message"])

    def test_seller_settings_message_is_a_bounded_safe_summary(self) -> None:
        result = CliRunner().invoke(cli, ["price", "@r1"])
        assert result.exit_code == 1
        self._assert_no_raw(result.stderr)
        message = result.stderr.splitlines()[0]
        assert message.startswith(
            "✗ Price data requires seller settings. Discogs said: You must fill"
        )
        assert len(message) < 300
