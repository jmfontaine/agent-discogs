"""Tests for `get stats`, a release's community and Marketplace numbers,
against the real SDK client over a mock transport."""

from __future__ import annotations

import json
from typing import Any

import httpx2
import pytest
from click.testing import CliRunner, Result

from agent_discogs import cli
from tests.conftest import _FakeAPI, json_error, text_error

_STATS = "/marketplace/stats/"

_NIN = [{"id": 3857, "name": "Nine Inch Nails", "join": ""}]

# Release id: the release record Discogs sends (the fields `get stats` reads).
_RELEASES: dict[int, dict[str, Any]] = {
    847868: {
        "title": "The Downward Spiral",
        "year": 1994,
        "artists": _NIN,
        "community": {
            "have": 7618,
            "want": 1355,
            "rating": {"count": 602, "average": 4.43},
        },
    },
    352665: {
        "title": "The Downward Spiral",
        "year": 2004,
        "artists": _NIN,
        "community": {
            "have": 1978,
            "want": 734,
            "rating": {"count": 251, "average": 4.76},
        },
    },
    # Nothing for sale, as Discogs sends it: null, not 0.
    1: {"title": "None For Sale", "year": 1990, "artists": _NIN},
    # Blocked from sale: null counts too.
    2: {"title": "Blocked", "year": 1990, "artists": _NIN},
    # Copies for sale, but no lowest price.
    3: {"title": "No Price", "year": 1990, "artists": _NIN},
    # No votes: Discogs sends a zero rating.
    4: {
        "title": "Unrated",
        "artists": _NIN,
        "community": {"have": 3, "want": 1, "rating": {"count": 0, "average": 0}},
    },
}

# Release id: (num_for_sale, lowest price, blocked_from_sale), as sent.
_MARKET: dict[int, tuple[int | None, float | None, bool]] = {
    847868: (45, 0.8, False),
    352665: (9, 72.0, False),
    1: (None, None, False),
    2: (None, None, True),
    3: (3, None, False),
    4: (1, 5.0, False),
}

_FIRST = (
    '@r847868 "The Downward Spiral" by Nine Inch Nails 1994 · have 7,618 · '
    "want 1,355 · rating 4.43/5 (602 votes) · for sale: 45 from EUR 0.80"
)
_SECOND = (
    '@r352665 "The Downward Spiral" by Nine Inch Nails 2004 · have 1,978 · '
    "want 734 · rating 4.76/5 (251 votes) · for sale: 9 from EUR 72.00"
)


class _Discogs:
    """`/releases/{id}` and `/marketplace/stats/{id}` for `_RELEASES` and
    `_MARKET`, priced in the `curr_abbr` asked for (EUR, the account's, by
    default). A path in `status` answers with that status."""

    def __init__(self, status: dict[str, int] | None = None) -> None:
        self.status = status or {}

    def __call__(self, path: str, params: dict[str, str]) -> object:
        if path in self.status:
            code = self.status[path]
            headers = {"Retry-After": "30"} if code == 429 else {}
            return httpx2.Response(code, json={"message": "Nope."}, headers=headers)
        release_id = int(path.rsplit("/", 1)[-1])
        if path.startswith("/releases/"):
            return {"id": release_id, **_RELEASES[release_id]}
        assert path.startswith(_STATS), path
        count, lowest, blocked = _MARKET[release_id]
        price = (
            None
            if lowest is None
            else {"value": lowest, "currency": params.get("curr_abbr", "EUR")}
        )
        return {
            "num_for_sale": count,
            "lowest_price": price,
            "blocked_from_sale": blocked,
        }


class _Harness:
    def __init__(
        self, monkeypatch: pytest.MonkeyPatch, status: dict[str, int] | None = None
    ) -> None:
        self.api = _FakeAPI(monkeypatch, _Discogs(status))

    def run(self, *args: str) -> tuple[Result, list[tuple[str, dict[str, str]]]]:
        start = len(self.api.sends)
        result = CliRunner().invoke(cli, list(args))
        return result, list(self.api.sends[start:])


def _paths(sent: list[tuple[str, dict[str, str]]]) -> list[str]:
    return [path for path, _ in sent]


def _document(result: Result) -> Any:
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


class TestGetStats:
    def test_one_line_per_release_two_requests_each(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch)
        result, sent = h.run("get", "stats", "@r847868", "@r352665")
        assert result.exit_code == 0, result.output
        assert result.stdout == f"{_FIRST}\n\n{_SECOND}\n"
        assert _paths(sent) == [
            "/releases/847868",
            "/marketplace/stats/847868",
            "/releases/352665",
            "/marketplace/stats/352665",
        ]
        assert all(params == {} for _, params in sent)

    def test_a_raw_id_is_a_release(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _Harness(monkeypatch)
        result, _ = h.run("get", "stats", "847868")
        assert result.stdout == f"{_FIRST}\n"

    def test_currency_goes_on_the_stats_request_only(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch)
        argv = ["get", "stats", "@r847868", "@r352665", "--currency", "eur"]
        result, sent = h.run(*argv)
        assert result.exit_code == 0, result.output
        assert [(path, params) for path, params in sent] == [
            ("/releases/847868", {}),
            ("/marketplace/stats/847868", {"curr_abbr": "EUR"}),
            ("/releases/352665", {}),
            ("/marketplace/stats/352665", {"curr_abbr": "EUR"}),
        ]
        again, sent = h.run(*argv)
        assert sent == []
        assert again.stdout == result.stdout
        # Without --currency the stats request is another one; the releases
        # are cached.
        _, sent = h.run("get", "stats", "@r847868", "@r352665")
        assert sent == [
            ("/marketplace/stats/847868", {}),
            ("/marketplace/stats/352665", {}),
        ]

    def test_another_currency_names_its_code(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch)
        result, _ = h.run("get", "stats", "@r847868", "--currency", "JPY")
        assert result.stdout.rstrip().endswith("· for sale: 45 from JPY 0.80")
        data = _document(
            h.run("get", "stats", "@r847868", "--currency", "jpy", "--json")[0]
        )
        assert data["market"] == {"for_sale": 45, "lowest": 0.8, "currency": "JPY"}

    @pytest.mark.parametrize(
        ("ref", "tail", "market"),
        [
            ("@r1", "· for sale: 0", {"for_sale": 0}),
            ("@r2", "· for sale: 0", {"for_sale": 0, "blocked": True}),
            ("@r3", "· for sale: 3", {"for_sale": 3}),
        ],
        ids=["nothing-for-sale", "blocked", "no-price"],
    )
    def test_null_market_numbers(
        self,
        monkeypatch: pytest.MonkeyPatch,
        ref: str,
        tail: str,
        market: dict[str, object],
    ) -> None:
        h = _Harness(monkeypatch)
        result, _ = h.run("get", "stats", ref)
        assert result.exit_code == 0, result.output
        line = result.stdout.rstrip("\n")
        assert line.endswith(f"1990 {tail}")
        assert "from" not in line
        assert _document(h.run("get", "stats", ref, "--json")[0])["market"] == market

    def test_no_votes_no_rating(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _Harness(monkeypatch)
        result, _ = h.run("get", "stats", "@r4")
        assert result.stdout == (
            '@r4 "Unrated" by Nine Inch Nails · have 3 · want 1 · for sale: 1 '
            "from EUR 5.00\n"
        )
        data = _document(h.run("get", "stats", "@r4", "--json")[0])
        assert data["community"] == {"have": 3, "want": 1, "votes": 0}

    def test_json_one_ref_is_an_object(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _Harness(monkeypatch)
        result, _ = h.run("get", "stats", "@r847868", "--json")
        assert _document(result) == {
            "ref": "@r847868",
            "title": "The Downward Spiral",
            "artists": [{"ref": "@a3857", "name": "Nine Inch Nails"}],
            "year": 1994,
            "community": {"have": 7618, "want": 1355, "rating": 4.43, "votes": 602},
            "market": {"for_sale": 45, "lowest": 0.8, "currency": "EUR"},
        }

    def test_json_several_refs_are_a_list(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch, status={"/releases/5": 404})
        result, sent = h.run("get", "stats", "@r847868", "@r5", "@r352665", "--json")
        assert result.exit_code == 1
        first, missing, last = json.loads(result.stdout)
        assert first["ref"] == "@r847868"
        assert set(missing) == {"ref", "error"}
        assert missing["ref"] == "@r5"
        assert missing["error"]["code"] == "not_found"
        assert last["market"]["lowest"] == 72.0
        # The missing release costs its one request, and no stats request.
        assert "/releases/5" in _paths(sent)
        assert "/marketplace/stats/5" not in _paths(sent)

    def test_full_is_both_records(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _Harness(monkeypatch)
        result, _ = h.run("get", "stats", "@r847868", "--json", "--full")
        data = _document(result)
        assert set(data) == {"release", "marketplace_stats"}
        assert data["release"]["id"] == 847868
        assert data["release"]["community"]["have"] == 7618
        assert data["marketplace_stats"] == {
            "blocked_from_sale": False,
            "lowest_price": {"currency": "EUR", "value": 0.8},
            "num_for_sale": 45,
        }

    def test_a_missing_release_sends_no_stats_request(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch, status={"/releases/5": 404})
        result, sent = h.run("get", "stats", "@r5")
        assert "Stats @r5 not found." in text_error(result)
        assert _paths(sent) == ["/releases/5"]

    def test_a_failed_stats_request_fails_the_ref_only(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch, status={"/marketplace/stats/847868": 429})
        result, sent = h.run("get", "stats", "@r847868", "@r352665")
        assert result.exit_code == 1
        first, second = result.stdout.rstrip("\n").split("\n\n")
        assert first.startswith("✗ ")
        assert "have" not in first  # never a row with half the numbers
        assert second == _SECOND
        assert _paths(sent).count("/marketplace/stats/847868") == 1  # no retry

    def test_cached_serves_the_cache_only(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch)
        result, sent = h.run("--cached", "get", "stats", "@r847868", "@r352665")
        assert sent == []
        assert result.exit_code == 1
        blocks = result.stdout.rstrip("\n").split("\n\n")
        assert [
            block.startswith("✗ Not cached: GET /releases/") for block in blocks
        ] == [
            True,
            True,
        ]

        # The releases cached, the stats not: a miss on either fails the ref.
        h.run("get", "release", "@r847868")
        h.run("get", "stats", "@r352665")
        result, sent = h.run(
            "--cached", "get", "stats", "@r847868", "@r352665", "--json"
        )
        assert sent == []
        assert result.exit_code == 1
        first, second = json.loads(result.stdout)
        assert first["error"]["code"] == "cache_miss"
        assert "/marketplace/stats/847868" in first["error"]["message"]
        assert second["market"]["for_sale"] == 9

    def test_fresh_sends_both_again(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _Harness(monkeypatch)
        h.run("get", "stats", "@r847868")
        _, sent = h.run("--fresh", "get", "stats", "@r847868")
        assert _paths(sent) == ["/releases/847868", "/marketplace/stats/847868"]

    @pytest.mark.parametrize(
        "argv",
        [
            "get stats @r1 --currency XYZ",
            "get stats @r1 --limit 5",
            "get stats @r1 --max-requests 3",
            "get stats @r1 --owned",
            "get stats @r1 --compact",
            "get stats @r1 --max-price 5 --currency EUR",
            "get stats " + " ".join(f"@r{i}" for i in range(1, 12)),
        ],
    )
    def test_rejected_before_any_request(
        self, monkeypatch: pytest.MonkeyPatch, argv: str
    ) -> None:
        h = _Harness(monkeypatch)
        result, sent = h.run(*argv.split(), "--json")
        assert json_error(result)["code"] == "invalid_argument"
        assert sent == []

    def test_rejection_messages(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _Harness(monkeypatch)
        result, sent = h.run("get", "stats", "@r1", "--limit", "5")
        assert text_error(result).startswith(
            "✗ --limit does not apply to 'get stats'. 'get stats' takes --currency."
        )
        result, _ = h.run("get", "stats", "@r1", "--currency", "xyz")
        assert text_error(result).startswith(
            "✗ --currency must be one of USD, GBP, EUR, CAD, AUD, JPY, CHF, MXN, "
            "BRL, NZD, SEK, ZAR ('xyz' given)."
        )
        refs = [f"@r{i}" for i in range(1, 12)]
        result, _ = h.run("get", "stats", *refs)
        assert "At most 10 refs per command (11 given)." in text_error(result)
        assert sent == []
        assert h.api.sends == []

    def test_a_master_ref_is_its_own_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch)
        result, sent = h.run("get", "stats", "@m3719", "@r847868")
        assert result.exit_code == 1
        first, second = result.stdout.rstrip("\n").split("\n\n")
        assert first == (
            "✗ @m3719 is a master, not a release. Use a release ref or ID with "
            "'get stats'."
        )
        assert second == _FIRST
        assert _paths(sent) == ["/releases/847868", "/marketplace/stats/847868"]

    def test_needs_no_credentials(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No username, no profile: the two requests and nothing else."""
        h = _Harness(monkeypatch)
        _, sent = h.run("get", "stats", "@r847868", "--json")
        assert "/oauth/identity" not in _paths(sent)
