"""Tests for `--json` output of `search`, `get` and the shortcuts."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner
from discogs_sdk import (
    Artist,
    MarketplaceReleaseStats,
    Master,
    NotFoundError,
    PriceSuggestions,
    RateLimitError,
    Release,
    SearchResult,
)

from agent_discogs import cli
from agent_discogs.pagination import PageResult
from agent_discogs.projections import project_release
from tests.conftest import (
    ServePage,
    UseClient,
    fake,
    fake_client,
    fake_model,
    json_error,
    listed,
    text_error,
)


def _via_json(data: object) -> object:
    """What a `--full` dump of a fake looks like after `default=str`."""
    return json.loads(json.dumps(data, default=str))


class TestJsonSearch:
    @pytest.fixture(autouse=True)
    def _patch_search(
        self,
        monkeypatch: pytest.MonkeyPatch,
        use_client: UseClient,
        serve_page: ServePage,
    ) -> None:
        self._monkeypatch = monkeypatch
        use_client(fake_client())
        self._serve_page = serve_page

    def test_search_json(self) -> None:
        item = SearchResult.model_validate(
            {
                "id": 847868,
                "type": "release",
                "title": "Nine Inch Nails - The Downward Spiral",
                "year": "1994",
                "country": "US",
                "label": ["Nothing Records"],
                "catno": "92346-2",
                "format": ["CD", "Album"],
                "master_id": 3719,
                "community": {"have": 7544, "want": 900},
                "thumb": "https://img.discogs.com/…",
            }
        )
        self._serve_page(PageResult(items=[item], page=1, total_items=1, total_pages=1))
        result = CliRunner().invoke(cli, ["search", "--json", "test"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["pagination"] == {
            "page": 1,
            "total_items": 1,
            "total_pages": 1,
            "filtered": True,
            "capped": False,
            "next_cursor": None,  # raw rows exhausted
        }
        assert data["results"] == [
            {
                "ref": "@r847868",
                "type": "release",
                "title": "Nine Inch Nails - The Downward Spiral",
                "year": "1994",
                "country": "US",
                "label": "Nothing Records",
                "catno": "92346-2",
                "format": ["CD", "Album"],
                "have": 7544,
                "master": "@m3719",
            }
        ]
        assert result.stdout.count("\n") == 1  # compact, one document

        full = CliRunner().invoke(cli, ["search", "--json", "--full", "test"])
        assert json.loads(full.stdout)["results"] == [item.model_dump(mode="json")]

    def test_full_requires_json(self) -> None:
        result = CliRunner().invoke(cli, ["search", "--full", "test"])
        assert text_error(result) == "✗ --full requires --json.\n"

    def test_json_error_envelope_for_flag_errors(self) -> None:
        result = CliRunner().invoke(cli, ["search", "--json", "test", "--page", "2"])
        error = json_error(result)
        assert error["code"] == "invalid_argument"
        assert error["message"].startswith("--page is not available")

    def test_json_error_envelope_for_api_errors(self) -> None:
        def _raise(*_a: object, **_kw: object) -> None:
            raise RateLimitError(
                "slow",
                status_code=429,
                response_body={},
                retry_after="12",
                retry_after_seconds=12.0,
            )

        self._monkeypatch.setattr("agent_discogs.pagination.fetch_page", _raise)
        result = CliRunner().invoke(cli, ["search", "--json", "test"])
        assert json_error(result) == {
            "code": "rate_limited",
            "message": "Rate limit exceeded.",
            "hint": "Retry in 12s. 60 req/min with DISCOGS_TOKEN, 25 without.",
            "retry_after": 12,
            "status": 429,
        }

        text = CliRunner().invoke(cli, ["search", "test"])
        assert text_error(text) == (
            "✗ Rate limit exceeded.\n"
            "  Retry in 12s. 60 req/min with DISCOGS_TOKEN, 25 without.\n"
        )

    def test_search_json_filtered_cursor(self) -> None:
        """Agents consume the cursor from JSON: present when more rows remain,
        absent once a scan has read every page."""
        item = SearchResult(id=1, type="release", title="X")
        bootleg = SearchResult(
            id=2, type="release", title="B", format=["Unofficial Release"]
        )

        self._serve_page(
            PageResult(items=[item] * 15, page=1, total_items=30, total_pages=2)
        )
        result = CliRunner().invoke(cli, ["search", "--json", "t"])
        data = json.loads(result.stdout)
        assert data["pagination"]["filtered"] is True
        assert data["pagination"]["capped"] is False
        assert data["pagination"]["next_cursor"] == "2:5"

        self._serve_page(
            PageResult(items=[bootleg], page=1, total_items=500, total_pages=34)
        )
        result = CliRunner().invoke(cli, ["search", "--json", "t"])
        data = json.loads(result.stdout)
        assert data["results"] == []
        assert data["pagination"]["capped"] is False
        assert data["pagination"]["next_cursor"] is None

    def test_search_json_server_side_has_no_cursor_keys(self) -> None:
        item = SearchResult(id=1, type="artist", title="X")
        self._serve_page(PageResult(items=[item], page=1, total_items=9, total_pages=2))
        result = CliRunner().invoke(cli, ["search", "--json", "artist", "x"])
        data = json.loads(result.stdout)
        assert data["pagination"] == {"page": 1, "total_items": 9, "total_pages": 2}


class TestJsonGet:
    @pytest.fixture(autouse=True)
    def _patch_get(
        self,
        monkeypatch: pytest.MonkeyPatch,
        use_client: UseClient,
        serve_page: ServePage,
    ) -> None:
        self._monkeypatch = monkeypatch
        self._set_client = use_client
        self._serve_page = serve_page

    def test_get_release_json_projection(self) -> None:
        release = fake_model(
            id=847868,
            title="The Downward Spiral",
            year=1994,
            released="1994-03-08",
            country="US",
            artists=[fake(id=3857, name="Nine Inch Nails", join=None)],
            labels=[fake(id=647, name="Nothing Records", catalog_number="92346-2")],
            formats=[fake(name="CD", descriptions=["Album"])],
            genres=["Electronic", "Folk, World, & Country"],
            styles=None,
            master_id=3719,
            community=fake(have=7545, want=1342, rating=fake(average=4.42, count=593)),
            num_for_sale=49,
            lowest_price=2.45,
            notes="  Slipcase. ",
            tracklist=[
                fake(
                    position="1",
                    title="Mr. Self Destruct",
                    duration="4:30",
                    type=None,
                    artists=None,
                )
            ],
            extra_artists=[
                fake(id=20661, name="Flood", role="Producer", tracks="1, 2")
            ],
            identifiers=[fake(type="Barcode", value="765449234620", description=None)],
            images=[{"uri": "https://img…"}],
            videos=[{"uri": "https://youtube…"}],
        )
        self._set_client(fake_client(releases_get=lambda _id: release))
        result = CliRunner().invoke(cli, ["get", "--json", "release", "@r847868"])
        assert result.exit_code == 0
        assert json.loads(result.stdout) == {
            "ref": "@r847868",
            "title": "The Downward Spiral",
            "artists": [{"ref": "@a3857", "name": "Nine Inch Nails"}],
            "year": 1994,
            "released": "1994-03-08",
            "country": "US",
            "labels": [{"ref": "@l647", "name": "Nothing Records", "catno": "92346-2"}],
            "formats": "CD (Album)",
            "genres": ["Electronic", "Folk, World, & Country"],
            "master": "@m3719",
            "community": {"have": 7545, "want": 1342, "rating": 4.42, "votes": 593},
            "market": {"for_sale": 49, "lowest": 2.45},
            # no "notes": text hides them without -v, and so does the projection
            "tracklist": [
                {"pos": "1", "title": "Mr. Self Destruct", "duration": "4:30"}
            ],
        }

        verbose = json.loads(
            CliRunner()
            .invoke(cli, ["get", "--json", "-v", "release", "@r847868"])
            .stdout
        )
        assert verbose["notes"] == "Slipcase."
        assert verbose["credits"] == {
            "Producer": [{"ref": "@a20661", "name": "Flood", "tracks": "1, 2"}]
        }
        assert verbose["identifiers"] == [{"type": "Barcode", "value": "765449234620"}]

        full = json.loads(
            CliRunner()
            .invoke(cli, ["get", "--json", "--full", "release", "@r847868"])
            .stdout
        )
        assert full["id"] == 847868
        assert full["images"] == [{"uri": "https://img…"}]

    def test_get_artist_json_projection(self) -> None:
        artist = fake_model(
            id=3857,
            name="Nine Inch Nails",
            profile="Industrial rock project.",
            urls=["https://www.nin.com/", "https://twitter.com/nin"],
            members=[
                fake(id=27457, name="Trent Reznor", active=True),
                fake(id=4237, name="Chris Vrenna", active=False),
            ],
            images=[{"uri": "x"}],
        )
        self._set_client(fake_client(artists_get=lambda _id: artist))
        data = json.loads(
            CliRunner().invoke(cli, ["get", "--json", "artist", "@a3857"]).stdout
        )
        assert data == {
            "ref": "@a3857",
            "name": "Nine Inch Nails",
            "profile": "Industrial rock project.",
            "urls": "nin.com, twitter.com",
            "members": [{"ref": "@a27457", "name": "Trent Reznor"}],
            "former": [{"ref": "@a4237", "name": "Chris Vrenna"}],
        }

    def test_get_artist_unknown_member_status_is_not_former(self) -> None:
        """A real Member with no `active` value (None) is not a former member."""
        artist = Artist.model_validate(
            {
                "id": 3857,
                "name": "Nine Inch Nails",
                "members": [
                    {"id": 27457, "name": "Trent Reznor", "active": True},
                    {"id": 4237, "name": "Chris Vrenna", "active": False},
                    {"id": 5555, "name": "Unknown Status"},
                ],
            }
        )
        assert artist.members is not None
        assert artist.members[2].active is None
        self._set_client(fake_client(artists_get=lambda _id: artist))

        text = CliRunner().invoke(cli, ["get", "artist", "@a3857"]).stdout
        assert (
            "Members: Trent Reznor [@a27457], Unknown Status [@a5555]\n"
            "Former: Chris Vrenna [@a4237]\n"
        ) in text

        data = json.loads(
            CliRunner().invoke(cli, ["get", "--json", "artist", "@a3857"]).stdout
        )
        assert data["members"] == [
            {"ref": "@a27457", "name": "Trent Reznor"},
            {"ref": "@a5555", "name": "Unknown Status"},
        ]
        assert data["former"] == [{"ref": "@a4237", "name": "Chris Vrenna"}]

    def test_get_label_json_projection(self) -> None:
        label = fake_model(
            id=647,
            name="Nothing Records",
            profile=None,
            urls=None,
            parent_label=fake(id=2311, name="Interscope Records"),
            sub_labels=[fake(id=561260, name="NIN")],
        )
        self._set_client(fake_client(labels_get=lambda _id: label))
        data = json.loads(
            CliRunner().invoke(cli, ["get", "--json", "label", "@l647"]).stdout
        )
        assert data == {
            "ref": "@l647",
            "name": "Nothing Records",
            "parent": {"ref": "@l2311", "name": "Interscope Records"},
            "sub_labels": [{"ref": "@l561260", "name": "NIN"}],
        }

    def test_get_master_json_projection(self) -> None:
        master = fake_model(
            id=3719,
            title="The Downward Spiral",
            year=1994,
            artists=[fake(id=3857, name="Nine Inch Nails", join=None)],
            genres=["Rock", "Folk, World, & Country"],
            styles=["Industrial"],
            main_release=847868,
            num_for_sale=200,
            lowest_price=1.5,
            tracklist=[
                fake(position="1", title="A", duration="", type=None, artists=None)
            ],
        )
        self._set_client(fake_client(masters_get=lambda _id: master))
        data = json.loads(
            CliRunner().invoke(cli, ["get", "--json", "master", "@m3719"]).stdout
        )
        assert data == {
            "ref": "@m3719",
            "title": "The Downward Spiral",
            "artists": [{"ref": "@a3857", "name": "Nine Inch Nails"}],
            "year": 1994,
            "genres": ["Rock", "Folk, World, & Country"],
            "styles": ["Industrial"],
            "main_release": "@r847868",
            "market": {"for_sale": 200, "lowest": 1.5},
            "tracklist": [{"pos": "1", "title": "A"}],
        }

    def test_lowest_price_without_currency_is_a_bare_amount(self) -> None:
        # Release and master lowest_price is a bare float: Discogs prices it in
        # the account's buyer currency without saying which, so neither the
        # text nor the JSON may claim one.
        market = {"num_for_sale": 9, "lowest_price": 9.36}
        release = Release.model_validate({"id": 1, "title": "R", **market})
        master = Master.model_validate({"id": 2, "title": "M", **market})
        self._set_client(
            fake_client(
                releases_get=lambda _id: release, masters_get=lambda _id: master
            )
        )
        for noun, ref in (("release", "@r1"), ("master", "@m2")):
            text = CliRunner().invoke(cli, ["get", noun, ref]).stdout
            assert "Market: 9 for sale from 9.36" in text
            assert "$" not in text
            data = json.loads(
                CliRunner().invoke(cli, ["get", "--json", noun, ref]).stdout
            )
            assert data["market"] == {"for_sale": 9, "lowest": 9.36}

    def test_get_releases_json_projection(self) -> None:
        artist = listed(id=3857, name="NIN")
        self._set_client(fake_client(artists_get=lambda _id: artist))
        rel = fake_model(
            id=3373,
            type="master",
            title="Down In It",
            year=1989,
            role="Main",
            thumb="x",
        )
        self._serve_page(PageResult(items=[rel], page=1, total_items=1, total_pages=1))
        data = json.loads(
            CliRunner().invoke(cli, ["get", "--json", "releases", "@a3857"]).stdout
        )
        assert data["pagination"]["page"] == 1
        assert data["results"] == [
            {
                "ref": "@m3373",
                "type": "master",
                "title": "Down In It",
                "year": 1989,
                "role": "Main",
            }
        ]

    def test_get_versions_json_projection(self) -> None:
        master = listed(id=3719, title="TDS")
        self._set_client(fake_client(masters_get=lambda _id: master))
        ver = fake_model(
            id=847868,
            title="The Downward Spiral",
            released="1994",
            country="US",
            label="Nothing Records",
            catalog_number="92346-2",
            format="CD, Album",
            stats=fake(community=fake(in_collection=7544, in_wantlist=1)),
            thumb="x",
        )
        self._serve_page(PageResult(items=[ver], page=1, total_items=1, total_pages=1))
        data = json.loads(
            CliRunner().invoke(cli, ["get", "--json", "versions", "@m3719"]).stdout
        )
        assert data["results"] == [
            {
                "ref": "@r847868",
                "title": "The Downward Spiral",
                "released": "1994",
                "country": "US",
                "label": "Nothing Records",
                "catno": "92346-2",
                "format": "CD, Album",
                "have": 7544,
            }
        ]

    def test_get_tracklist_json_projection_and_full(self) -> None:
        track = fake_model(
            position="A1",
            title="Track One",
            duration="4:00",
            type="track",
            artists=[fake(id=1, name="X", join=None)],
        )
        release = fake(id=123, title="R", tracklist=[track])
        self._set_client(fake_client(releases_get=lambda _id: release))
        data = json.loads(
            CliRunner().invoke(cli, ["get", "--json", "tracklist", "@r123"]).stdout
        )
        assert data == {
            "ref": "@r123",
            "title": "R",
            "tracklist": [
                {
                    "pos": "A1",
                    "title": "Track One",
                    "duration": "4:00",
                    "artists": [{"ref": "@a1", "name": "X"}],
                }
            ],
        }
        full = json.loads(
            CliRunner()
            .invoke(cli, ["get", "--json", "--full", "tracklist", "@r123"])
            .stdout
        )
        assert full == {"tracklist": [_via_json(track.model_dump())]}

    def test_get_credits_and_identifiers_json(self) -> None:
        credit = fake_model(id=20661, name="Flood", role="Producer", tracks="")
        ident = fake_model(type="Barcode", value="765449234620", description=None)
        release = fake(
            id=123, extra_artists=[credit], identifiers=[ident], tracklist=None
        )
        self._set_client(fake_client(releases_get=lambda _id: release))

        data = json.loads(
            CliRunner().invoke(cli, ["get", "--json", "credits", "@r123"]).stdout
        )
        assert data == {
            "ref": "@r123",
            "credits": {"Producer": [{"ref": "@a20661", "name": "Flood"}]},
        }
        data = json.loads(
            CliRunner().invoke(cli, ["get", "--json", "ids", "@r123"]).stdout
        )
        assert data == {
            "ref": "@r123",
            "identifiers": [{"type": "Barcode", "value": "765449234620"}],
        }
        full = json.loads(
            CliRunner().invoke(cli, ["get", "--json", "--full", "ids", "@r123"]).stdout
        )
        assert full == {"identifiers": [ident.model_dump()]}
        full = json.loads(
            CliRunner()
            .invoke(cli, ["get", "--json", "--full", "credits", "@r123"])
            .stdout
        )
        assert full == {"credits": [credit.model_dump()], "track_credits": []}

        empty = fake(id=123, extra_artists=None, identifiers=None)
        self._set_client(fake_client(releases_get=lambda _id: empty))
        data = json.loads(
            CliRunner().invoke(cli, ["get", "--json", "credits", "@r123"]).stdout
        )
        assert data == {"ref": "@r123"}  # empty facets are omitted, per contract

    def test_get_price_json_projection_and_full(self) -> None:
        price_suggestions = PriceSuggestions.model_validate(
            {
                "Mint (M)": {"currency": "EUR", "value": 100.0},
                "Good (G)": {"currency": "EUR", "value": 12.5},
            }
        )
        marketplace_stats = MarketplaceReleaseStats.model_validate(
            {"num_for_sale": 50, "lowest_price": {"currency": "GBP", "value": 5.0}}
        )
        release = fake(
            id=123,
            title="R",
            price_suggestions=fake(get=lambda: price_suggestions),
            marketplace_stats=fake(get=lambda: marketplace_stats),
        )
        self._set_client(fake_client(releases_get=lambda _id: release))
        data = json.loads(
            CliRunner().invoke(cli, ["get", "--json", "price", "@r123"]).stdout
        )
        assert data == {
            "ref": "@r123",
            "title": "R",
            "suggestions": {
                "Mint (M)": {"value": 100.0, "currency": "EUR"},
                "Good (G)": {"value": 12.5, "currency": "EUR"},
            },
            "market": {"for_sale": 50, "lowest": 5.0, "currency": "GBP"},
        }
        full = json.loads(
            CliRunner()
            .invoke(cli, ["get", "--json", "--full", "price", "@r123"])
            .stdout
        )
        assert full == _via_json(
            {
                **price_suggestions.model_dump(),
                "marketplace_stats": marketplace_stats.model_dump(),
            }
        )

    def test_get_json_error_envelope(self) -> None:
        def _missing(_id: int) -> None:
            raise NotFoundError("nope", status_code=404, response_body={})

        self._set_client(fake_client(masters_get=_missing))
        result = CliRunner().invoke(cli, ["get", "--json", "master", "@m4917"])
        assert result.exit_code == 1
        assert json.loads(result.stdout) == {
            "error": {
                "code": "not_found",
                "message": "Master @m4917 not found.",
                "hint": 'Try: agent-discogs search "<title>"',
                "status": 404,
            }
        }

        result = CliRunner().invoke(cli, ["get", "--json", "credits", "@a3857"])
        assert json_error(result)["code"] == "invalid_argument"

        result = CliRunner().invoke(
            cli, ["get", "--json", "versions", "@m3719", "--after", "2:0"]
        )
        assert json_error(result)["code"] == "invalid_argument"

    def test_get_full_requires_json(self) -> None:
        self._set_client(fake_client())
        result = CliRunner().invoke(cli, ["get", "--full", "master", "@m3719"])
        assert text_error(result) == "✗ --full requires --json.\n"


class TestJsonShortcuts:
    def test_tracks_json(self, use_client: UseClient) -> None:
        track = fake_model(position="1", title="Song", duration="3:00")
        release = fake(id=123, tracklist=[track])
        use_client(fake_client(releases_get=lambda _id: release))
        result = CliRunner().invoke(cli, ["tracks", "--json", "@r123"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert isinstance(data["tracklist"], list)
        assert data["tracklist"][0]["title"] == "Song"

    def test_price_json(self, use_client: UseClient) -> None:
        price_suggestions = PriceSuggestions.model_validate(
            {"Mint (M)": {"value": 50.0, "currency": "USD"}}
        )
        marketplace_stats = MarketplaceReleaseStats.model_validate(
            {"num_for_sale": 10, "lowest_price": None}
        )
        release = fake(
            id=123,
            title="R",
            price_suggestions=fake(get=lambda: price_suggestions),
            marketplace_stats=fake(get=lambda: marketplace_stats),
        )
        use_client(fake_client(releases_get=lambda _id: release))
        result = CliRunner().invoke(cli, ["price", "--json", "@r123"])
        assert result.exit_code == 0
        assert json.loads(result.stdout) == {
            "ref": "@r123",
            "title": "R",
            "suggestions": {"Mint (M)": {"value": 50.0, "currency": "USD"}},
            "market": {"for_sale": 10},
        }

        result = CliRunner().invoke(cli, ["price", "--full", "@r123"])
        assert text_error(result) == "✗ --full requires --json.\n"


class TestReleaseMasterYear:
    """`project_release` puts the master's year, or why it is missing, right
    after `master`, and neither without `--master-year`."""

    _RELEASE = Release.model_validate({"id": 1, "title": "T", "master_id": 5460})

    def test_year_follows_master(self) -> None:
        data = project_release(self._RELEASE, master_year=1959)
        keys = list(data)
        assert keys[keys.index("master") :][:2] == ["master", "master_year"]
        assert data["master_year"] == 1959
        assert "master_year_error" not in data

    def test_error_replaces_the_year(self) -> None:
        error = {"code": "cache_miss", "message": "Not cached: GET /masters/5460"}
        data = project_release(self._RELEASE, master_year_error=error)
        assert data["master_year_error"] == error
        assert "master_year" not in data

    def test_neither_by_default(self) -> None:
        data = project_release(self._RELEASE)
        assert data["master"] == "@m5460"
        assert "master_year" not in data
        assert "master_year_error" not in data
