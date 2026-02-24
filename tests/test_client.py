"""Tests for SDK client initialization."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

import agent_discogs.client as client_module
from agent_discogs.client import get_client, has_token


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

    def test_no_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DISCOGS_TOKEN", raising=False)
        calls: list[dict[str, object]] = []

        def fake_discogs(**kw: object) -> object:
            calls.append(kw)
            return object()

        monkeypatch.setattr("agent_discogs.client.Discogs", fake_discogs)
        get_client()
        assert len(calls) == 1
        assert calls[0]["token"] is None


class TestHasToken:
    def test_has_token_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DISCOGS_TOKEN", "abc")
        assert has_token() is True

    def test_has_token_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DISCOGS_TOKEN", raising=False)
        assert has_token() is False
