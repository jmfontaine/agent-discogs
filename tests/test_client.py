"""Tests for SDK client initialization."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

import agent_discogs.client as client_module
from agent_discogs.client import get_client


class TestGetClient:
    @pytest.fixture(autouse=True)
    def _reset_singleton(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> Iterator[None]:
        # get_client() opens the SQLite cache: keep it out of the real cache
        # dir, and close what each fake client was handed.
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        self.calls: list[dict[str, Any]] = []

        def fake_discogs(**kw: Any) -> object:
            self.calls.append(kw)
            return object()

        monkeypatch.setattr("agent_discogs.client.Discogs", fake_discogs)
        client_module._client = None
        yield
        client_module._client = None
        for call in self.calls:
            call["cache"].close()

    def test_creates_client(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DISCOGS_TOKEN", "test-token")
        c = get_client()
        assert c is not None

    def test_singleton(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DISCOGS_TOKEN", "test-token")
        c1 = get_client()
        c2 = get_client()
        assert c1 is c2

    def test_no_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DISCOGS_TOKEN", raising=False)
        get_client()
        assert len(self.calls) == 1
        assert self.calls[0]["token"] is None
