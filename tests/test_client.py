"""Tests for the client `get_client()` builds: what it sends, reports and caches."""

from __future__ import annotations

import httpx2
import pytest
from discogs_sdk import DiscogsAPIError

from agent_discogs import trace
from agent_discogs.client import get_client
from tests.conftest import RealSdk


class TestGetClient:
    """`get_client()` with the real `Discogs` class; only HTTP is faked."""

    @pytest.fixture(autouse=True)
    def _real_client(self, real_sdk: RealSdk, monkeypatch: pytest.MonkeyPatch) -> None:
        self.sdk = real_sdk
        self.cache_dir = real_sdk.cache_home / "agent-discogs"
        self.status = 200
        real_sdk.respond = lambda _request: httpx2.Response(
            self.status, json={"id": 1, "title": "Album"}
        )
        monkeypatch.setattr(trace, "_trace", trace.Trace())

    def _events(self) -> list[tuple[str, int | None]]:
        return [(e.source, e.status_code) for e in trace._trace.events]

    def test_one_client_per_process(self) -> None:
        assert get_client() is get_client()

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
