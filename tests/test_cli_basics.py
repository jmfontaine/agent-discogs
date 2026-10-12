"""Tests for the CLI group: version and help, `status`, command aliases,
the `main()` entry point, and exception handling."""

from __future__ import annotations

import errno
import importlib
import importlib.metadata
import io
import runpy
import sys
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from agent_discogs import cli, main
from tests.conftest import (
    UseClient,
    fake_client,
    json_error,
    text_error,
)


class TestCLIBasics:
    def test_version(self) -> None:
        result = CliRunner().invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert "agent-discogs" in result.stdout
        expected = importlib.metadata.version("agent-discogs")
        assert expected in result.stdout

    def test_help(self) -> None:
        result = CliRunner().invoke(cli, ["--help"])
        assert result.exit_code == 0
        # Commands section
        assert "search [type] <query>" in result.stdout
        assert "get <noun> <ref>" in result.stdout
        assert "tracks <ref>" in result.stdout
        assert "price <ref>" in result.stdout
        assert "cache clear" in result.stdout
        assert "status" in result.stdout
        assert "completion bash|zsh|fish" in result.stdout
        # Aliases
        assert "aliases: find, query" in result.stdout
        assert "aliases: fetch, show" in result.stdout
        # Search types and get nouns
        assert "Search Types:" in result.stdout
        assert "Get Nouns:" in result.stdout
        # Ref system explanation
        assert "@a3857 (artist)" in result.stdout
        assert "@r847868 (release)" in result.stdout
        # Environment
        assert "DISCOGS_TOKEN" in result.stdout
        # Examples
        assert "Examples:" in result.stdout
        assert 'agent-discogs search "The Downward Spiral"' in result.stdout

    def test_no_args_shows_help(self) -> None:
        result = CliRunner().invoke(cli, [])
        assert result.exit_code == 0
        assert "agent-discogs - token-efficient Discogs CLI" in result.stdout
        assert "Commands:" in result.stdout
        assert "Refs:" in result.stdout
        assert "Examples:" in result.stdout

    def test_search_help(self) -> None:
        result = CliRunner().invoke(cli, ["search", "--help"])
        assert result.exit_code == 0
        assert "--year" in result.stdout
        assert "--genre" in result.stdout
        assert "--limit" in result.stdout

    def test_get_help(self) -> None:
        result = CliRunner().invoke(cli, ["get", "--help"])
        assert result.exit_code == 0
        assert "release" in result.stdout
        assert "artist" in result.stdout
        assert "tracklist" in result.stdout
        assert "price" in result.stdout

    def test_unknown_command(self) -> None:
        result = CliRunner().invoke(cli, ["nonexistent"])
        assert result.exit_code == 2
        assert result.stdout == ""
        assert result.stderr.endswith("\n\nError: No such command 'nonexistent'.\n")


class TestStatusCommand:
    def test_status(self) -> None:
        result = CliRunner().invoke(cli, ["status"])
        assert result.exit_code == 0
        version = importlib.metadata.version("agent-discogs")
        assert f"agent-discogs v{version}" in result.stdout
        assert "Auth:" in result.stdout
        assert "Cache:" in result.stdout

    @pytest.mark.parametrize(
        ("env", "auth"),
        [
            ({"DISCOGS_TOKEN": "t"}, "token (authenticated)"),
            (
                {"DISCOGS_CONSUMER_KEY": "k", "DISCOGS_CONSUMER_SECRET": "s"},
                "consumer (authenticated app, not a user: no price suggestions)",
            ),
            (
                {
                    "DISCOGS_CONSUMER_KEY": "k",
                    "DISCOGS_CONSUMER_SECRET": "s",
                    "DISCOGS_ACCESS_TOKEN": "a",
                    "DISCOGS_ACCESS_TOKEN_SECRET": "b",
                },
                "oauth (authenticated)",
            ),
            ({}, "none (unauthenticated, 25 req/min)"),
        ],
        ids=["token", "consumer", "oauth", "none"],
    )
    def test_reports_the_auth_mode_in_use(
        self, env: dict[str, str], auth: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        result = CliRunner().invoke(cli, ["status"])
        assert result.exit_code == 0
        assert f"Auth: {auth}" in result.stdout.splitlines()

    def test_never_touches_the_cache(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("DISCOGS_TOKEN", "t")
        monkeypatch.setattr("agent_discogs.client._client", None)
        not_a_dir = tmp_path / "file"
        not_a_dir.write_text("")
        monkeypatch.setenv("XDG_CACHE_HOME", str(not_a_dir))
        result = CliRunner().invoke(cli, ["status"])
        assert result.exit_code == 0, result.output
        assert "Auth: token (authenticated)" in result.stdout.splitlines()

        absent = tmp_path / "absent"
        monkeypatch.setenv("XDG_CACHE_HOME", str(absent))
        result = CliRunner().invoke(cli, ["status"])
        assert result.exit_code == 0, result.output
        assert not absent.exists()


class TestAliases:
    def test_find_alias(self) -> None:
        result = CliRunner().invoke(cli, ["find", "--help"])
        assert result.exit_code == 0
        assert "--year" in result.stdout
        assert result.stdout.startswith("Usage: cli search ")  # canonical name

    def test_fetch_alias(self) -> None:
        result = CliRunner().invoke(cli, ["fetch", "--help"])
        assert result.exit_code == 0
        assert "release" in result.stdout
        assert result.stdout.startswith("Usage: cli get ")

    def test_query_alias(self) -> None:
        result = CliRunner().invoke(cli, ["query", "--help"])
        assert result.exit_code == 0
        assert "--year" in result.stdout
        assert result.stdout.startswith("Usage: cli search ")

    def test_show_alias(self) -> None:
        result = CliRunner().invoke(cli, ["show", "--help"])
        assert result.exit_code == 0
        assert "release" in result.stdout
        assert result.stdout.startswith("Usage: cli get ")


class TestMainEntry:
    def test_main_invokes_cli(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """main() delegates to cli()."""
        calls: list[object] = []
        monkeypatch.setattr("agent_discogs.cli", lambda *a, **kw: calls.append(1))
        main()
        assert len(calls) == 1

    def test_dunder_main(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`python -m agent_discogs` runs main()."""
        calls: list[object] = []
        monkeypatch.setattr("agent_discogs.main", lambda: calls.append(1))
        runpy.run_module("agent_discogs", run_name="__main__")
        assert calls == [1]


class TestExceptionHandling:
    def test_abort_propagates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _raise_abort(
            *_a: object,
            **_kw: object,
        ) -> None:
            raise click.exceptions.Abort

        monkeypatch.setattr("agent_discogs.commands.get._dispatch", _raise_abort)
        result = CliRunner().invoke(cli, ["get", "artist", "@a1"])
        # click's own abort, not reported as an unexpected error
        assert text_error(result) == "Aborted!\n"

    def test_keyboard_interrupt(self, use_client: UseClient) -> None:
        def _raise(_id: int) -> None:
            raise KeyboardInterrupt

        use_client(fake_client(artists_get=_raise))
        result = CliRunner().invoke(cli, ["get", "artist", "@a1"])
        assert result.exit_code == 130
        assert (result.stdout, result.stderr) == ("", "")  # no traceback

    def test_generic_exception_in_invoke(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Exception escaping a command with no boundary of its own -> text error.

        `get`/`search` catch everything (incl. client creation) so `--json`
        callers get an envelope; this last resort only sees the rest.
        """

        def _raise() -> str:
            raise RuntimeError("kaboom")

        monkeypatch.setattr("agent_discogs.commands.status.auth_mode", _raise)
        result = CliRunner().invoke(cli, ["status"])
        assert result.exit_code == 1
        first = result.stderr.splitlines()[0]
        assert first.startswith("✗ ")
        assert "RuntimeError" in first
        assert "kaboom" in first

    def test_broken_stdout_pipe_exits_quietly(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`agent-discogs ... | head -1`: the reader closed the pipe, which is
        not an error to report. click's EPIPE handling exits 1 silently."""

        class _ClosedPipe(io.StringIO):
            def write(self, _s: str) -> int:
                raise BrokenPipeError(errno.EPIPE, "Broken pipe")

        stderr = io.StringIO()
        monkeypatch.setattr(sys, "stdout", _ClosedPipe())
        monkeypatch.setattr(sys, "stderr", stderr)
        with pytest.raises(SystemExit) as exc:
            cli.main(["status"], prog_name="agent-discogs")
        assert exc.value.code == 1
        assert stderr.getvalue() == ""

    def test_client_failure_is_inside_the_json_boundary(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _raise() -> None:
            raise RuntimeError("client setup failed")

        monkeypatch.setattr("agent_discogs.commands.get.get_client", _raise)
        result = CliRunner().invoke(cli, ["get", "--json", "artist", "@a1"])
        error = json_error(result)
        assert error == {
            "code": "unexpected",
            "message": "Unexpected error: RuntimeError: client setup failed",
        }

        monkeypatch.setattr("agent_discogs.commands.search.get_client", _raise)
        result = CliRunner().invoke(cli, ["search", "--json", "x"])
        assert json_error(result) == error
