"""Tests for `get wantlist` and its `--for-sale` scan, against the real SDK
client over a mock transport."""

from __future__ import annotations

import json
import shlex
from typing import Any

import httpx2
import pytest
from click.testing import CliRunner, Result

from agent_discogs import cli
from tests.conftest import _FakeAPI, json_error, text_error

_WANTS = "/users/me/wants"
_STATS = "/marketplace/stats/"

# Release id: (copies for sale, lowest price, blocked from sale).
_MARKET = {
    2: (3, 12.5, False),
    5: (1, 20.0, False),
    7: (2, 25.0, False),
    9: (4, 5.0, True),
}


def _too_many() -> httpx2.Response:
    return httpx2.Response(429, json={}, headers={"Retry-After": "30"})


class _Wantlist:
    """The token's user `me` wants releases 1 to `size`, each `Album <id>`
    by Artist on Label (`CAT<id>`), want 2 with notes and a rating. Their
    Marketplace stats follow `_MARKET`, priced in the `curr_abbr` asked for
    (USD by default); every other release has none for sale. The stats
    requests numbered in `throttle` (1-based) are answered with a 429.
    `titles` replaces the title of the wants it numbers."""

    def __init__(
        self,
        size: int = 10,
        *,
        throttle: tuple[int, ...] = (),
        titles: dict[int, str | None] | None = None,
    ) -> None:
        self.size, self.throttle, self.titles = size, throttle, titles or {}
        self.stats_sent = 0

    def __call__(self, path: str, params: dict[str, str]) -> object:
        if path == "/oauth/identity":
            return {"id": 7, "username": "me"}
        if path == _WANTS:
            return self._wants(int(params["page"]), int(params["per_page"]))
        if path.startswith(_STATS):
            self.stats_sent += 1
            if self.stats_sent in self.throttle:
                return _too_many()
            release_id = int(path.rsplit("/", 1)[-1])
            count, lowest, blocked = _MARKET.get(release_id, (0, None, False))
            price = (
                None
                if lowest is None
                else {"value": lowest, "currency": params.get("curr_abbr", "USD")}
            )
            return {
                "num_for_sale": count,
                "lowest_price": price,
                "blocked_from_sale": blocked,
            }
        raise AssertionError(path)

    def _wants(self, page: int, per: int) -> object:
        wants = []
        for i in range((page - 1) * per + 1, min(page * per, self.size) + 1):
            want: dict[str, Any] = {
                "id": i,
                "rating": 0,
                "basic_information": {
                    "id": i,
                    "title": self.titles.get(i, f"Album {i}"),
                    "year": 1990,
                    "artists": [{"name": "Artist", "join": ""}],
                    "labels": [{"name": "Label", "catno": f"CAT{i}"}],
                    "formats": [{"name": "Vinyl", "qty": "1", "descriptions": ["LP"]}],
                },
            }
            if i == 2:
                want.update(notes="Sealed only", rating=4)
            wants.append(want)
        pagination = {
            "page": page,
            "pages": -(-self.size // per),
            "items": self.size,
            "per_page": per,
        }
        return {"pagination": pagination, "wants": wants}


class _Harness:
    """`_FakeAPI` over `wantlist`. With `token`, the environment holds the
    credentials of a user, as `get wantlist` needs."""

    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        wantlist: _Wantlist,
        *,
        token: bool = True,
    ) -> None:
        if token:
            monkeypatch.setenv("DISCOGS_TOKEN", "test-token")
        self.api = _FakeAPI(monkeypatch, wantlist)

    def run(self, *args: str) -> tuple[Result, list[tuple[str, dict[str, str]]]]:
        start = len(self.api.sends)
        result = CliRunner().invoke(cli, list(args))
        return result, list(self.api.sends[start:])


def _paths(sent: list[tuple[str, dict[str, str]]]) -> list[str]:
    return [path for path, _ in sent]


def _stats_ids(sent: list[tuple[str, dict[str, str]]]) -> list[int]:
    return [int(path.rsplit("/", 1)[-1]) for path, _ in sent if path.startswith(_STATS)]


def _document(result: Result) -> dict[str, Any]:
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def _continuation(result: Result) -> list[str]:
    """The argv of the `Continue scan:` or `Next page:` footer."""
    line = next(
        line
        for line in result.stdout.splitlines()
        if line.startswith(("Continue scan:", "Next page:"))
    )
    return shlex.split(line.split(":", 1)[1])[1:]  # drop "agent-discogs"


class TestWantlist:
    def test_pages_server_side(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _Harness(monkeypatch, _Wantlist(150))
        result, sent = h.run("get", "wantlist", "--limit", "100", "--json")
        document = _document(result)
        assert _paths(sent) == ["/oauth/identity", _WANTS]
        assert sent[1][1] == {"page": "1", "per_page": "100"}
        assert len(document["results"]) == 100
        assert document["pagination"] == {
            "page": 1,
            "total_items": 150,
            "total_pages": 2,
        }
        assert document["results"][1] == {
            "ref": "@r2",
            "title": "Artist - Album 2",
            "year": 1990,
            "label": "Label",
            "catno": "CAT2",
            "format": "Vinyl (LP)",
            "notes": "Sealed only",
            "rating": 4,
        }
        assert "rating" not in document["results"][0]  # 0 is unrated

        second, sent = h.run(
            "get", "wantlist", "--limit", "100", "--page", "2", "--json"
        )
        assert _paths(sent) == [_WANTS]  # the username is cached
        assert [row["ref"] for row in _document(second)["results"]] == [
            f"@r{i}" for i in range(101, 151)
        ]

        again, sent = h.run("get", "wantlist", "--limit", "100", "--json")
        assert sent == []
        assert again.stdout == result.stdout

    @pytest.mark.parametrize("title", [None, ""])
    def test_a_want_without_a_title_is_titled_by_its_artist(
        self, monkeypatch: pytest.MonkeyPatch, title: str | None
    ) -> None:
        h = _Harness(monkeypatch, _Wantlist(2, titles={1: title}))
        result, _ = h.run("get", "wantlist")
        assert result.exit_code == 0, result.output
        assert '@r1 "Artist" 1990 · ' in result.stdout
        rows = _document(h.run("get", "wantlist", "--json")[0])["results"]
        assert [row["title"] for row in rows] == ["Artist", "Artist - Album 2"]

    def test_text_rows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _Harness(monkeypatch, _Wantlist(150))
        result, _ = h.run("get", "wantlist")
        assert result.exit_code == 0, result.output
        lines = result.stdout.splitlines()
        assert lines[0] == "Wantlist: me (page 1, 5 of 150 wants)"
        assert lines[3] == (
            '@r2 "Artist - Album 2" 1990 · Label CAT2 · Vinyl (LP) · notes: Sealed only'
        )
        assert lines[-1] == "Next page: agent-discogs get wantlist --page 2"

    def test_full_gives_the_want_records(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _Harness(monkeypatch, _Wantlist())
        result, _ = h.run("get", "wantlist", "--json", "--full")
        row = _document(result)["results"][1]
        assert row["notes"] == "Sealed only"
        assert row["basic_information"]["labels"][0]["catalog_number"] == "CAT2"


class TestForSale:
    def test_keeps_the_wants_for_sale(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _Harness(monkeypatch, _Wantlist())
        result, sent = h.run("get", "wantlist", "--for-sale", "--json")
        document = _document(result)
        assert _paths(sent)[:2] == ["/oauth/identity", _WANTS]
        assert _stats_ids(sent) == list(range(1, 11))
        assert len(sent) == 12
        assert all("curr_abbr" not in params for _, params in sent)
        assert [(row["ref"], row["market"]) for row in document["results"]] == [
            ("@r2", {"for_sale": 3, "lowest": 12.5, "currency": "USD"}),
            ("@r5", {"for_sale": 1, "lowest": 20.0, "currency": "USD"}),
            ("@r7", {"for_sale": 2, "lowest": 25.0, "currency": "USD"}),
        ]
        assert document["pagination"]["capped"] is False
        assert document["pagination"]["filtered"] is True

        text, sent = h.run("get", "wantlist", "--for-sale")
        assert sent == []
        assert text.stdout.splitlines()[2].endswith("· for sale: 3 from USD 12.50")

    def test_max_price_in_the_named_currency(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch, _Wantlist())
        argv = ["get", "wantlist", "--for-sale", "--max-price", "20"]
        result, sent = h.run(*argv, "--currency", "eur", "--json")
        stats = [params for path, params in sent if path.startswith(_STATS)]
        assert len(stats) == 10
        assert all(params == {"curr_abbr": "EUR"} for params in stats)
        assert [
            (row["ref"], row["market"]["lowest"], row["market"]["currency"])
            for row in _document(result)["results"]
        ] == [("@r2", 12.5, "EUR"), ("@r5", 20.0, "EUR")]

    def test_max_requests_caps_and_the_continuation_resumes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch, _Wantlist())
        argv = ["get", "wantlist", "--for-sale", "--max-requests", "5"]
        result, sent = h.run(*argv, "--json")
        assert len(sent) == 5
        assert _stats_ids(sent) == [1, 2, 3]
        pagination = _document(result)["pagination"]
        assert pagination["capped"] is True
        assert pagination["stop_reason"] == "max_requests"
        assert pagination["next_cursor"] == "2:3"

        h = _Harness(monkeypatch, _Wantlist())  # a cold cache again
        checked: list[int] = []
        text, sent = h.run(*argv)
        checked += _stats_ids(sent)
        assert "scan stopped at --max-requests 5" in text.stdout
        while "Continue scan:" in text.stdout:
            text, sent = h.run(*_continuation(text))
            assert text.exit_code == 0, text.output
            assert len(sent) <= 5
            checked += _stats_ids(sent)
        assert checked == list(range(1, 11))  # each want checked once, in order

    def test_the_smallest_cap_still_moves_on_under_fresh(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Under --fresh the username and the wants page are sent again on
        every run: the minimum cap, 3, leaves one send to check a want."""
        h = _Harness(monkeypatch, _Wantlist())
        argv = ["--fresh", "get", "wantlist", "--for-sale", "--max-requests", "3"]
        checked: list[int] = []
        result, sent = h.run(*argv)
        for _ in range(3):
            assert result.exit_code == 0, result.output
            assert _paths(sent)[:2] == ["/oauth/identity", _WANTS]
            assert len(sent) == 3
            checked += _stats_ids(sent)
            result, sent = h.run(*_continuation(result))
        assert checked == [1, 2, 3]

    @pytest.mark.parametrize("amount", ["1000000", "19.9999999", "0.0000001"])
    def test_max_price_reads_the_same_in_header_and_continuation(
        self, monkeypatch: pytest.MonkeyPatch, amount: str
    ) -> None:
        """The continuation keeps the exact threshold: a rounded 19.9999999
        would let in the EUR 20 want the first page excluded."""
        h = _Harness(monkeypatch, _Wantlist(200))
        argv = ["get", "wantlist", "--for-sale", "--currency", "JPY", "--limit", "1"]
        result, _ = h.run(*argv, "--max-price", amount, "--max-requests", "4")
        assert f"max-price={amount} currency=JPY" in result.stdout.splitlines()[0]
        continuation = _continuation(result)
        assert continuation[continuation.index("--max-price") + 1] == amount

    def test_rate_limit_keeps_the_rows_matched(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch, _Wantlist(throttle=(4,)))
        result, sent = h.run("get", "wantlist", "--for-sale", "--json")
        document = _document(result)
        assert _stats_ids(sent) == [1, 2, 3, 4]
        assert [row["ref"] for row in document["results"]] == ["@r2"]
        pagination = document["pagination"]
        assert pagination["stop_reason"] == "rate_limited"
        assert pagination["retry_after"] == 30
        assert pagination["next_cursor"] == "2:3"

    def test_cached_reports_each_miss(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _Harness(monkeypatch, _Wantlist())
        result, sent = h.run("--cached", "get", "wantlist", "--json")
        assert json_error(result)["code"] == "cache_miss"
        assert sent == []

        h.run("get", "wantlist", "--limit", "100")  # wants cached, stats not
        # The username and the wants are cached: the 4 sends check wants 1-4.
        h.run("get", "wantlist", "--for-sale", "--max-requests", "4")
        result, sent = h.run("--cached", "get", "wantlist", "--for-sale", "--json")
        assert sent == []
        assert result.exit_code == 1
        rows = json.loads(result.stdout)["results"]
        assert rows[0]["ref"] == "@r2"
        assert rows[0]["market"]["for_sale"] == 3
        assert [row["error"]["code"] for row in rows[1:]] == ["cache_miss"] * 4
        assert [row["ref"] for row in rows[1:]] == ["@r5", "@r6", "@r7", "@r8"]

        text, _ = h.run("--cached", "get", "wantlist", "--for-sale")
        assert text.exit_code == 1
        assert "· ✗ Not cached: GET /marketplace/stats/5" in text.stdout
        assert "Rerun without --cached" in text.stdout

    @pytest.mark.parametrize(
        "argv",
        [
            "get wantlist --max-price 20 --currency EUR",
            "get wantlist --currency EUR",
            "get wantlist --max-requests 5",
            "get wantlist --for-sale --max-price 20",
            "get wantlist --for-sale --max-price -1 --currency EUR",
            "get wantlist --for-sale --max-price nan --currency EUR",
            "get wantlist --for-sale --max-requests 1",
            "get wantlist --for-sale --max-requests 2",
            "get wantlist --for-sale --currency XYZ",
            "get wantlist --for-sale --page 2",
            "get wantlist --after 2:3",
            "get wantlist @r1",
            "get release @r1 --for-sale",
        ],
    )
    def test_misplaced_flags_fail_before_any_request(
        self, monkeypatch: pytest.MonkeyPatch, argv: str
    ) -> None:
        h = _Harness(monkeypatch, _Wantlist())
        result, sent = h.run(*argv.split(), "--json")
        assert json_error(result)["code"] == "invalid_argument"
        assert sent == []

    def test_an_unknown_currency_is_an_invalid_argument(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Like any bad flag value: exit 1 and the error, not a usage error."""
        h = _Harness(monkeypatch, _Wantlist())
        result, sent = h.run("get", "wantlist", "--for-sale", "--currency", "xyz")
        assert text_error(result).startswith(
            "✗ --currency must be one of USD, GBP, EUR, CAD, AUD, JPY, CHF, MXN, "
            "BRL, NZD, SEK, ZAR ('xyz' given)."
        )
        assert sent == []


class TestNeedsAUser:
    @pytest.mark.parametrize("mode", ["none", "consumer"])
    def test_fails_before_any_request(
        self, monkeypatch: pytest.MonkeyPatch, mode: str
    ) -> None:
        if mode == "consumer":
            monkeypatch.setenv("DISCOGS_CONSUMER_KEY", "key")
            monkeypatch.setenv("DISCOGS_CONSUMER_SECRET", "secret")
        h = _Harness(monkeypatch, _Wantlist(), token=False)
        result, sent = h.run("get", "wantlist", "--for-sale", "--json")
        error = json_error(result)
        assert error["code"] == "auth_required"
        assert "get wantlist" in error["message"]
        assert sent == []

        result, sent = h.run("get", "wantlist")
        assert "DISCOGS_TOKEN" in text_error(result)
        assert sent == []


def test_other_nouns_still_need_a_ref() -> None:
    result = CliRunner().invoke(cli, ["get", "release"])
    assert result.exit_code == 2
    assert "Missing argument 'REF...'" in result.stderr
