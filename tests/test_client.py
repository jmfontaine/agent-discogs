"""Tests for SDK client initialization."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

import agent_discogs.client as client_module
from agent_discogs.client import get_client


class TestGetClient:
    @pytest.fixture(autouse=True)
    def _reset_singleton(self) -> Iterator[None]:
        client_module._client = None
        yield
        client_module._client = None

    def test_creates_client(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DISCOGS_TOKEN", "test-token")
        monkeypatch.setattr("agent_discogs.client.Discogs", lambda **kw: object())
        c = get_client()
        assert c is not None

    def test_singleton(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DISCOGS_TOKEN", "test-token")
        monkeypatch.setattr("agent_discogs.client.Discogs", lambda **kw: object())
        c1 = get_client()
        c2 = get_client()
        assert c1 is c2
