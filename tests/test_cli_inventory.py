"""Tests for `get inventory` and its client-side filters, against the real SDK
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

_INVENTORY = "/users/seller/inventory"
_WANTS = "/users/me/wants"
_FOLDER_ALL = "/users/me/collection/folders/0/releases"

_CONDITIONS = [
    "Mint (M)",
    "Near Mint (NM or M-)",
    "Very Good Plus (VG+)",
    "Very Good (VG)",
    "Good Plus (G+)",
    "Good (G)",
    "Fair (F)",
    "Poor (P)",
]
_SLEEVES = [*_CONDITIONS, "Generic", "Not Graded", "No Cover"]
_COUNTRIES = ["Germany", "France", "United States"]
_GRADES = {"M", "NM", "VG+", "VG", "G+", "G", "F", "P"}
_VG_PLUS_OR_BETTER = {"M", "NM", "VG+"}


def _too_many() -> httpx2.Response:
    return httpx2.Response(429, json={}, headers={"Retry-After": "30"})


class _Seller:
    """Discogs as the token's user `me` sees it, browsing `seller`.

    `seller` lists `size` listings. Listing `i` (0-based) has the listing id
    `5000 + i` and is a copy of release `100 + i`, graded `_CONDITIONS[i % 8]`
    with a `_SLEEVES[i % 11]` sleeve, priced EUR `30 + i % 20`, shipping from
    `_COUNTRIES[i % 3]`; `overrides` replaces fields of some listings.

    `me` wants the releases in `wanted`, then more (90000 and up) to make
    `wants` wants, and owns the releases in `owned` (one page). `throttle`
    answers with a 429: `"identity"` the username, `"inventory:N"` inventory
    page N, `"wants:N"` wants page N.
    """

    def __init__(
        self,
        size: int = 300,
        *,
        wants: int = 150,
        wanted: tuple[int, ...] = (),
        owned: tuple[int, ...] = (),
        overrides: dict[int, dict[str, Any]] | None = None,
        throttle: tuple[str, ...] = (),
    ) -> None:
        self.size, self.throttle = size, throttle
        self.overrides = overrides or {}
        self.wanted = [*wanted, *range(90000, 90000 + wants - len(wanted))]
        self.owned = owned

    def listing(self, i: int) -> dict[str, Any]:
        # Shaped like what Discogs sends today, empty shipping prices
        # included (seen on every listing of five sellers sampled).
        listing: dict[str, Any] = {
            "id": 5000 + i,
            "status": "For Sale",
            "condition": _CONDITIONS[i % 8],
            "sleeve_condition": _SLEEVES[i % 11],
            "price": {"value": 30.0 + i % 20, "currency": "EUR"},
            "original_price": {
                "curr_abbr": "EUR",
                "curr_id": 3,
                "formatted": f"€{30 + i % 20}.00",
                "value": 30.0 + i % 20,
            },
            "shipping_price": {},
            "original_shipping_price": {},
            "ships_from": _COUNTRIES[i % 3],
            "posted": "2024-03-01T23:20:17-07:00",
            "allow_offers": False,
            "offer_submitted": False,
            "shipping_is_blocked": True,
            "uri": f"https://www.discogs.com/sell/item/{5000 + i}",
            "seller": {
                "id": 9,
                "username": "seller",
                "stats": {"rating": "100.0", "stars": 5.0, "total": 1200},
            },
            "release": {
                "id": 100 + i,
                "artist": "Artist",
                "title": f"Album {i}",
                "format": "LP, Album",
                "label": "Label",
                "catalog_number": f"CAT{i}",
                "year": 1990,
                "images": [],
                "stats": {"community": {"in_wantlist": 3, "in_collection": 9}},
            },
        }
        listing.update(self.overrides.get(i, {}))
        return listing

    def __call__(self, path: str, params: dict[str, str]) -> object:
        if path == "/oauth/identity":
            if "identity" in self.throttle:
                return _too_many()
            return {"id": 7, "username": "me"}
        page = int(params.get("page", 1))
        per = int(params.get("per_page", 50))
        if path.startswith("/users/") and path.endswith("/inventory"):
            if f"inventory:{page}" in self.throttle:
                return _too_many()
            rows = [self.listing(i) for i in _window(page, per, self.size)]
            return {"pagination": _pagination(page, per, self.size), "listings": rows}
        if path == _WANTS:
            if f"wants:{page}" in self.throttle:
                return _too_many()
            ids = [self.wanted[i] for i in _window(page, per, len(self.wanted))]
            wants = [{"id": rid, "basic_information": {"id": rid}} for rid in ids]
            size = len(self.wanted)
            return {"pagination": _pagination(page, per, size), "wants": wants}
        if path == _FOLDER_ALL:
            items = [{"id": rid, "instance_id": n} for n, rid in enumerate(self.owned)]
            return {"pagination": _pagination(1, per, len(items)), "releases": items}
        raise AssertionError(path)


def _window(page: int, per: int, size: int) -> range:
    return range((page - 1) * per, min(page * per, size))


def _pagination(page: int, per: int, size: int) -> dict[str, int]:
    return {"page": page, "pages": -(-size // per), "items": size, "per_page": per}


class _Harness:
    """`_FakeAPI` over `seller`. With `token`, the environment holds the
    credentials of a user, as `--in-wantlist` and `--owned` need."""

    def __init__(
        self, monkeypatch: pytest.MonkeyPatch, seller: _Seller, *, token: bool = True
    ) -> None:
        if token:
            monkeypatch.setenv("DISCOGS_TOKEN", "test-token")
        self.api = _FakeAPI(monkeypatch, seller)

    def run(self, *args: str) -> tuple[Result, list[tuple[str, dict[str, str]]]]:
        start = len(self.api.sends)
        result = CliRunner().invoke(cli, list(args))
        return result, list(self.api.sends[start:])


def _paths(sent: list[tuple[str, dict[str, str]]]) -> list[str]:
    return [path for path, _ in sent]


def _document(result: Result) -> dict[str, Any]:
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def _listings(result: Result) -> list[int]:
    return [row["listing"] for row in _document(result)["results"]]


def _continuation(result: Result) -> list[str]:
    """The argv of the `Continue scan:` or `Next page:` footer."""
    line = next(
        line
        for line in result.stdout.splitlines()
        if line.startswith(("Continue scan:", "Next page:"))
    )
    return shlex.split(line.split(":", 1)[1])[1:]  # drop "agent-discogs"


class TestInventory:
    @pytest.mark.parametrize("token", [True, False])
    def test_one_request_and_no_identity(
        self, monkeypatch: pytest.MonkeyPatch, *, token: bool
    ) -> None:
        h = _Harness(monkeypatch, _Seller(), token=token)
        result, sent = h.run("get", "inventory", "seller", "--json")
        document = _document(result)
        assert sent == [(_INVENTORY, {"page": "1", "per_page": "5"})]
        assert document["pagination"] == {
            "page": 1,
            "total_items": 300,
            "total_pages": 60,
        }
        assert document["results"][2] == {
            "listing": 5002,
            "ref": "@r102",
            "title": "Artist - Album 2",
            "format": "LP, Album",
            "condition": "VG+",
            "sleeve": "VG+",
            "price": 32.0,
            "currency": "EUR",
            "ships_from": "United States",
            "listed": "2024-03-01T23:20:17-07:00",
            "url": "https://www.discogs.com/sell/item/5002",
        }

    def test_text_rows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _Harness(monkeypatch, _Seller())
        result, _ = h.run("get", "inventory", "seller")
        assert result.exit_code == 0, result.output
        lines = result.stdout.splitlines()
        assert lines[0] == "Inventory: seller (page 1, 5 of 300 listings)"
        assert lines[3] == (
            '@r101 "Artist - Album 1" · LP, Album · NM/NM · EUR 31.00 '
            "· ships from France · listed 2024-03-01"
        )
        assert lines[-1] == "Next page: agent-discogs get inventory seller --page 2"

    def test_a_row_leaves_out_what_discogs_did_not_send(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        bare = {"release": {"id": 7}, "sleeve_condition": None, "posted": None}
        nameless = {"release": {"title": "Only Title"}}
        seller = _Seller(5, overrides={0: bare, 1: nameless})
        h = _Harness(monkeypatch, seller)
        result, _ = h.run("get", "inventory", "seller")
        lines = result.stdout.splitlines()
        assert lines[2] == "@r7 · M · EUR 30.00 · ships from Germany"
        assert lines[3] == (
            'listing 5001 "Only Title" · NM/NM · EUR 31.00 · ships from France '
            "· listed 2024-03-01"
        )
        rows = _document(h.run("get", "inventory", "seller", "--json")[0])["results"]
        assert rows[0] == {
            "listing": 5000,
            "ref": "@r7",
            "condition": "M",
            "price": 30.0,
            "currency": "EUR",
            "ships_from": "Germany",
            "url": "https://www.discogs.com/sell/item/5000",
        }
        assert "ref" not in rows[1]

    def test_full_gives_the_listing_records(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch, _Seller())
        result, _ = h.run("get", "inventory", "seller", "--json", "--full")
        row = _document(result)["results"][0]
        assert row["sleeve_condition"] == "Mint (M)"
        assert row["release"]["catalog_number"] == "CAT0"
        # Discogs sends `{}` for both; discogs-sdk 0.8.1 reads them as none.
        assert row["shipping_price"] is None
        assert row["original_shipping_price"] is None

    def test_sorts_server_side(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _Harness(monkeypatch, _Seller())
        argv = ["get", "inventory", "seller", "--sort", "price", "--desc"]
        result, sent = h.run(*argv, "--limit", "20", "--page", "3")
        assert result.exit_code == 0, result.output
        assert sent == [
            (
                _INVENTORY,
                {"sort": "price", "sort_order": "desc", "page": "3", "per_page": "20"},
            )
        ]
        assert _continuation(result) == [
            *("get", "inventory", "seller", "--sort", "price", "--desc"),
            *("--limit", "20", "--page", "4"),
        ]

    def test_a_username_starting_with_a_dash_follows_double_dash(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch, _Seller())
        result, sent = h.run("get", "inventory", "--", "-seller")
        assert result.exit_code == 0, result.output
        assert _paths(sent) == ["/users/-seller/inventory"]
        assert _continuation(result) == [
            *("get", "inventory", "--page", "2", "--", "-seller")
        ]


class TestFilters:
    def test_condition_keeps_that_grade_or_better(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch, _Seller())
        argv = ["get", "inventory", "seller", "--condition", "VG+", "--limit", "100"]
        result, sent = h.run(*argv, "--json")
        document = _document(result)
        expected = [5000 + i for i in range(300) if i % 8 < 3][:100]
        assert _listings(result) == expected
        assert {row["condition"] for row in document["results"]} == _VG_PLUS_OR_BETTER
        assert all(params["per_page"] == "100" for _, params in sent)
        assert document["pagination"]["filtered"] is True
        assert document["pagination"]["total_items"] == 300

        for spelling in ("vg+", "Very Good Plus (VG+)", "very good plus (vg+)"):
            again, _ = h.run(*argv[:4], spelling, *argv[5:], "--json")
            assert again.stdout == result.stdout

    def test_sleeve_drops_ungraded_sleeves(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch, _Seller())
        argv = ["get", "inventory", "seller", "--sleeve", "vg+", "--limit", "100"]
        result, _ = h.run(*argv, "--json")
        expected = [5000 + i for i in range(300) if i % 11 < 3]
        assert _listings(result) == expected
        sleeves = {row["sleeve"] for row in _document(result)["results"]}
        assert sleeves == _VG_PLUS_OR_BETTER

        poor, _ = h.run("get", "inventory", "seller", "--sleeve", "P", "--json")
        assert {row["sleeve"] for row in _document(poor)["results"]} <= _GRADES

    def test_ships_from_matches_the_country_in_any_case(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch, _Seller())
        argv = ["get", "inventory", "seller", "--ships-from", "GERMANY", "--json"]
        result, _ = h.run(*argv)
        assert _listings(result) == [5000, 5003, 5006, 5009, 5012]
        partial, _ = h.run("get", "inventory", "seller", "--ships-from", "Germ")
        assert partial.exit_code == 0, partial.output
        assert "@r" not in partial.stdout

    def test_max_price_in_the_named_currency(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        overrides = {
            0: {"price": {"value": 20.0, "currency": "EUR"}},
            1: {"price": {"value": 20.01, "currency": "EUR"}},
            2: {"price": {"value": 5.0, "currency": "USD"}},
            3: {"price": None},
            4: {"price": {"value": 3.0, "currency": "GBP"}, "condition": "Poor (P)"},
        }
        h = _Harness(monkeypatch, _Seller(10, overrides=overrides))
        argv = ["get", "inventory", "seller", "--max-price", "20"]
        result, sent = h.run(*argv, "--currency", "eur", "--json")
        document = _document(result)
        assert _listings(result) == [5000]
        assert document["pagination"]["skipped_currency"] == 2
        assert all("curr_abbr" not in params for _, params in sent)

        # A listing that fails another filter is not counted as skipped.
        graded, _ = h.run(*argv, "--currency", "EUR", "--condition", "G", "--json")
        assert _document(graded)["pagination"]["skipped_currency"] == 1

        text, _ = h.run(*argv, "--currency", "EUR")
        assert text.stdout.splitlines()[0] == (
            "Inventory: seller max-price=20 currency=EUR "
            "(page 1, 1 of ≤10 listings; 2 listings in other currencies skipped)"
        )
        one, _ = h.run(*argv, "--currency", "EUR", "--condition", "G")
        assert "1 listing in another currency skipped" in one.stdout

    def test_page_continuation_keeps_every_filter(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch, _Seller())
        argv = [
            *("get", "inventory", "seller", "--condition", "Near Mint (NM or M-)"),
            *("--sleeve", "g", "--ships-from", "France", "--max-price", "45.5"),
            *("--currency", "EUR", "--sort", "listed", "--limit", "2"),
        ]
        result, _ = h.run(*argv)
        assert result.exit_code == 0, result.output
        # The header shows each grade as the minimum it is; the
        # continuation repeats the plain flags.
        assert result.stdout.splitlines()[0].startswith(
            "Inventory: seller condition≥NM sleeve≥G ships-from=France "
            "max-price=45.5 currency=EUR (page 1, 2 of ≤300 listings"
        )
        continuation = _continuation(result)
        assert continuation[:3] == ["get", "inventory", "seller"]
        for flag, value in [
            ("--condition", "NM"),
            ("--sleeve", "G"),
            ("--ships-from", "France"),
            ("--max-price", "45.5"),
            ("--currency", "EUR"),
            ("--sort", "listed"),
            ("--limit", "2"),
        ]:
            assert continuation[continuation.index(flag) + 1] == value
        assert "--after" in continuation

    def test_max_requests_caps_and_the_continuation_resumes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch, _Seller())
        argv = ["get", "inventory", "seller", "--condition", "VG+", "--limit", "100"]
        result, sent = h.run(*argv, "--max-requests", "1", "--json")
        document = _document(result)
        assert len(sent) == 1
        assert document["pagination"]["capped"] is True
        assert document["pagination"]["stop_reason"] == "max_requests"
        first = _listings(result)
        assert first == [5000 + i for i in range(100) if i % 8 < 3]

        h = _Harness(monkeypatch, _Seller())  # a cold cache again
        text, _ = h.run(*argv, "--max-requests", "1")
        assert "scan stopped at --max-requests 1" in text.stdout.splitlines()[0]
        continuation = _continuation(text)
        assert continuation == [
            *argv[:5],
            *("--max-requests", "1", "--limit", "100", "--after", "2:100"),
        ]
        seen: list[int] = []
        cursor: str | None = "2:100"
        while cursor is not None:
            continuation[-1] = cursor
            result, sent = h.run(*continuation, "--json")
            assert len(sent) == 1
            seen += _listings(result)
            cursor = _document(result)["pagination"]["next_cursor"]
        expected = [5000 + i for i in range(300) if i % 8 < 3]
        assert first + seen == expected  # every match once, in order

    def test_rate_limit_keeps_the_rows_matched(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch, _Seller(throttle=("inventory:2",)))
        argv = ["get", "inventory", "seller", "--condition", "VG+", "--limit", "100"]
        result, sent = h.run(*argv, "--json")
        document = _document(result)
        assert len(sent) == 2
        assert _listings(result) == [5000 + i for i in range(100) if i % 8 < 3]
        pagination = document["pagination"]
        assert pagination["stop_reason"] == "rate_limited"
        assert pagination["retry_after"] == 30
        assert pagination["next_cursor"] == "2:100"

    def test_a_rate_limit_on_the_first_page_is_an_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch, _Seller(throttle=("inventory:1",)))
        result, _ = h.run("get", "inventory", "seller", "--condition", "VG+", "--json")
        assert json_error(result)["code"] == "rate_limited"


class TestInWantlist:
    # Listings 5, 105, 205 and 299 are of wanted releases, one per page and
    # the last listing.
    _WANTED = (105, 205, 305, 399)

    def test_keeps_the_listings_of_wanted_releases(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch, _Seller(wanted=self._WANTED))
        argv = ["get", "inventory", "seller", "--in-wantlist", "--limit", "100"]
        result, sent = h.run(*argv, "--json")
        document = _document(result)
        assert _paths(sent) == [
            "/oauth/identity",
            _WANTS,
            _WANTS,
            _INVENTORY,
            _INVENTORY,
            _INVENTORY,
        ]
        assert _listings(result) == [5005, 5105, 5205, 5299]
        assert document["wantlist"] == {
            "complete": True,
            "wants_read": 150,
            "total_wants": 150,
        }
        assert document["pagination"]["next_cursor"] is None

        text, sent = h.run(*argv)
        assert sent == []
        assert text.stdout.splitlines()[0] == (
            "Inventory: seller in wantlist (page 1, 4 of ≤300 listings)"
        )

    def test_a_capped_wantlist_read_scans_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch, _Seller(wanted=self._WANTED))
        argv = ["get", "inventory", "seller", "--in-wantlist", "--max-requests", "2"]
        result, sent = h.run(*argv, "--json")
        document = _document(result)
        assert _paths(sent) == ["/oauth/identity", _WANTS]
        assert document["results"] == []
        assert document["pagination"] == {
            "page": 1,
            "total_items": None,
            "total_pages": None,
            "filtered": True,
            "capped": True,
            "next_cursor": "1:0",
            "stop_reason": "max_requests",
        }
        assert document["wantlist"] == {
            "complete": False,
            "wants_read": 100,
            "total_wants": 150,
            "stop_reason": "max_requests",
        }

        h = _Harness(monkeypatch, _Seller(wanted=self._WANTED))  # a cold cache
        text, sent = h.run(*argv)
        assert _paths(sent) == ["/oauth/identity", _WANTS]
        lines = text.stdout.splitlines()
        assert lines[0] == (
            "Inventory: seller in wantlist "
            "(--max-requests 2 ran out before the scan started)"
        )
        assert lines[2] == (
            "Wantlist: the read stopped at --max-requests 2 (100 of 150 wants "
            "read), so no listing was scanned: a listing is kept only against "
            "the whole wantlist."
        )
        fresh, _ = h.run("--fresh", *argv)
        assert fresh.stdout.splitlines()[2].endswith(
            "the whole wantlist. --fresh reads it again on every run, so "
            "continue with a higher --max-requests."
        )
        # The username and the first wants page are cached now: the rerun
        # reads the rest of the wantlist, then the first inventory page.
        again, sent = h.run(*_continuation(text), "--json")
        assert _paths(sent) == [_WANTS, _INVENTORY]
        assert _listings(again) == [5005]

    def test_a_rate_limited_wantlist_read_scans_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seller = _Seller(wanted=self._WANTED, throttle=("wants:2",))
        h = _Harness(monkeypatch, seller)
        argv = ["get", "inventory", "seller", "--in-wantlist", "--owned"]
        result, sent = h.run(*argv, "--json")
        document = _document(result)
        assert _paths(sent) == ["/oauth/identity", _WANTS, _WANTS]
        assert document["results"] == []
        assert document["pagination"]["stop_reason"] == "rate_limited"
        assert document["pagination"]["retry_after"] == 30
        assert document["wantlist"]["stop_reason"] == "rate_limited"
        assert document["wantlist"]["retry_after"] == 30
        assert "collection" not in document

        text, _ = h.run(*argv)
        lines = text.stdout.splitlines()
        assert lines[0] == (
            "Inventory: seller in wantlist "
            "(rate limited before the scan started, retry in 30s)"
        )
        assert lines[2].startswith("Wantlist: the read was rate limited, retry in 30s")

    def test_a_rate_limit_on_the_username_scans_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _Harness(monkeypatch, _Seller(throttle=("identity",)))
        result, sent = h.run("get", "inventory", "seller", "--in-wantlist", "--json")
        document = _document(result)
        assert _paths(sent) == ["/oauth/identity"]
        assert document["wantlist"] == {
            "complete": False,
            "wants_read": 0,
            "total_wants": None,
            "stop_reason": "rate_limited",
            "retry_after": 30,
        }


class TestOwned:
    def test_marks_a_listing_by_its_release(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # 5000 is listing 0's own id; its release, 100, is not owned.
        seller = _Seller(6, owned=(5000, 101), overrides={2: {"release": {}}})
        h = _Harness(monkeypatch, seller)
        result, sent = h.run("get", "inventory", "seller", "--owned", "--json")
        document = _document(result)
        assert _paths(sent) == [_INVENTORY, "/oauth/identity", _FOLDER_ALL]
        assert [row["owned"] for row in document["results"]] == [
            0,
            1,
            None,
            0,
            0,
        ]
        assert document["collection"]["complete"] is True

        text, _ = h.run("get", "inventory", "seller", "--owned")
        lines = text.stdout.splitlines()
        assert lines[2].endswith("· listed 2024-03-01 · not owned")
        assert lines[3].endswith("· owned")
        assert lines[4] == (
            "listing 5002 · VG+/VG+ · EUR 32.00 · ships from United States "
            "· listed 2024-03-01 · owned ?"
        )
        assert "--owned" in _continuation(text)


class TestNeedsAUser:
    @pytest.mark.parametrize("mode", ["none", "consumer"])
    @pytest.mark.parametrize("flag", ["--in-wantlist", "--owned"])
    def test_fails_before_any_request(
        self, monkeypatch: pytest.MonkeyPatch, mode: str, flag: str
    ) -> None:
        if mode == "consumer":
            monkeypatch.setenv("DISCOGS_CONSUMER_KEY", "key")
            monkeypatch.setenv("DISCOGS_CONSUMER_SECRET", "secret")
        h = _Harness(monkeypatch, _Seller(), token=False)
        result, sent = h.run("get", "inventory", "seller", flag, "--json")
        error = json_error(result)
        assert error["code"] == "auth_required"
        assert flag in error["message"]
        assert sent == []


class TestFlags:
    @pytest.mark.parametrize(
        "argv",
        [
            "get inventory seller other",
            "get inventory seller --sort audio",
            "get inventory seller --sort year",
            "get inventory seller --condition VG++",
            "get inventory seller --condition Generic",
            "get inventory seller --sleeve M-",
            "get inventory seller --max-price 20",
            "get inventory seller --currency EUR",
            "get inventory seller --max-price 20 --currency XYZ",
            "get inventory seller --max-price -1 --currency EUR",
            "get inventory seller --condition VG+ --page 2",
            "get inventory seller --after 2:3",
            "get inventory seller --max-requests 5",
            "get inventory seller --condition VG+ --max-requests 0",
            "get inventory seller --for-sale",
            "get inventory seller --role Main",
            "get inventory seller --limit 101",
            "get release @r1 --condition VG+",
            "get wantlist --in-wantlist",
            "get wantlist --for-sale --ships-from Germany",
        ],
    )
    def test_misplaced_flags_fail_before_any_request(
        self, monkeypatch: pytest.MonkeyPatch, argv: str
    ) -> None:
        h = _Harness(monkeypatch, _Seller())
        result, sent = h.run(*argv.split(), "--json")
        assert json_error(result)["code"] == "invalid_argument"
        assert sent == []

    @pytest.mark.parametrize("username", ["", " "])
    def test_a_blank_username_fails(
        self, monkeypatch: pytest.MonkeyPatch, username: str
    ) -> None:
        h = _Harness(monkeypatch, _Seller())
        result, sent = h.run("get", "inventory", username)
        assert "username" in text_error(result)
        assert sent == []

    @pytest.mark.parametrize("country", ["", " "])
    def test_a_blank_country_fails(
        self, monkeypatch: pytest.MonkeyPatch, country: str
    ) -> None:
        """A blank country would be dropped from the Next page command,
        which would then continue another listing, or none."""
        h = _Harness(monkeypatch, _Seller())
        result, sent = h.run("get", "inventory", "seller", "--ships-from", country)
        assert "--ships-from needs a country" in text_error(result)
        assert sent == []

    def test_messages_name_the_problem(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _Harness(monkeypatch, _Seller())
        two, _ = h.run("get", "inventory", "a", "b")
        assert text_error(two).startswith(
            "✗ get inventory takes exactly one username (2 given)."
        )
        grade, _ = h.run("get", "inventory", "a", "--condition", "mint-")
        assert text_error(grade).startswith(
            "✗ --condition must be a Discogs grade: M, NM, VG+, VG, G+, G, F or P, "
            "or its full name such as 'Very Good Plus (VG+)' (any case; 'mint-' "
            "given)."
        )

    def test_without_a_username_it_is_a_usage_error(self) -> None:
        result = CliRunner().invoke(cli, ["get", "inventory"])
        assert result.exit_code == 2
        assert "Missing argument 'REF...'" in result.stderr
