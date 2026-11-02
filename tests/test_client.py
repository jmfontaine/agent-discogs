"""Tests for the client `get_client()` builds: what it sends, reports and caches,
and how long it lives."""

from __future__ import annotations

from collections.abc import Iterator

import httpx2
import pytest
from click.testing import CliRunner
from discogs_sdk import Discogs, DiscogsAPIError

import agent_discogs.client as client_module
from agent_discogs import cli, trace
from agent_discogs.client import get_client
from tests.conftest import RealSdk, _tracks_fake, invocation

_ALBUM = {"id": 1, "title": "Album"}


class TestGetClient:
    """`get_client()` with the real `Discogs` class; only HTTP is faked."""

    @pytest.fixture(autouse=True)
    def _real_client(
        self, real_sdk: RealSdk, monkeypatch: pytest.MonkeyPatch
    ) -> Iterator[None]:
        self.sdk = real_sdk
        self.cache_dir = real_sdk.cache_home / "agent-discogs"
        self.status = 200
        real_sdk.respond = lambda _request: httpx2.Response(self.status, json=_ALBUM)
        monkeypatch.setattr(trace, "_trace", trace.Trace())
        with invocation():
            yield

    def _events(self) -> list[tuple[str, int | None]]:
        return [(e.source, e.status_code) for e in trace._trace.events]

    def test_one_client_per_invocation(self) -> None:
        first = get_client()
        assert get_client() is first
        with invocation():
            assert get_client() is not first

    def test_building_it_touches_no_disk(self) -> None:
        get_client()
        assert not self.cache_dir.exists()
        assert self.sdk.sends == []

    def test_repeat_request_is_served_from_the_cache_dir(self) -> None:
        assert get_client().releases.get(1).title == "Album"
        assert get_client().releases.get(1).title == "Album"
        assert self.sdk.sends == ["/releases/1"]
        assert (self.cache_dir / "cache.db").is_file()

    def test_every_request_reaches_the_trace(self) -> None:
        for _ in range(2):  # the attribute read is what sends the lookup
            assert get_client().releases.get(1).title == "Album"
        assert self._events() == [("network", 200), ("cache", 200)]
        assert trace.sends() == 1

    def test_a_server_error_is_sent_once_without_sleeping(self) -> None:
        self.status = 503
        release = get_client().releases.get(1)
        with pytest.raises(DiscogsAPIError) as failure:
            _ = release.title
        assert failure.value.status_code == 503
        assert self.sdk.sends == ["/releases/1"]
        assert self._events() == [("network", 503)]
        assert self.sdk.sleeps == []


def test_needs_a_click_context() -> None:
    with pytest.raises(RuntimeError, match="no active click context"):
        get_client()


class TestInvocationLifetime:
    """The client lives for one `cli` invocation, built from its environment."""

    @pytest.fixture(autouse=True)
    def _real_client(self, real_sdk: RealSdk, monkeypatch: pytest.MonkeyPatch) -> None:
        self.sdk = real_sdk
        self.monkeypatch = monkeypatch
        self.status = 200
        self.tokens: list[str] = []
        self.log: list[str] = []

        def _respond(request: httpx2.Request) -> httpx2.Response:
            self.tokens.append(request.headers["authorization"])
            return httpx2.Response(self.status, json=_ALBUM)

        real_sdk.respond = _respond

    def _log_closes(self) -> None:
        """Append `close` to `self.log` when a built client is closed."""
        build = client_module._build

        def _build() -> Discogs:
            discogs = build()
            close = discogs.close

            def _close() -> None:
                self.log.append("close")
                close()

            self.monkeypatch.setattr(discogs, "close", _close)
            return discogs

        self.monkeypatch.setattr(client_module, "_build", _build)

    @pytest.mark.parametrize("fresh", [True, False])
    def test_the_next_invocation_reads_its_own_environment(self, fresh: bool) -> None:
        self.monkeypatch.setenv("DISCOGS_TOKEN", "token-A")
        assert CliRunner().invoke(cli, ["get", "release", "@r1"]).exit_code == 0
        dir_b = self.sdk.cache_home / "b"
        self.monkeypatch.setenv("DISCOGS_TOKEN", "token-B")
        self.monkeypatch.setenv("XDG_CACHE_HOME", str(dir_b))
        flags = ["--fresh"] if fresh else []
        result = CliRunner().invoke(cli, [*flags, "get", "release", "@r1"])
        assert result.exit_code == 0, result.output
        assert self.tokens == ["Discogs token=token-A", "Discogs token=token-B"]
        assert dir_b.exists() is not fresh  # --fresh opens no cache
        assert (dir_b / "agent-discogs" / "cache.db").is_file() is not fresh

    @pytest.mark.parametrize(("status", "exit_code"), [(200, 0), (404, 1)])
    def test_closed_when_the_invocation_ends(self, status: int, exit_code: int) -> None:
        self.status = status
        self._log_closes()
        result = CliRunner().invoke(cli, ["get", "release", "@r1"])
        assert result.exit_code == exit_code
        assert self.log == ["close"]

    def test_closed_on_keyboard_interrupt(self) -> None:
        def _interrupt(_request: httpx2.Request) -> httpx2.Response:
            raise KeyboardInterrupt

        self.sdk.respond = _interrupt
        self._log_closes()
        result = CliRunner().invoke(cli, ["get", "release", "@r1"])
        assert result.exit_code == 130
        assert self.log == ["close"]

    def test_status_builds_none(self) -> None:
        self._log_closes()
        assert CliRunner().invoke(cli, ["status"]).exit_code == 0
        assert (self.sdk.clients, self.log) == ([], [])

    def test_closed_after_the_footer_and_panel(self) -> None:
        self._log_closes()
        self.monkeypatch.setattr(trace, "finish", lambda: self.log.append("finish"))
        result = CliRunner().invoke(cli, ["get", "release", "@r1"])
        assert result.exit_code == 0
        assert self.log == ["finish", "close"]


def test_a_failing_close_changes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    closes: list[str] = []

    def _run(close: object) -> tuple[int, str, str]:
        client = _tracks_fake(lambda: None)
        client.close = close
        monkeypatch.setattr(client_module, "_build", lambda: client)
        result = CliRunner().invoke(cli, ["tracks", "@r123"])
        return result.exit_code, result.stdout, result.stderr

    def _fail() -> None:
        closes.append("close")
        msg = "cannot close"
        raise OSError(msg)

    clean = _run(lambda: None)
    assert _run(_fail) == clean
    assert closes == ["close"]
    assert clean[0] == 0
