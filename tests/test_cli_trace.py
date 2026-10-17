"""Tests for the request trace: the budget footer, the `--debug` panel,
and the network sends each command costs."""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Iterator
from pathlib import Path

import httpx2
import pytest
from click.testing import CliRunner
from discogs_sdk import (
    Discogs,
    NotFoundError,
    RateLimit,
    RequestEvent,
)

from agent_discogs import cli, trace
from tests.conftest import (
    UseClient,
    _tracks_fake,
    fake_client,
)


def _network_event(remaining: int = 59, limit: int = 60) -> RequestEvent:
    return RequestEvent(
        method="GET",
        url="https://api.discogs.com/releases/123",
        status_code=200,
        source="network",
        elapsed_ms=10.0,
        attempts=1,
        stored=True,
        ratelimit=RateLimit(limit, limit - remaining, remaining),
    )


def _no_home() -> Path:
    msg = "Could not determine home directory."
    raise RuntimeError(msg)


class TestBudgetFooter:
    """The footer is stderr-only, printed after the command, in every mode."""

    def test_footer_on_stderr_not_stdout(
        self, monkeypatch: pytest.MonkeyPatch, use_client: UseClient
    ) -> None:
        monkeypatch.setenv("DISCOGS_TOKEN", "t")
        use_client(_tracks_fake(lambda: trace.record(_network_event(43))))
        footer = "api: 1 request · 43/60 left this minute\n"

        result = CliRunner().invoke(cli, ["tracks", "@r123"])
        assert result.exit_code == 0
        assert "Song" in result.stdout
        assert result.stderr == footer
        assert "api:" not in result.stdout

        result = CliRunner().invoke(cli, ["tracks", "--json", "@r123"])
        assert result.exit_code == 0
        assert json.loads(result.stdout)["tracklist"][0]["title"] == "Song"
        assert result.stderr == footer

    def test_footer_survives_a_failing_ref(
        self, monkeypatch: pytest.MonkeyPatch, use_client: UseClient
    ) -> None:
        def _boom() -> None:
            trace.record(_network_event(5))
            raise RuntimeError("after the request")

        monkeypatch.setenv("DISCOGS_TOKEN", "t")
        use_client(_tracks_fake(_boom))
        result = CliRunner().invoke(cli, ["tracks", "--json", "@r123"])
        assert result.exit_code == 1
        assert json.loads(result.stdout)["error"]["code"] == "unexpected"
        assert result.stderr == (
            "api: 1 request · 5/60 left this minute"
            " ⚠ pause ~60s before uncached calls\n"
        )

    def test_unauthenticated_wording(self, use_client: UseClient) -> None:
        use_client(_tracks_fake(lambda: trace.record(_network_event(3, 25))))
        result = CliRunner().invoke(cli, ["tracks", "@r123"])
        assert result.stderr.endswith(
            "3/25 left this minute ⚠ set DISCOGS_TOKEN for 60/min, or pause ~60s\n"
        )

    def test_consumer_credentials_are_not_told_to_set_a_token(
        self, monkeypatch: pytest.MonkeyPatch, use_client: UseClient
    ) -> None:
        monkeypatch.setenv("DISCOGS_CONSUMER_KEY", "k")
        monkeypatch.setenv("DISCOGS_CONSUMER_SECRET", "s")
        use_client(_tracks_fake(lambda: trace.record(_network_event(3, 60))))
        result = CliRunner().invoke(cli, ["tracks", "@r123"])
        assert result.stderr == (
            "api: 1 request · 3/60 left this minute"
            " ⚠ pause ~60s before uncached calls\n"
        )

    def test_no_requests_no_footer(self) -> None:
        for args in (["status"], ["skills"], ["--help"], ["get", "--help"]):
            result = CliRunner().invoke(cli, args)
            assert result.exit_code == 0, args
            assert result.stderr == "", args


class TestDebug:
    def test_panel_on_stderr_stdout_identical(self, use_client: UseClient) -> None:
        use_client(_tracks_fake(lambda: trace.record(_network_event())))
        plain = CliRunner().invoke(cli, ["tracks", "@r123"])
        debug = CliRunner().invoke(cli, ["--debug", "tracks", "@r123"])
        assert debug.exit_code == 0
        assert debug.stdout == plain.stdout
        assert "── debug ──" not in debug.stdout
        lines = debug.stderr.splitlines()
        assert lines[0] == "api: 1 request · 59/60 left this minute"
        assert lines[1].startswith("── debug ──")
        assert lines[2].startswith("total     ")
        assert lines[3].startswith("GET /releases/123   200   10ms")
        chars = len(plain.stdout)
        assert lines[-1] == (
            f"output    {chars} chars, {plain.stdout.count(chr(10))} lines, "
            f"≈{chars // 4} tokens"
        )
        assert not isinstance(sys.stdout, trace._CountingWriter)  # proxy removed

    def test_env_var_enables_debug(self, use_client: UseClient) -> None:
        use_client(_tracks_fake(lambda: None))
        result = CliRunner(env={"AGENT_DISCOGS_DEBUG": "1"}).invoke(
            cli, ["tracks", "@r123"]
        )
        assert result.exit_code == 0
        assert "── debug ──" in result.stderr
        assert "── debug ──" not in result.stdout

    def test_sdk_log_passthrough_only_with_debug(self, use_client: UseClient) -> None:
        sdk_logger = logging.getLogger("discogs_sdk")
        use_client(_tracks_fake(lambda: sdk_logger.debug("Cache hit: GET /x")))

        result = CliRunner().invoke(cli, ["--debug", "tracks", "@r123"])
        assert "[sdk] Cache hit: GET /x\n" in result.stderr
        assert sdk_logger.handlers == []

        result = CliRunner().invoke(cli, ["tracks", "@r123"])
        assert "[sdk]" not in result.stderr

    @pytest.fixture
    def cache_home(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        """An XDG cache holding a 2 KB database and a 1 KB WAL sidecar."""
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        directory = tmp_path / "agent-discogs"
        directory.mkdir()
        (directory / "cache.db").write_bytes(b"x" * 2048)
        (directory / "cache.db-wal").write_bytes(b"x" * 1024)
        return directory

    def test_sidecar_vanishing_between_glob_and_stat(
        self, cache_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        real_glob = Path.glob

        def glob_then_checkpoint(self: Path, pattern: str) -> Iterator[Path]:
            found = list(real_glob(self, pattern))
            (cache_home / "cache.db-wal").unlink()  # a sibling's last close
            return iter(found)

        monkeypatch.setattr(Path, "glob", glob_then_checkpoint)
        result = CliRunner().invoke(cli, ["--debug", "skills"])
        assert result.exit_code == 0, result.exception
        assert f"cache     {trace._tilde(cache_home)}  2.0 KB  ttl 1h" in result.stderr

    def test_home_lookup_failure_with_xdg_cache(
        self, cache_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(Path, "home", _no_home)
        result = CliRunner().invoke(cli, ["--debug", "skills"])
        assert result.exit_code == 0, result.exception
        assert f"cache     {cache_home}  3.0 KB  ttl 1h" in result.stderr

    def test_home_lookup_failure_without_xdg_cache(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
        monkeypatch.setattr(Path, "home", _no_home)
        result = CliRunner().invoke(cli, ["--debug", "skills"])
        assert result.exit_code == 0, result.exception
        assert "── debug ──" in result.stderr
        assert "cache     unknown (no home directory)  ttl 1h" in result.stderr

    def test_an_unreadable_cache_size_keeps_the_command(
        self, cache_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        plain = CliRunner().invoke(cli, ["skills"])
        real_stat = Path.stat

        def stat(self: Path, **kwargs: bool) -> object:
            if self.name == "cache.db":
                raise PermissionError(13, "Permission denied", str(self))
            return real_stat(self, **kwargs)

        monkeypatch.setattr(Path, "stat", stat)
        result = CliRunner().invoke(cli, ["--debug", "skills"])
        assert result.exception is None
        assert result.exit_code == 0
        assert result.stdout == plain.stdout
        where = trace._tilde(cache_home)
        assert f"cache     {where}  unknown  ttl 1h" in result.stderr

    def test_failed_command_keeps_its_exit_code(
        self, monkeypatch: pytest.MonkeyPatch, use_client: UseClient
    ) -> None:
        def _missing(_id: int) -> object:
            raise NotFoundError("nope", status_code=404, response_body={})

        use_client(fake_client(releases_get=_missing))
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
        monkeypatch.setattr(Path, "home", _no_home)
        result = CliRunner().invoke(cli, ["--debug", "tracks", "@r1"])
        assert result.exit_code == 1, result.exception
        assert isinstance(result.exception, SystemExit)  # not a teardown crash
        assert "✗ Tracklist @r1 not found." in result.stderr
        assert "cache     unknown (no home directory)  ttl 1h" in result.stderr

    def test_logger_restored_when_the_panel_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def broken_panel(*_a: object, **_kw: object) -> str:
            raise ValueError("panel")

        monkeypatch.setattr(trace, "render_panel", broken_panel)
        sdk_logger = logging.getLogger("discogs_sdk")
        level = sdk_logger.level
        result = CliRunner().invoke(cli, ["--debug", "skills"])
        assert isinstance(result.exception, ValueError)
        assert sdk_logger.handlers == []
        assert sdk_logger.level == level


def _api_rows(per_page: int, start: int, *, search: bool) -> list[dict[str, object]]:
    """Rows no filter in TestRequestCost keeps (an unofficial release, role
    Remix, year 1990), so client-side scans read every page of the listing."""
    shape: dict[str, object] = (
        {"year": "1990", "format": ["Unofficial Release"]}
        if search
        else {"year": 1990, "format": "Vinyl", "role": "Remix"}
    )
    return [
        {"id": start + i, "title": "T", "type": "release", **shape}
        for i in range(per_page)
    ]


class TestRequestCost:
    """Network sends per command, counted at the transport (the cost tables)."""

    @pytest.fixture(autouse=True)
    def _client(self, use_client: UseClient) -> None:
        self.sent: list[str] = []

        def _handle(request: httpx2.Request) -> httpx2.Response:
            path = request.url.path
            self.sent.append(path)
            page = int(request.url.params.get("page", "1"))
            per_page = int(request.url.params.get("per_page", "5"))
            pagination = {"page": page, "pages": 10, "per_page": per_page}
            if path.endswith(("/releases", "/versions", "/search")):
                search = path.endswith("/search")
                key = "results" if search else path.rsplit("/", 1)[1]
                rows = _api_rows(per_page, page * 100, search=search)
                return httpx2.Response(
                    200, json={"pagination": {**pagination, "items": 50}, key: rows}
                )
            entity_id = int(path.rsplit("/", 1)[1])
            if path.startswith("/marketplace/"):
                return httpx2.Response(200, json={})
            return httpx2.Response(
                200,
                json={"id": entity_id, "name": "N", "title": "T", "master_id": 3719},
            )

        client = Discogs(
            cache=False,
            http_client=httpx2.Client(transport=httpx2.MockTransport(_handle)),
            on_request=trace.record,
            max_retries=0,
        )
        use_client(client)

    @pytest.mark.parametrize(
        ("argv", "sends"),
        [
            ("get artist @a1", 1),
            ("get release @r1 @r2 @r3", 3),
            ("tracks @r1", 1),
            ("price @r1", 3),
            ("get price @r1 --json --full", 2),  # full documents: no release read
            # Lists: the JSON page reads no header attribute; text adds the
            # artist/label/master name.
            ("get releases @a1 --json", 1),
            ("get releases @a1", 2),
            ("get releases @l1 --json", 1),
            ("get releases @l1", 2),
            ("get versions @m1 --json", 1),
            ("get versions @m1", 2),
            # A release ref costs its lookup for master_id, the title is free.
            ("get versions @r1 --json", 2),
            ("get versions @r1", 2),
            # Client-side scans read 100-row pages until the page fills or the
            # listing ends: nothing matches here, so all 10 pages. Text adds
            # the header lookup; --max-requests caps the whole command.
            ("get releases @a1 --role Main --json", 10),
            ("get releases @a1 --role Main", 11),
            ("get releases @a1 --role Main --max-requests 4", 4),
            ("get releases @l1 --year 1999 --json", 10),
            ("get releases @l1 --year 1999", 11),
            ("get releases @l1 --year 1999 --json --max-requests 4", 4),
            ("search x --release-type all", 1),
            ("search artist x", 1),
            ("search x", 10),
            ("search x --max-requests 4", 4),
        ],
    )
    def test_sends(self, argv: str, sends: int) -> None:
        result = CliRunner().invoke(cli, argv.split())
        assert result.exit_code == 0, result.output
        assert len(self.sent) == sends, self.sent

    def test_versions_of_a_release_list_its_master(self) -> None:
        result = CliRunner().invoke(cli, ["get", "versions", "@r1", "--json"])
        assert result.exit_code == 0, result.output
        assert self.sent == ["/releases/1", "/masters/3719/versions"]
