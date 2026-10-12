"""Shared test setup."""

from __future__ import annotations

import pytest

pytest_plugins = ["pytester"]

# What a developer's shell may export that changes CLI output or SDK auth:
# the debug switch, and every credential the SDK falls back to when the
# client gets no explicit one (unsetting DISCOGS_TOKEN alone leaves OAuth on).
ISOLATED_ENV = (
    "AGENT_DISCOGS_DEBUG",
    "DISCOGS_TOKEN",
    "DISCOGS_CONSUMER_KEY",
    "DISCOGS_CONSUMER_SECRET",
    "DISCOGS_ACCESS_TOKEN",
    "DISCOGS_ACCESS_TOKEN_SECRET",
)


@pytest.fixture(autouse=True)
def _isolate_env(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run offline tests without ISOLATED_ENV; live tests keep the real values."""
    if request.node.get_closest_marker("live"):
        return
    for name in ISOLATED_ENV:
        monkeypatch.delenv(name, raising=False)
