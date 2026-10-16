"""Tests for the `cache` command and the `--cached` / `--fresh` cache flags."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner
from discogs_sdk import (
    CacheMissError,
    RequestEvent,
)

from agent_discogs import cli, trace
from tests.conftest import (
    _tracks_fake,
    fake,
    fake_model,
)


class TestCacheCommand:
    def test_cache_clear(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "agent_discogs.commands.cache.get_client",
            lambda: fake(clear_cache=lambda: None),
        )
        result = CliRunner().invoke(cli, ["cache", "clear"])
        assert result.exit_code == 0
        assert "Cache cleared" in result.stdout

    def test_cache_help(self) -> None:
        result = CliRunner().invoke(cli, ["cache", "--help"])
        assert result.exit_code == 0
        assert "clear" in result.stdout


class _CacheScope:
    """Stand-in for `cache_only()` / `no_cache()`: records enter and exit."""

    def __init__(self, log: list[str], name: str) -> None:
        self.log, self.name = log, name

    def __call__(self) -> _CacheScope:
        return self

    def __enter__(self) -> None:
        self.log.append(f"enter {self.name}")

    def __exit__(self, *_exc: object) -> None:
        self.log.append(f"exit {self.name}")


class TestCacheFlags:
    @pytest.fixture(autouse=True)
    def _scoped_client(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The real get_client() over a fake singleton, so the pending scope
        is entered the way a real run enters it."""
        self.log: list[str] = []
        self.scoped = _tracks_fake(lambda: self.log.append("request"))
        self.scoped.cache_only = _CacheScope(self.log, "cache_only")
        self.scoped.no_cache = _CacheScope(self.log, "no_cache")
        monkeypatch.setattr("agent_discogs.client._client", self.scoped)

    def test_both_flags_is_a_usage_error(self) -> None:
        result = CliRunner().invoke(cli, ["--cached", "--fresh", "status"])
        assert result.exit_code == 2
        assert "--cached and --fresh are mutually exclusive." in result.stderr
        assert self.log == []

    def test_flag_after_the_command_names_its_place(self) -> None:
        result = CliRunner().invoke(cli, ["cache", "--fresh"])
        assert result.exit_code == 2
        assert result.stdout == ""
        assert result.stderr.endswith(
            "\n\nError: No such option '--fresh'. It is a global option: put it"
            " before the command, as in 'agent-discogs --fresh cache ...'.\n"
        )
        assert self.log == []

    def test_scope_waits_for_a_client(self) -> None:
        result = CliRunner().invoke(cli, ["--cached", "status"])
        assert result.exit_code == 0
        assert self.log == []

    def test_cached_wraps_the_whole_command(self) -> None:
        result = CliRunner().invoke(cli, ["--cached", "tracks", "@r123", "@r456"])
        assert result.exit_code == 0
        assert self.log == [
            "enter cache_only",
            "request",
            "request",
            "exit cache_only",
        ]

    def test_fresh_wraps_the_whole_command(self) -> None:
        result = CliRunner().invoke(cli, ["--fresh", "tracks", "@r123", "@r456"])
        assert result.exit_code == 0
        assert self.log == ["enter no_cache", "request", "request", "exit no_cache"]

    def test_cache_miss_is_a_per_ref_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        price = fake_model(conditions={"Mint (M)": fake(value=50.0, currency="USD")})
        stats = fake_model(num_for_sale=10, lowest_price=None)

        def _get(release_id: int) -> object:
            if release_id == 1:
                raise CacheMissError("GET", "https://api.discogs.com/releases/1")
            trace.record(
                RequestEvent(
                    method="GET",
                    url=f"https://api.discogs.com/releases/{release_id}",
                    status_code=200,
                    source="cache",
                    elapsed_ms=0.2,
                    attempts=0,
                    stored=False,
                    ratelimit=None,
                )
            )
            return fake(
                id=release_id,
                title=f"R{release_id}",
                price_suggestions=fake(get=lambda: price),
                marketplace_stats=fake(get=lambda: stats),
            )

        monkeypatch.setattr(self.scoped.releases, "get", _get)
        result = CliRunner().invoke(cli, ["--cached", "price", "@r10", "@r1", "@r20"])
        assert result.exit_code == 1
        out = result.stdout
        markers = [
            'Price Guide: @r10 "R10"',
            "✗ Not cached: GET /releases/1\n  Rerun without --cached",
            'Price Guide: @r20 "R20"',
        ]
        positions = [out.index(m) for m in markers]
        assert positions == sorted(positions)
        assert result.stderr == "api: 0 requests · cached\n"

        result = CliRunner().invoke(
            cli, ["--cached", "price", "--json", "@r10", "@r1", "@r20"]
        )
        assert result.exit_code == 1
        items = json.loads(result.stdout)
        assert [i.get("ref") for i in items] == ["@r10", "@r1", "@r20"]
        assert items[1]["error"]["code"] == "cache_miss"
        assert items[1]["error"]["message"] == "Not cached: GET /releases/1"
