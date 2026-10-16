"""Tests for `get stats`, a release's community and Marketplace numbers, and
`get versions --market`, each listed pressing's Marketplace numbers, against
the real SDK client over a mock transport."""

from __future__ import annotations

import json
from typing import Any

import httpx2
import pytest
from click.testing import CliRunner, Result

from agent_discogs import cli
from tests.conftest import _FakeAPI, _too_many, json_error, text_error

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
    # A pressing of master 1, "Blue Train".
    10: {"title": "Blue Train", "year": 1958, "artists": _NIN, "master_id": 1},
}

# Release id: (num_for_sale, lowest price, blocked_from_sale), as sent.
_MARKET: dict[int, tuple[int | None, float | None, bool]] = {
    847868: (45, 0.8, False),
    352665: (9, 72.0, False),
    1: (None, None, False),
    2: (None, None, True),
    3: (3, None, False),
    4: (1, 5.0, False),
    # The versions of master 1: priced, priced, nothing for sale, no price,
    # blocked, and the unofficial one.
    11: (6, 427.5, False),
    12: (2, 2429.99, False),
    13: (None, None, False),
    14: (3, None, False),
    15: (None, None, True),
    16: (1, 9.0, False),
}

_VERSIONS = "/masters/1/versions"
_VERSION_IDS = range(11, 17)


def _version(release_id: int) -> dict[str, Any]:
    """A version row of master 1, as Discogs sends it; 16 is unofficial, and
    the token's user owns one copy of 11."""
    unofficial = release_id == 16
    return {
        "id": release_id,
        "title": "Blue Train",
        "released": "1958",
        "country": "US",
        "label": "Blue Note",
        "catno": "BLP 1577",
        "format": "LP, Unofficial Release" if unofficial else "LP, Album, Mono",
        "stats": {
            "community": {"in_collection": 100 + release_id, "in_wantlist": release_id},
            "user": {"in_collection": int(release_id == 11), "in_wantlist": 0},
        },
    }


def _versions_page(params: dict[str, str]) -> dict[str, Any]:
    """A page of master 1's versions; like Discogs, `format` keeps the rows
    that list it as a whole description."""
    rows = [_version(i) for i in _VERSION_IDS]
    if "format" in params:
        rows = [r for r in rows if params["format"] in r["format"].split(", ")]
    page, per = int(params["page"]), int(params["per_page"])
    pagination = {
        "page": page,
        "pages": max(1, -(-len(rows) // per)),
        "items": len(rows),
        "per_page": per,
    }
    return {"pagination": pagination, "versions": rows[(page - 1) * per : page * per]}


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
    default), and master 1 with its versions. A path in `status` answers with
    that status; with `limited_page`, the versions list claims that many
    pages and that page answers 429."""

    def __init__(self, status: dict[str, int] | None = None) -> None:
        self.status = status or {}
        self.limited_page: int | None = None

    def __call__(self, path: str, params: dict[str, str]) -> object:
        if path == _VERSIONS and self.limited_page == int(params["page"]):
            return _too_many()
        if path in self.status:
            code = self.status[path]
            headers = {"Retry-After": "30"} if code == 429 else {}
            return httpx2.Response(code, json={"message": "Nope."}, headers=headers)
        if path == "/masters/1":
            return {"id": 1, "title": "Blue Train"}
        if path == _VERSIONS:
            page = _versions_page(params)
            if self.limited_page is not None:
                page["pagination"]["pages"] = self.limited_page
            return page
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
        self.discogs = _Discogs(status)
        self.api = _FakeAPI(monkeypatch, self.discogs)

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


def _stats_ids(sent: list[tuple[str, dict[str, str]]]) -> list[int]:
    """The release ids of the stats requests sent, in order."""
    return [int(p.removeprefix(_STATS)) for p in _paths(sent) if p.startswith(_STATS)]


def _markets(document: dict[str, Any]) -> list[Any]:
    return [row.get("market", "absent") for row in document["results"]]


def _rows(result: Result) -> list[str]:
    return [line for line in result.stdout.splitlines() if line.startswith("@r")]


_ROW_11 = (
    "@r11 1958 US · Blue Note BLP 1577 · LP, Album, Mono · have 111 · want 11 "
    "· for sale: 6 from EUR 427.50"
)
_EUR_11 = {"for_sale": 6, "lowest": 427.5, "currency": "EUR"}
_EUR_12 = {"for_sale": 2, "lowest": 2429.99, "currency": "EUR"}


class TestVersionsMarket:
    """`get versions --market`: each listed pressing's Marketplace numbers,
    one `/marketplace/stats/{id}` lookup per row after the listing."""

    def test_json_one_lookup_per_row_after_the_page(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch)
        result, sent = h.run("get", "versions", "@m1", "--market", "--limit", "3")
        assert result.exit_code == 0, result.output
        assert _paths(sent)[:2] == ["/masters/1", _VERSIONS]

        h = _Harness(monkeypatch)
        argv = ["get", "versions", "@m1", "--market", "--limit", "3", "--json"]
        result, sent = h.run(*argv)
        assert _paths(sent) == [_VERSIONS, *(f"{_STATS}{i}" for i in (11, 12, 13))]
        assert all(params == {} for path, params in sent if path.startswith(_STATS))
        document = _document(result)
        assert _markets(document) == [_EUR_11, _EUR_12, {"for_sale": 0}]
        assert document["market_read"] == {"complete": True, "rows_read": 3, "rows": 3}

        # A rerun is served from the cache; --fresh sends it all again.
        again, sent = h.run(*argv)
        assert sent == []
        assert again.stdout == result.stdout
        _, sent = h.run("--fresh", *argv)
        assert _paths(sent) == [_VERSIONS, *(f"{_STATS}{i}" for i in (11, 12, 13))]

    def test_text_rows_end_in_the_market_tail(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch)
        result, sent = h.run("get", "versions", "@m1", "--market", "--limit", "5")
        assert result.exit_code == 0, result.output
        # The header goes first, as for a scan, so --max-requests counts it.
        assert _paths(sent) == [
            "/masters/1",
            _VERSIONS,
            *(f"{_STATS}{i}" for i in range(11, 16)),
        ]
        assert result.stdout.splitlines()[0].startswith('Versions of @m1 "Blue Train"')
        rows = _rows(result)
        assert rows[0] == _ROW_11
        assert [row.split(" · want ")[1] for row in rows[1:]] == [
            "12 · for sale: 2 from EUR 2429.99",
            "13 · for sale: 0",
            "14 · for sale: 3",
            "15 · for sale: 0",
        ]
        assert "Market:" not in result.stdout

    def test_a_release_ref_finds_its_master_first(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch)
        argv = ["get", "versions", "@r10", "--market", "--limit", "1"]
        _, sent = h.run(*argv, "--json")
        assert _paths(sent) == ["/releases/10", _VERSIONS, f"{_STATS}11"]
        h = _Harness(monkeypatch)
        result, sent = h.run(*argv)
        assert _paths(sent) == ["/releases/10", _VERSIONS, f"{_STATS}11"]
        assert _rows(result) == [_ROW_11]

    def test_currency_prices_every_lookup(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch)
        argv = ["get", "versions", "@m1", "--market", "--limit", "2"]
        result, sent = h.run(*argv, "--currency", "usd")
        assert result.exit_code == 0, result.output
        assert [params for path, params in sent if path.startswith(_STATS)] == [
            {"curr_abbr": "USD"},
            {"curr_abbr": "USD"},
        ]
        assert _rows(result)[0].endswith("· for sale: 6 from USD 427.50")
        # The cache entry is keyed by URL: EUR rows are other requests.
        document = _document(h.run(*argv, "--currency", "EUR", "--json")[0])
        assert _markets(document) == [_EUR_11, _EUR_12]

    def test_null_numbers_and_blocked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _Harness(monkeypatch)
        argv = ["get", "versions", "@m1", "--market", "--limit", "5", "--json"]
        document = _document(h.run(*argv)[0])
        assert _markets(document)[2:] == [
            {"for_sale": 0},
            {"for_sale": 3},
            {"for_sale": 0, "blocked": True},
        ]

    @pytest.mark.parametrize(
        "argv",
        [
            "get versions @m1 --market --currency XXX",
            "get versions @m1 --currency EUR",
            "get versions @m1 --market --max-price 5 --currency EUR",
            "get releases @a1 --market",
            "get stats @r1 --market",
            "get versions @m1 @m2 --market --max-requests 5",
        ],
    )
    def test_rejected_before_any_request(
        self, monkeypatch: pytest.MonkeyPatch, argv: str
    ) -> None:
        h = _Harness(monkeypatch)
        result, sent = h.run(*argv.split(), "--json")
        assert json_error(result)["code"] == "invalid_argument"
        assert sent == []

    def test_currency_needs_market(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _Harness(monkeypatch)
        result, _ = h.run("get", "versions", "@m1", "--currency", "EUR")
        assert text_error(result).startswith(
            "✗ --currency needs --market with get versions."
        )

    def test_max_requests_stops_the_lookups(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch)
        argv = ["get", "versions", "@m1", "--market", "--limit", "5"]
        result, sent = h.run(*argv, "--max-requests", "4", "--json")
        assert _paths(sent) == [_VERSIONS, *(f"{_STATS}{i}" for i in (11, 12, 13))]
        document = _document(result)
        assert _markets(document)[3:] == [None, None]
        assert document["market_read"] == {
            "complete": False,
            "rows_read": 3,
            "rows": 5,
            "stop_reason": "max_requests",
        }

        h = _Harness(monkeypatch)
        result, sent = h.run(*argv, "--max-requests", "4")
        assert result.exit_code == 0, result.output
        assert _paths(sent) == ["/masters/1", _VERSIONS, f"{_STATS}11", f"{_STATS}12"]
        rows = _rows(result)
        assert "for sale:" in rows[1]
        assert "for sale:" not in rows[2]
        assert (
            "Market: the lookups stopped at --max-requests 4 (2 of 5 rows "
            'checked): rows without "for sale:" were not checked.'
        ) in result.stdout.splitlines()

    def test_a_cap_of_one_sends_the_page_only(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch)
        argv = ["get", "versions", "@m1", "--market", "--max-requests", "1"]
        result, sent = h.run(*argv, "--json")
        assert _paths(sent) == [_VERSIONS]
        assert set(_markets(_document(result))) == {None}
        h = _Harness(monkeypatch)
        result, sent = h.run(*argv)
        assert result.exit_code == 0, result.output
        assert _paths(sent) == ["/masters/1", _VERSIONS]

    def test_cached_header_leaves_the_budget_to_the_lookups(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch)
        h.run("get", "master", "@m1")  # only the master is cached
        argv = ["get", "versions", "@m1", "--market", "--max-requests", "4"]
        result, sent = h.run(*argv)
        assert result.exit_code == 0, result.output
        assert _paths(sent) == [_VERSIONS, *(f"{_STATS}{i}" for i in (11, 12, 13))]

    def test_a_scan_shares_the_cap(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _Harness(monkeypatch)
        argv = ["get", "versions", "@m1", "--release-type", "official", "--market"]
        result, sent = h.run(*argv, "--max-requests", "5", "--json")
        # The scan's one page, then four lookups: the fifth row is unchecked.
        assert _paths(sent) == [_VERSIONS, *(f"{_STATS}{i}" for i in range(11, 15))]
        document = _document(result)
        assert [row["ref"] for row in document["results"]] == [
            f"@r{i}" for i in range(11, 16)
        ]
        assert document["market_read"]["rows_read"] == 4
        assert document["market_read"]["stop_reason"] == "max_requests"

    def test_a_429_keeps_the_rows_read(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _Harness(monkeypatch, status={f"{_STATS}13": 429})
        argv = ["get", "versions", "@m1", "--market", "--limit", "5", "--json"]
        result, sent = h.run(*argv)
        assert _stats_ids(sent) == [11, 12, 13]
        document = _document(result)
        assert _markets(document) == [_EUR_11, _EUR_12, None, None, None]
        assert document["market_read"] == {
            "complete": False,
            "rows_read": 2,
            "rows": 5,
            "stop_reason": "rate_limited",
            "retry_after": 30,
        }
        text, _ = h.run(*argv[:-1])
        assert text.exit_code == 0, text.output
        assert (
            "Market: a lookup was rate limited, retry in 30s (2 of 5 rows "
            'checked): rows without "for sale:" were not checked.'
        ) in text.stdout.splitlines()

        # After the wait, the rerun sends only the lookups not read yet.
        h.discogs.status = {}
        result, sent = h.run(*argv)
        assert _stats_ids(sent) == [13, 14, 15]
        assert _document(result)["market_read"]["complete"] is True

    def test_a_429_on_the_page_is_an_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch, status={_VERSIONS: 429})
        result, sent = h.run("get", "versions", "@m1", "--market", "--json")
        assert json_error(result)["code"] == "rate_limited"
        assert _stats_ids(sent) == []

    def test_a_scan_stopped_by_a_429_sends_no_lookup(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch)
        h.discogs.limited_page = 2
        argv = ["get", "versions", "@m1", "--release-type", "official", "--market"]
        result, sent = h.run(*argv, "--limit", "6", "--json")
        assert result.exit_code == 0, result.output
        assert _paths(sent) == [_VERSIONS, _VERSIONS]
        document = _document(result)
        assert _markets(document) == [None] * 5
        assert document["market_read"] == {
            "complete": False,
            "rows_read": 0,
            "rows": 5,
            "stop_reason": "rate_limited",
            "retry_after": 30,
        }
        result, sent = h.run(*argv, "--limit", "6")
        assert _paths(sent) == ["/masters/1", _VERSIONS]
        assert (
            "Market: the listing was rate limited, retry in 30s (0 of 5 rows "
            'checked): rows without "for sale:" were not checked.'
        ) in result.stdout.splitlines()

    def test_another_lookup_failure_fails_the_command(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch, status={f"{_STATS}12": 500})
        result, _ = h.run("get", "versions", "@m1", "--market", "--json")
        assert json_error(result)["status"] == 500

    def test_cached_marks_the_rows_not_cached(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch)
        argv = ["get", "versions", "@m1", "--limit", "3"]
        h.run(*argv, "--json")  # only the page is cached
        result, sent = h.run("--cached", *argv, "--market", "--json")
        assert sent == []
        assert result.exit_code == 1
        document = json.loads(result.stdout)
        assert [row["error"]["code"] for row in document["results"]] == [
            "cache_miss"
        ] * 3
        assert "market" not in document["results"][0]

        # As text the header must be cached too, else the command fails.
        assert "Not cached: GET /masters/1" in text_error(
            h.run("--cached", *argv, "--market")[0]
        )
        h.run(*argv)  # now the header is cached
        result, sent = h.run("--cached", *argv, "--market")
        assert sent == []
        assert result.exit_code == 1
        assert _rows(result)[0].endswith("· ✗ Not cached: GET /marketplace/stats/11")
        assert "Rerun without --cached to check the rows marked ✗." in result.stdout

        # After a full run everything is cached: nothing sent, exit 0.
        h.run(*argv, "--market", "--json")
        result, sent = h.run("--cached", *argv, "--market", "--json")
        assert sent == []
        assert _markets(_document(result)) == [_EUR_11, _EUR_12, {"for_sale": 0}]

    def test_the_continuation_repeats_market_and_currency(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch)
        argv = ["get", "versions", "@m1", "--market", "--currency", "EUR"]
        result, _ = h.run(*argv, "--limit", "2", "--max-requests", "9")
        assert result.exit_code == 0, result.output
        assert result.stdout.splitlines()[-1] == (
            "Next page: agent-discogs get versions @m1 --market --currency EUR "
            "--max-requests 9 --limit 2 --page 2"
        )

    def test_owned_marks_come_before_the_market_tail(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DISCOGS_TOKEN", "test-token")
        h = _Harness(monkeypatch)
        argv = ["get", "versions", "@m1", "--owned", "--wanted", "--market"]
        result, _ = h.run(*argv, "--limit", "2")
        assert result.exit_code == 0, result.output
        assert [row.split(" · want ")[1] for row in _rows(result)] == [
            "11 · owned · not wanted · for sale: 6 from EUR 427.50",
            "12 · not owned · not wanted · for sale: 2 from EUR 2429.99",
        ]

    def test_full_records_get_their_market(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch)
        argv = ["get", "versions", "@m1", "--market", "--limit", "2"]
        document = _document(h.run(*argv, "--json", "--full")[0])
        assert document["results"][0]["id"] == 11
        assert _markets(document) == [_EUR_11, _EUR_12]
        assert document["market_read"]["complete"] is True

    def test_several_refs_each_read_their_own_rows(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch)
        h.run("get", "versions", "@m1", "--limit", "1", "--json")
        argv = ["get", "versions", "@m1", "@r10", "--market", "--limit", "1", "--json"]
        result, sent = h.run("--cached", *argv)
        assert sent == []
        # A row not cached fails the command, not its ref.
        assert result.exit_code == 1
        first, second = json.loads(result.stdout)
        assert first["results"][0]["error"]["code"] == "cache_miss"
        assert second["error"]["code"] == "cache_miss"  # /releases/10
