"""Tests for shared configuration."""

from __future__ import annotations

import pytest

from agent_discogs.config import has_token


class TestHasToken:
    def test_has_token_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DISCOGS_TOKEN", "abc")
        assert has_token() is True

    def test_has_token_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DISCOGS_TOKEN", raising=False)
        assert has_token() is False
