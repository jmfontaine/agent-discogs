"""Tests for the CLI against the real SDK client built by `get_client()`."""

from __future__ import annotations

import contextlib
import functools
import json
import os
import sqlite3
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx2
import pytest
from click.testing import CliRunner

import agent_discogs.client as client_module
from agent_discogs import cli
from tests.conftest import (
    RealSdk,
)

_ONE_PAGE = {"page": 1, "pages": 1, "items": 1}


_RELEASE_WITH_HEADINGS = {
    "id": 1,
    "title": "Sides",
    "artists": [{"id": 2, "name": "Band", "join": ""}],
    "tracklist": [
        {"position": "", "title": "Side A", "duration": "", "type_": "heading"},
        {"position": "A1", "title": "Opener", "duration": "3:00", "type_": "track"},
    ],
}


# Each auth mode's environment, as the SDK reads it (DISCOGS_TOKEN wins).
_CREDENTIALS: dict[str, dict[str, str]] = {
    "token": {"DISCOGS_TOKEN": "t"},
    "consumer": {"DISCOGS_CONSUMER_KEY": "k", "DISCOGS_CONSUMER_SECRET": "s"},
    "oauth": {
        "DISCOGS_CONSUMER_KEY": "k",
        "DISCOGS_CONSUMER_SECRET": "s",
        "DISCOGS_ACCESS_TOKEN": "a",
        "DISCOGS_ACCESS_TOKEN_SECRET": "b",
    },
    "none": {},
}


_SEE_STATUS = "agent-discogs status shows the credentials in use."


class TestRealSdk:
    """The CLI against a real `Discogs` client built by `get_client()`, with only
    its HTTP faked. The `SimpleNamespace` fakes elsewhere cannot notice an SDK
    upgrade that changes the events it emits or the models it returns."""

    @pytest.fixture(autouse=True)
    def _real_client(self, real_sdk: RealSdk) -> Iterator[None]:
        self.sdk = real_sdk
        self.sends, self.sleeps = real_sdk.sends, real_sdk.sleeps
        self.cache_home = real_sdk.cache_home
        real_sdk.respond = lambda _request: httpx2.Response(
            200,
            json=_RELEASE_WITH_HEADINGS,
            headers={
                "X-Discogs-Ratelimit": "60",
                "X-Discogs-Ratelimit-Used": "1",
                "X-Discogs-Ratelimit-Remaining": "59",
            },
        )
        yield
        assert self.sleeps == []  # the CLI never sleeps on the caller's behalf

    def test_repeat_lookup_is_served_from_cache(self) -> None:
        first = CliRunner().invoke(cli, ["tracks", "@r1"])
        assert first.exit_code == 0
        assert "A1. Opener (3:00)" in first.stdout
        assert first.stderr == "api: 1 request · 59/60 left this minute\n"

        repeat = CliRunner().invoke(cli, ["tracks", "@r1"])
        assert repeat.exit_code == 0
        assert repeat.stdout == first.stdout
        assert repeat.stderr == "api: 0 requests · cached\n"
        assert self.sends == ["/releases/1"]

    def test_headings_render_as_headings(self) -> None:
        result = CliRunner().invoke(cli, ["tracks", "@r1"])
        assert "  Side A\n  A1. Opener (3:00)\n" in result.stdout
        result = CliRunner().invoke(cli, ["tracks", "--json", "@r1"])
        assert json.loads(result.stdout)["tracklist"] == [
            {"title": "Side A", "type": "heading"},
            {"pos": "A1", "title": "Opener", "duration": "3:00"},
        ]

    def test_cached_miss_spends_nothing(self) -> None:
        result = CliRunner().invoke(cli, ["--debug", "--cached", "tracks", "@r1"])
        assert result.exit_code == 1
        lines = [s for s in result.stderr.splitlines() if not s.startswith("[sdk]")]
        assert lines[:3] == [
            "✗ Not cached: GET /releases/1",
            "  Rerun without --cached to fetch it from the API.",
            "api: 0 requests · cached",
        ]
        assert not [line for line in lines if line.startswith("GET ")]  # no row

        result = CliRunner().invoke(cli, ["--cached", "tracks", "--json", "@r1"])
        assert result.exit_code == 1
        assert json.loads(result.stdout)["error"]["code"] == "cache_miss"
        assert result.stderr == "api: 0 requests · cached\n"
        assert self.sends == []

    def _respond_in_turn(self, *responses: httpx2.Response) -> None:
        replies = iter(responses)
        self.sdk.respond = lambda _request: next(replies)

    def test_server_error_is_sent_once(self) -> None:
        self._respond_in_turn(
            httpx2.Response(503, text="unavailable"),
            httpx2.Response(200, json=_RELEASE_WITH_HEADINGS),
        )
        result = CliRunner().invoke(cli, ["tracks", "--json", "@r1"])
        assert result.exit_code == 1
        assert json.loads(result.stdout)["error"]["code"] == "api_error"
        assert result.stderr == "api: 1 request\n"
        assert self.sends == ["/releases/1"]
        assert self.sleeps == []

    def test_rate_limit_is_reported_without_sleeping(self) -> None:
        self._respond_in_turn(
            httpx2.Response(429, json={}, headers={"Retry-After": "60"}),
            httpx2.Response(200, json=_RELEASE_WITH_HEADINGS),
        )
        result = CliRunner().invoke(cli, ["tracks", "--json", "@r1"])
        assert result.exit_code == 1
        error = json.loads(result.stdout)["error"]
        assert (error["code"], error["retry_after"]) == ("rate_limited", 60)
        assert self.sends == ["/releases/1"]
        assert self.sleeps == []

    def _use_credentials(self, mode: str, monkeypatch: pytest.MonkeyPatch) -> None:
        """Leave only `mode`'s credentials in the environment. No client exists
        yet, so the one `get_client()` builds selects that mode."""
        monkeypatch.delenv("DISCOGS_TOKEN")
        for name, value in _CREDENTIALS[mode].items():
            monkeypatch.setenv(name, value)

    def _assert_error(self, mode: str, expected: dict[str, object]) -> None:
        """The same error in the JSON envelope and in the text output, from a
        request sent by a client using `mode`."""
        result = CliRunner().invoke(cli, ["tracks", "--json", "@r1"])
        assert result.exit_code == 1
        assert self.sends == ["/releases/1"]
        assert client_module._client is not None
        assert client_module._client.auth_mode == mode
        assert json.loads(result.stdout) == {"error": expected}
        result = CliRunner().invoke(cli, ["tracks", "@r1"])
        assert result.exit_code == 1
        assert result.stderr.startswith(
            f"✗ {expected['message']}\n  {expected['hint']}\n"
        )

    @pytest.mark.parametrize(
        ("mode", "message", "hint"),
        [
            pytest.param(
                "token",
                "Authentication failed with DISCOGS_TOKEN.",
                "Check the token, or generate a new one at "
                f"discogs.com/settings/developers. {_SEE_STATUS}",
                id="token",
            ),
            pytest.param(
                "consumer",
                "Authentication failed with the consumer key/secret "
                "(DISCOGS_CONSUMER_KEY, DISCOGS_CONSUMER_SECRET).",
                "Check both at discogs.com/settings/developers. They identify an "
                "app, not a user: price suggestions need DISCOGS_TOKEN or OAuth. "
                f"{_SEE_STATUS}",
                id="consumer",
            ),
            pytest.param(
                "oauth",
                "Authentication failed with OAuth (DISCOGS_CONSUMER_KEY, "
                "DISCOGS_CONSUMER_SECRET, DISCOGS_ACCESS_TOKEN, "
                "DISCOGS_ACCESS_TOKEN_SECRET).",
                "Check all four; the access token may have been revoked. "
                f"{_SEE_STATUS}",
                id="oauth",
            ),
            pytest.param(
                "none",
                "Authentication failed: no credentials are set.",
                "Set token: export DISCOGS_TOKEN=<token> "
                f"(discogs.com/settings/developers). {_SEE_STATUS}",
                id="none",
            ),
        ],
    )
    def test_401_names_the_credentials_in_use(
        self, mode: str, message: str, hint: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._use_credentials(mode, monkeypatch)
        self.sdk.respond = lambda _request: httpx2.Response(
            401, json={"message": "You must authenticate to access this resource."}
        )
        self._assert_error(
            mode,
            {"code": "auth_required", "message": message, "hint": hint, "status": 401},
        )

    @pytest.mark.parametrize(
        ("mode", "limit", "hint"),
        [
            pytest.param(
                "token",
                60,
                "Retry in 30s. Discogs reported 60 req/min for the credentials "
                "in use (token).",
                id="token",
            ),
            pytest.param(
                "consumer",
                60,
                "Retry in 30s. Discogs reported 60 req/min for the credentials "
                "in use (consumer).",
                id="consumer",
            ),
            pytest.param(
                "oauth",
                60,
                "Retry in 30s. Discogs reported 60 req/min for the credentials "
                "in use (oauth).",
                id="oauth",
            ),
            pytest.param(
                "token",
                25,
                "Retry in 30s. Discogs reported 25 req/min for the credentials "
                "in use (token).",
                id="token-counted-as-anonymous",
            ),
            pytest.param(
                "none",
                25,
                "Retry in 30s. 60 req/min with DISCOGS_TOKEN, 25 without.",
                id="none",
            ),
        ],
    )
    def test_429_hint_follows_the_credentials_in_use(
        self, mode: str, limit: int, hint: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._use_credentials(mode, monkeypatch)
        self.sdk.respond = lambda _request: httpx2.Response(
            429,
            json={"message": "You are making requests too quickly."},
            headers={
                "Retry-After": "30",
                "X-Discogs-Ratelimit": str(limit),
                "X-Discogs-Ratelimit-Used": str(limit),
                "X-Discogs-Ratelimit-Remaining": "0",
            },
        )
        self._assert_error(
            mode,
            {
                "code": "rate_limited",
                "message": f"Rate limited: 0/{limit} requests left this minute.",
                "hint": hint,
                "retry_after": 30,
                "status": 429,
                "limit": limit,
                "remaining": 0,
            },
        )

    @pytest.mark.parametrize(
        ("mode", "hint"),
        [
            pytest.param(
                "consumer",
                "Retry in 30s. Discogs allows 60 req/min for the credentials in "
                "use (consumer).",
                id="consumer",
            ),
            pytest.param(
                "none",
                "Retry in 30s. 60 req/min with DISCOGS_TOKEN, 25 without.",
                id="none",
            ),
        ],
    )
    def test_429_without_rate_limit_headers(
        self, mode: str, hint: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._use_credentials(mode, monkeypatch)
        self.sdk.respond = lambda _request: httpx2.Response(
            429, json={}, headers={"Retry-After": "30"}
        )
        self._assert_error(
            mode,
            {
                "code": "rate_limited",
                "message": "Rate limit exceeded.",
                "hint": hint,
                "retry_after": 30,
                "status": 429,
            },
        )

    def test_connection_failure_is_one_counted_send(self) -> None:
        def _refuse(request: httpx2.Request) -> httpx2.Response:
            raise httpx2.ConnectError("Connection refused", request=request)

        self.sdk.respond = _refuse
        result = CliRunner().invoke(cli, ["tracks", "@r1"])
        assert result.exit_code == 1
        assert result.stderr.splitlines() == [
            "✗ Connection error: Connection refused",
            "  Check your network and retry.",
            "api: 1 request",
        ]
        assert self.sends == ["/releases/1"]
        assert self.sleeps == []

    def _full_release(self, **fields: object) -> dict[str, Any]:
        release = {**_RELEASE_WITH_HEADINGS, **fields}
        self.sdk.respond = lambda _request: httpx2.Response(200, json=release)
        result = CliRunner().invoke(cli, ["get", "release", "--json", "--full", "@r1"])
        assert result.exit_code == 0, result.output
        return json.loads(result.stdout)

    def test_full_json_timestamps_are_rfc3339(self) -> None:
        full = self._full_release(
            date_added="2005-01-03T23:17:26-08:00", date_changed="2005-01-03T23:17:26"
        )
        assert full["date_added"] == "2005-01-03T23:17:26-08:00"
        changed = full["date_changed"]  # sent without an offset
        assert changed.startswith("2005-01-03T23:17:26")
        assert datetime.fromisoformat(changed).utcoffset() is not None

    def test_full_json_unparseable_timestamp_is_null(self) -> None:
        full = self._full_release(date_added="not-a-timestamp")
        assert "date_added" in full
        assert full["date_added"] is None

    def test_full_json_uses_the_sdk_field_names(self) -> None:
        credit = {"id": 3, "name": "Flood", "anv": "F.", "role": "Producer"}
        track = {"position": "A1", "title": "Opener", "extraartists": [credit]}
        full = self._full_release(
            labels=[{"id": 4, "name": "Nothing", "catno": "INT-1"}],
            extraartists=[credit],
            tracklist=[track],
            unmodeled_field={"kept": [1, "two"]},
        )
        assert full["labels"][0]["catalog_number"] == "INT-1"
        assert "catno" not in full["labels"][0]
        assert full["extra_artists"][0]["name_variation"] == "F."
        assert "extraartists" not in full
        assert full["tracklist"][0]["extra_artists"][0]["role"] == "Producer"
        assert full["unmodeled_field"] == {"kept": [1, "two"]}

    def test_full_json_tracks_carry_type(self) -> None:
        for argv in (
            ["tracks", "--json", "--full", "@r1"],
            ["get", "release", "--json", "--full", "@r1"],
        ):
            result = CliRunner().invoke(cli, argv)
            tracks = json.loads(result.stdout)["tracklist"]
            assert [t["type"] for t in tracks] == ["heading", "track"]
            assert not [t for t in tracks if "type_" in t]

    def test_full_json_search_rows_are_normalized(self) -> None:
        self.sdk.respond = lambda _request: httpx2.Response(
            200,
            json={
                "pagination": {"page": 1, "pages": 1, "items": 1},
                "results": [
                    {
                        "id": 1,
                        "type": "release",
                        "title": "Sides",
                        "catno": "INT-1",
                        "uri": "/release/1",
                    }
                ],
            },
        )
        result = CliRunner().invoke(
            cli, ["search", "--json", "--full", "--release-type", "all", "Sides"]
        )
        assert result.exit_code == 0, result.output
        row = json.loads(result.stdout)["results"][0]
        assert row["catalog_number"] == "INT-1"
        assert row["uri"] == "https://www.discogs.com/release/1"

    @pytest.mark.parametrize(
        ("argv", "body"),
        [
            (
                ["get", "release", "@r38606880"],
                {
                    "id": 38606880,
                    "title": "Slate",
                    "uri": "https://www.discogs.com/release/38606880-Nine-Inch-Nails-Slate",
                },
            ),
            (
                ["get", "master", "@m3719"],
                {
                    "id": 3719,
                    "title": "The Downward Spiral",
                    "uri": "https://www.discogs.com/master/3719-Nine-Inch-Nails-The-Downward-Spiral",
                },
            ),
            (
                ["get", "artist", "@a3857"],
                {
                    "id": 3857,
                    "name": "Nine Inch Nails",
                    "uri": "https://www.discogs.com/artist/3857-Nine-Inch-Nails",
                },
            ),
            (
                ["get", "label", "@l647"],
                {
                    "id": 647,
                    "name": "Nothing Records",
                    "uri": "https://www.discogs.com/label/647-Nothing-Records",
                },
            ),
        ],
    )
    def test_json_links_each_entity_to_its_discogs_page(
        self, argv: list[str], body: dict[str, object]
    ) -> None:
        bare = {k: v for k, v in body.items() if k != "uri"}
        outputs = {}
        for name, sent in (("linked", body), ("bare", bare)):
            self.sdk.respond = lambda _request, sent=sent: httpx2.Response(
                200, json=sent
            )
            text = CliRunner().invoke(cli, ["--fresh", *argv])
            doc = CliRunner().invoke(cli, ["--fresh", *argv[:2], "--json", argv[2]])
            assert text.exit_code == doc.exit_code == 0, text.output + doc.output
            outputs[name] = (text.stdout, json.loads(doc.stdout))
        assert outputs["linked"][1]["url"] == body["uri"]
        assert "url" not in outputs["bare"][1]
        # The link is JSON-only: the text view ignores it, byte for byte.
        assert outputs["linked"][0] == outputs["bare"][0]
        assert "discogs.com" not in outputs["linked"][0]

    @pytest.mark.parametrize(
        ("argv", "path", "key", "fields"),
        [
            (["get", "versions", "@m3719"], "/masters/3719/versions", "versions", {}),
            (
                ["get", "releases", "@a3857"],
                "/artists/3857/releases",
                "releases",
                {"type": "release"},
            ),
            (["get", "releases", "@l647"], "/labels/647/releases", "releases", {}),
        ],
    )
    def test_json_list_rows_carry_a_link_when_discogs_sends_one(
        self, argv: list[str], path: str, key: str, fields: dict[str, object]
    ) -> None:
        # No such row has `uri` today; the SDK models would keep one as an
        # unmodeled field, so `url` must follow it if Discogs ever sends it.
        rows = [
            {
                **fields,
                "id": 10,
                "title": "Absolute",
                "uri": "https://www.discogs.com/release/10-A",
            },
            {**fields, "id": 11, "title": "Relative", "uri": "/release/11-R"},
            {**fields, "id": 12, "title": "Bare"},
        ]
        pagination = {"page": 1, "pages": 1, "per_page": 5, "items": 3}
        entity = {"id": 1, "title": "Entity", "name": "Entity"}  # text headers
        outputs = {}
        for name, sent in (
            ("linked", rows),
            ("bare", [{k: v for k, v in r.items() if k != "uri"} for r in rows]),
        ):
            listing = {"pagination": pagination, key: sent}
            self.sdk.respond = lambda request, listing=listing: httpx2.Response(
                200, json=listing if request.url.path == path else entity
            )
            text = CliRunner().invoke(cli, ["--fresh", *argv])
            doc = CliRunner().invoke(cli, ["--fresh", *argv[:2], "--json", argv[2]])
            assert text.exit_code == doc.exit_code == 0, text.output + doc.output
            outputs[name] = (text.stdout, json.loads(doc.stdout)["results"])
        assert [row.get("url") for row in outputs["linked"][1]] == [
            "https://www.discogs.com/release/10-A",
            "https://www.discogs.com/release/11-R",
            None,
        ]
        assert not [row for row in outputs["bare"][1] if "url" in row]
        assert outputs["linked"][0] == outputs["bare"][0]
        assert "discogs.com" not in outputs["linked"][0]

    def test_json_search_links_are_absolute(self) -> None:
        self.sdk.respond = lambda _request: httpx2.Response(
            200,
            json={
                "pagination": {"page": 1, "pages": 1, "items": 3},
                "results": [
                    {"id": 1, "type": "release", "title": "Sides", "uri": "/release/1"},
                    {"id": 2, "type": "playlist", "title": "Mix", "uri": "/lists/2"},
                    {"id": 3, "type": "artist", "title": "Band"},
                ],
            },
        )
        argv = ["search", "--release-type", "all", "Sides"]
        result = CliRunner().invoke(cli, [*argv[:1], "--json", *argv[1:]])
        assert result.exit_code == 0, result.output
        rows = json.loads(result.stdout)["results"]
        assert [row.get("url") for row in rows] == [
            "https://www.discogs.com/release/1",
            "https://www.discogs.com/lists/2",
            None,
        ]
        text = CliRunner().invoke(cli, argv)
        assert text.exit_code == 0, text.output
        assert "discogs.com" not in text.stdout

    @pytest.mark.parametrize(
        ("argv", "body", "detail"),
        [
            (["get", "release", "@r1"], None, "Expecting value"),
            (["search", "--release-type", "all", "x"], None, "Expecting value"),
            (
                ["get", "release", "@r1"],
                {"id": "one", "title": "T"},
                "Release.id: Input should be a valid integer",
            ),
            (
                ["search", "--release-type", "all", "x"],
                {"pagination": _ONE_PAGE, "results": [{"id": "one", "title": "T"}]},
                "SearchResult.id: Input should be a valid integer",
            ),
            (
                ["search", "--release-type", "all", "x"],
                {"pagination": _ONE_PAGE, "results": {}},
                '"results" is an object, expected an array',
            ),
            (
                ["search", "x"],  # a filtered scan
                {"pagination": _ONE_PAGE, "results": {}},
                '"results" is an object, expected an array',
            ),
            (
                ["get", "releases", "@l1"],
                {"pagination": _ONE_PAGE, "releases": {}},
                '"releases" is an object, expected an array',
            ),
            (
                ["search", "--release-type", "all", "x"],
                {"pagination": [], "results": []},
                '"pagination" is an array, expected an object',
            ),
        ],
        ids=[
            "html-entity",
            "html-page",
            "invalid-entity",
            "invalid-row",
            "results-object",
            "results-object-scan",
            "releases-object",
            "pagination-list",
        ],
    )
    def test_unreadable_response_is_a_bad_response(
        self, argv: list[str], body: object, detail: str
    ) -> None:
        """A 200 the CLI cannot read is Discogs' fault, not the caller's: it
        fails with `bad_response` instead of `invalid_argument` or an empty page.
        A text catalogue's label header, read first, gets a valid answer."""
        label = httpx2.Response(200, json={"id": 1, "name": "Label"})
        self.sdk.respond = lambda request: (
            label
            if request.url.path == "/labels/1"
            else httpx2.Response(200, text="<html>Bad gateway</html>")
            if body is None
            else httpx2.Response(200, json=body)
        )
        result = CliRunner().invoke(cli, [*argv, "--json"])
        assert result.exit_code == 1, result.output
        error = json.loads(result.stdout)["error"]
        assert error["code"] == "bad_response"
        assert detail in error["message"]

        text = CliRunner().invoke(cli, argv)
        assert text.exit_code == 1, text.output
        assert text.stdout == ""
        first = text.stderr.splitlines()[0]
        assert first.startswith("✗ Discogs sent a response that cannot be read: ")
        assert detail in first
        # Nothing was cached: the retry asked Discogs again.
        listing = [path for path in self.sends if path != "/labels/1"]
        assert listing == [self.sends[0]] * 2

    def test_bad_price_guide_recovers_with_fresh(self) -> None:
        """A price guide's conditions are validated only when read, after the
        SDK has cached the body, so the hint's `--fresh` is what gets past it."""
        conditions: dict[str, object] = {"currency": "USD", "value": "n/a"}

        def _respond(request: httpx2.Request) -> httpx2.Response:
            path = request.url.path
            if path.startswith("/marketplace/price_suggestions/"):
                return httpx2.Response(200, json={"Mint (M)": conditions})
            if path.startswith("/marketplace/stats/"):
                return httpx2.Response(200, json={"num_for_sale": 1})
            return httpx2.Response(200, json=_RELEASE_WITH_HEADINGS)

        self.sdk.respond = _respond
        bad = CliRunner().invoke(cli, ["price", "--json", "@r1"])
        assert bad.exit_code == 1, bad.output
        error = json.loads(bad.stdout)["error"]
        assert error["code"] == "bad_response"
        assert "--fresh" in error["hint"]

        conditions["value"] = 9.5
        retry = CliRunner().invoke(cli, ["price", "--json", "@r1"])
        assert json.loads(retry.stdout)["error"]["code"] == "bad_response"
        assert self.sends.count("/marketplace/price_suggestions/1") == 1  # cached

        fresh = CliRunner().invoke(cli, ["--fresh", "price", "--json", "@r1"])
        assert fresh.exit_code == 0, fresh.output
        assert "9.5" in fresh.stdout

    def test_page_without_pagination_is_one_page(self) -> None:
        """An absent `pagination` key is intentional: one page of what was sent."""
        row = {"id": 1, "type": "release", "title": "Sides"}
        self.sdk.respond = lambda _request: httpx2.Response(
            200, json={"results": [row]}
        )
        result = CliRunner().invoke(
            cli, ["search", "--json", "--release-type", "all", "x"]
        )
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout) == {
            "pagination": {"page": 1, "total_items": 1, "total_pages": 1},
            "results": [{"ref": "@r1", "type": "release", "title": "Sides"}],
        }

    def _seed_unreadable_release(self, caplog: pytest.LogCaptureFixture) -> None:
        """Cache a /releases/1 body the Release model rejects, as an older SDK
        or another process sharing the cache file could have stored it. The
        SDK never stores such a body itself, so this goes around its parsing."""
        self.sdk.respond = lambda _request: httpx2.Response(
            200, json={"id": "one", "title": "T"}
        )
        client = client_module.get_client()
        client._send("GET", client._build_url("/releases/1"), parse=lambda d: d)
        self.sdk.respond = lambda _request: httpx2.Response(
            200, json=_RELEASE_WITH_HEADINGS
        )
        self.sends.clear()
        caplog.clear()

    def test_unreadable_cache_entry_is_fetched_again(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        self._seed_unreadable_release(caplog)
        result = CliRunner().invoke(cli, ["get", "release", "--json", "@r1"])
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["title"] == "Sides"
        assert "no longer parses" in caplog.text  # the seeded entry was found
        assert self.sends == ["/releases/1"]

    def test_unreadable_cache_entry_is_a_miss_under_cached(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        self._seed_unreadable_release(caplog)
        result = CliRunner().invoke(
            cli, ["--cached", "get", "release", "--json", "@r1"]
        )
        assert result.exit_code == 1, result.output
        assert json.loads(result.stdout)["error"]["code"] == "cache_miss"
        assert "no longer parses" in caplog.text  # the seeded entry was found
        assert self.sends == []

    def _database(self) -> Path:
        return self.cache_home / "agent-discogs" / "cache.db"

    def _rows(self) -> int:
        with contextlib.closing(sqlite3.connect(self._database())) as db:
            return db.execute("SELECT COUNT(*) FROM cache_entries").fetchone()[0]

    def _assert_purge_failed(self, args: list[str]) -> None:
        """`cache clear` failed with `cache_error`, the same in text and JSON."""
        database = self._database()
        result = CliRunner().invoke(cli, [*args, "--json"])
        assert result.exit_code == 1
        # stderr may hold the SDK's own warnings, but never the error itself.
        assert "✗" not in result.stderr
        error = json.loads(result.stdout)["error"]
        assert error["code"] == "cache_error"
        assert error["path"] == str(database)
        assert error["message"].startswith(
            f"Cache error at {database}: could not clear the cache: sqlite3."
        )
        assert f"delete {database}" in error["hint"]
        assert "--fresh" not in error["hint"]  # it does not help a purge

        result = CliRunner().invoke(cli, args)
        assert result.exit_code == 1
        assert result.stdout == ""
        assert result.stderr.endswith(f"✗ {error['message']}\n  {error['hint']}\n")

    def test_cache_clear_purges_a_healthy_cache(self) -> None:
        assert CliRunner().invoke(cli, ["tracks", "@r1"]).exit_code == 0
        assert self._rows() == 1
        result = CliRunner().invoke(cli, ["cache", "clear"])
        assert result.exit_code == 0
        assert result.stdout == "Cache cleared.\n"
        assert self._rows() == 0

        result = CliRunner().invoke(cli, ["cache", "clear", "--json"])
        assert result.exit_code == 0
        assert json.loads(result.stdout) == {"cleared": True}

    def test_cache_clear_fails_on_a_corrupt_database(self) -> None:
        database = self._database()
        database.parent.mkdir(mode=0o700)
        garbage = b"not a sqlite database " * 8
        database.write_bytes(garbage)
        database.chmod(0o600)
        self._assert_purge_failed(["cache", "clear"])
        assert database.read_bytes() == garbage  # untouched, not unlinked

    def test_cache_clear_fails_on_a_locked_database(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # No busy wait: SQLite's default 5s timeout would only slow the test.
        monkeypatch.setattr(
            sqlite3, "connect", functools.partial(sqlite3.connect, timeout=0)
        )
        assert CliRunner().invoke(cli, ["tracks", "@r1"]).exit_code == 0
        with contextlib.closing(sqlite3.connect(self._database())) as holder:
            holder.execute("BEGIN EXCLUSIVE")
            self._assert_purge_failed(["cache", "clear"])
            holder.rollback()
        assert self._rows() == 1  # the entry is still there

    def _break_cache(self, kind: str, monkeypatch: pytest.MonkeyPatch) -> Path:
        """Make the cache unusable one way; return the database path it names."""
        if kind == "uncreatable dir":
            not_a_dir = self.cache_home / "file"
            not_a_dir.write_text("")
            monkeypatch.setenv("XDG_CACHE_HOME", str(not_a_dir))
            return not_a_dir / "agent-discogs" / "cache.db"
        directory = self.cache_home / "agent-discogs"
        directory.mkdir(mode=0o700)
        database = directory / "cache.db"
        if kind == "symlinked database":
            target = self.cache_home / "elsewhere.db"
            target.write_bytes(b"")
            database.symlink_to(target)
        else:  # unopenable database: neither readable nor writable
            database.write_bytes(b"")
            database.chmod(0)
        return database

    _BROKEN_CACHES = (
        "uncreatable dir",
        "symlinked database",
        pytest.param(
            "unopenable database",
            marks=pytest.mark.skipif(
                os.geteuid() == 0, reason="root opens a mode-0 file"
            ),
        ),
    )

    @pytest.mark.parametrize("kind", _BROKEN_CACHES)
    @pytest.mark.parametrize("flags", [[], ["--cached"]], ids=["no flag", "cached"])
    def test_unusable_cache_is_a_cache_error(
        self, kind: str, flags: list[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        database = self._break_cache(kind, monkeypatch)
        result = CliRunner().invoke(cli, [*flags, "tracks", "--json", "@r1"])
        assert result.exit_code == 1
        assert result.stderr == ""
        error = json.loads(result.stdout)["error"]
        assert error["code"] == "cache_error"
        assert error["path"] == str(database)
        assert error["message"].startswith(f"Cache error at {database}: could not ")
        assert str(database.parent) in error["hint"]
        # Delete-by-hand advice only where there is a file to delete.
        assert (f"delete {database}" in error["hint"]) == (kind != "uncreatable dir")

        result = CliRunner().invoke(cli, [*flags, "tracks", "@r1"])
        assert result.exit_code == 1
        assert result.stdout == ""
        assert result.stderr == f"✗ {error['message']}\n  {error['hint']}\n"
        assert self.sends == []
        # Nothing deleted the database the error names.
        assert os.path.lexists(database) == (kind != "uncreatable dir")

    @pytest.mark.parametrize("kind", _BROKEN_CACHES)
    def test_fresh_skips_an_unusable_cache(
        self, kind: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._break_cache(kind, monkeypatch)
        result = CliRunner().invoke(cli, ["--fresh", "tracks", "@r1"])
        assert result.exit_code == 0, result.output
        assert "A1. Opener (3:00)" in result.stdout
        assert self.sends == ["/releases/1"]

    def test_multi_ref_json_reports_a_cache_error_per_ref(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._break_cache("uncreatable dir", monkeypatch)
        result = CliRunner().invoke(cli, ["get", "release", "--json", "@r1", "@r2"])
        assert result.exit_code == 1
        items = json.loads(result.stdout)
        assert [i["ref"] for i in items] == ["@r1", "@r2"]
        assert {i["error"]["code"] for i in items} == {"cache_error"}
        assert self.sends == []

    @pytest.mark.parametrize("kind", _BROKEN_CACHES)
    def test_cache_clear_on_an_unusable_cache_fails(
        self, kind: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        database = self._break_cache(kind, monkeypatch)
        result = CliRunner().invoke(cli, ["cache", "clear"])
        assert result.exit_code == 1
        assert result.stdout == ""
        assert result.stderr.startswith(f"✗ Cache error at {database}: could not ")
        assert str(database.parent) in result.stderr
        result = CliRunner().invoke(cli, ["cache", "clear", "--json"])
        assert result.exit_code == 1
        assert json.loads(result.stdout)["error"]["code"] == "cache_error"
        assert os.path.lexists(database) == (kind != "uncreatable dir")
