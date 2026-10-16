"""Tests for the `get` command and the `tracks` / `price` shortcuts."""

from __future__ import annotations

import json
from typing import Any

import httpx2
import pytest
from click.testing import CliRunner
from discogs_sdk import (
    MarketplaceReleaseStats,
    PriceSuggestions,
)
from discogs_sdk.models import LabelRelease

from agent_discogs import cli
from agent_discogs.pagination import PageResult
from tests.conftest import (
    ServePage,
    UseClient,
    _empty_listing,
    _FakeAPI,
    fake,
    fake_client,
    fake_model,
    json_error,
    listed,
    text_error,
)


def _label_row(release_id: int, catno: str = "C1", year: int = 1999) -> LabelRelease:
    """A label catalogue row; two built from the same values are `==`, as two
    rows Discogs sent alike are."""
    return LabelRelease.model_validate(
        {
            "id": release_id,
            "title": f"T{release_id}",
            "artist": "A",
            "year": year,
            "catno": catno,
            "format": "CD",
        }
    )


def _row_refs(stdout: str) -> list[str]:
    return [line.split(" ", 1)[0] for line in stdout.splitlines() if line[:2] == "@r"]


class TestVersionsRequest:
    """Each `get versions` filter and sort reaches Discogs as its query
    parameter, on the master's versions listing."""

    @pytest.fixture(autouse=True)
    def _fake_api(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.api = _FakeAPI(monkeypatch, _empty_listing)

    def _sent(self, *args: str) -> dict[str, str]:
        """The params of the one versions request `get versions @m4917 <args>`
        sends; the text header's title lookup is the other request."""
        result = CliRunner().invoke(cli, ["get", "versions", "@m4917", *args])
        assert result.exit_code == 0, result.output
        (params,) = [p for path, p in self.api.sends if path.endswith("/versions")]
        assert {path for path, _ in self.api.sends} == {
            "/masters/4917/versions",
            "/masters/4917",
        }
        return params

    @pytest.mark.parametrize(
        ("flag", "param"),
        [
            ("--country", "country"),
            ("--format", "format"),
            ("--label", "label"),
            ("--year", "released"),
        ],
    )
    def test_each_filter_is_its_own_parameter(self, flag: str, param: str) -> None:
        assert self._sent(flag, "Value") == {
            param: "Value",
            "page": "1",
            "per_page": "5",
        }

    @pytest.mark.parametrize(("desc", "order"), [(["--desc"], "desc"), ([], "asc")])
    def test_sort_and_order(self, desc: list[str], order: str) -> None:
        assert self._sent("--sort", "released", *desc) == {
            "sort": "released",
            "sort_order": order,
            "page": "1",
            "per_page": "5",
        }

    def test_get_versions_with_filters(self) -> None:
        args = ["--country", "US", "--format", "Vinyl", "--label", "Nothing"]
        result = CliRunner().invoke(cli, ["get", "versions", "@m4917", *args])
        assert result.exit_code == 0, result.output
        assert result.stdout == (
            'Versions of @m4917 "Entity" (page 1, 0 of 0 versions)\n\n'
        )
        assert self.api.sends == [
            (
                "/masters/4917/versions",
                {
                    "country": "US",
                    "format": "Vinyl",
                    "label": "Nothing",
                    "page": "1",
                    "per_page": "5",
                },
            ),
            ("/masters/4917", {}),
        ]

    @pytest.mark.parametrize("policy", [[], ["--cached"]])
    def test_a_failing_listing_costs_one_request(
        self, monkeypatch: pytest.MonkeyPatch, policy: list[str]
    ) -> None:
        """Without a scan the header title is read only after the listing
        succeeded: a missing master costs its one request, and --cached names
        the versions listing it could not serve."""

        def missing(_path: str, _params: dict[str, str]) -> object:
            return httpx2.Response(404, json={"message": "Master not found."})

        api = _FakeAPI(monkeypatch, missing)
        result = CliRunner().invoke(cli, [*policy, "get", "versions", "@m55"])
        stderr = text_error(result)
        if policy:
            assert api.sends == []
            assert "Not cached: GET /masters/55/versions" in stderr
        else:
            assert [path for path, _ in api.sends] == ["/masters/55/versions"]
            assert "not found" in stderr


class TestGetCommand:
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

    def test_get_artist(self) -> None:
        artist = fake(
            id=3857,
            name="Nine Inch Nails",
            profile="Industrial",
            urls=None,
            members=None,
        )
        self._set_client(fake_client(artists_get=lambda _id: artist))
        result = CliRunner().invoke(cli, ["get", "artist", "@a3857"])
        assert result.exit_code == 0
        assert "Nine Inch Nails" in result.stdout

    def test_get_label(self) -> None:
        label = fake(
            id=2919,
            name="Nothing Records",
            profile="Label",
            urls=None,
            sub_labels=None,
        )
        self._set_client(fake_client(labels_get=lambda _id: label))
        result = CliRunner().invoke(cli, ["get", "label", "@l2919"])
        assert result.exit_code == 0
        assert "Nothing Records" in result.stdout

    def test_get_master(self) -> None:
        artist = fake(name="NIN", join=None)
        master = fake(
            id=4917,
            title="The Downward Spiral",
            year=1994,
            artists=[artist],
            genres=["Electronic"],
            styles=None,
            main_release=367113,
            num_for_sale=None,
            lowest_price=None,
            tracklist=None,
        )
        self._set_client(fake_client(masters_get=lambda _id: master))
        result = CliRunner().invoke(cli, ["get", "master", "@m4917"])
        assert result.exit_code == 0
        assert "The Downward Spiral" in result.stdout

    def test_get_release(self) -> None:
        release = fake(
            id=367113,
            title="The Downward Spiral",
            year=1994,
            artists=None,
            community=None,
            labels=None,
            formats=None,
            genres=None,
            styles=None,
            num_for_sale=None,
            lowest_price=None,
            master_id=None,
            tracklist=None,
        )
        self._set_client(fake_client(releases_get=lambda _id: release))
        result = CliRunner().invoke(cli, ["get", "release", "@r367113"])
        assert result.exit_code == 0
        assert "The Downward Spiral" in result.stdout

    def test_get_price(self) -> None:
        suggestions = PriceSuggestions.model_validate(
            {"Mint (M)": {"currency": "EUR", "value": 100.0}}
        )
        stats = MarketplaceReleaseStats.model_validate(
            {"num_for_sale": 100, "lowest_price": {"currency": "GBP", "value": 5.0}}
        )
        release = fake(
            id=367113,
            title="TDS",
            year=1994,
            artists=None,
            price_suggestions=fake(get=lambda: suggestions),
            marketplace_stats=fake(get=lambda: stats),
        )
        self._set_client(fake_client(releases_get=lambda _id: release))
        result = CliRunner().invoke(cli, ["get", "price", "@r367113"])
        assert result.exit_code == 0
        assert "Price Guide:" in result.stdout
        assert "EUR 100.00" in result.stdout
        assert "Lowest: GBP 5.00" in result.stdout
        assert "$" not in result.stdout

    def test_get_credits(self) -> None:
        release = fake(
            id=847868,
            title="The Downward Spiral",
            extra_artists=[
                fake(id=20661, name="Flood", role="Producer [Production]", tracks="")
            ],
        )
        self._set_client(fake_client(releases_get=lambda _id: release))
        result = CliRunner().invoke(cli, ["get", "credits", "@r847868"])
        assert result.exit_code == 0
        assert 'Credits: @r847868 "The Downward Spiral" (1)' in result.stdout
        assert "Producer [Production]: Flood [@a20661]" in result.stdout

    def test_get_identifiers_and_ids_alias(self) -> None:
        release = fake(
            id=847868,
            title="The Downward Spiral",
            identifiers=[
                fake(type="Barcode", value="765449234620", description="Scanned")
            ],
        )
        self._set_client(fake_client(releases_get=lambda _id: release))
        full = CliRunner().invoke(cli, ["get", "identifiers", "@r847868"])
        alias = CliRunner().invoke(cli, ["get", "ids", "@r847868"])
        assert full.exit_code == 0
        assert "Barcode: 765449234620 (Scanned)" in full.stdout
        assert alias.stdout == full.stdout

    def test_get_credits_rejects_non_release_ref(self) -> None:
        self._set_client(fake_client())
        result = CliRunner().invoke(cli, ["get", "credits", "@a3857"])
        assert text_error(result) == (
            "✗ @a3857 is an artist, not a release. "
            "Use a release ref or ID with 'get credits'.\n"
        )

    def test_get_tracklist(self) -> None:
        track = fake(
            position="A1", title="Mr. Self Destruct", duration="4:09", type=None
        )
        release = fake(
            id=367113,
            title="TDS",
            year=1994,
            artists=None,
            tracklist=[track],
        )
        self._set_client(fake_client(releases_get=lambda _id: release))
        result = CliRunner().invoke(cli, ["get", "tracklist", "@r367113"])
        assert result.exit_code == 0
        assert "A1. Mr. Self Destruct" in result.stdout

    def test_get_releases(self) -> None:
        artist = listed(id=3857, name="Nine Inch Nails")
        self._set_client(fake_client(artists_get=lambda _id: artist))

        rel = fake(id=4917, type="master", title="TDS", year=1994, role="Main")
        self._serve_page(
            PageResult(items=[rel], page=1, total_items=100, total_pages=20)
        )

        result = CliRunner().invoke(cli, ["get", "releases", "@a3857"])
        assert result.exit_code == 0
        assert "Nine Inch Nails" in result.stdout
        assert "Next page:" in result.stdout

    def test_get_versions_master(self) -> None:
        master = listed(id=4917, title="The Downward Spiral")
        self._set_client(fake_client(masters_get=lambda _id: master))

        ver = fake(
            id=367113,
            released="1994",
            country="US",
            label="Nothing",
            catalog_number="INT-92346",
            format="Vinyl",
        )
        self._serve_page(
            PageResult(items=[ver], page=1, total_items=500, total_pages=100)
        )

        result = CliRunner().invoke(cli, ["get", "versions", "@m4917"])
        assert result.exit_code == 0
        assert "Versions of" in result.stdout
        assert "Next page:" in result.stdout

    def test_get_versions_release_smart_resolution(self) -> None:
        """Release ref with master_id resolves to master versions."""
        release = fake(id=367113, master_id=4917, title="The Downward Spiral")
        self._set_client(fake_client(releases_get=lambda _id: release))

        ver = fake(
            id=999,
            released="2020",
            country="UK",
            label="L",
            catalog_number="C",
            format="CD",
        )
        self._serve_page(PageResult(items=[ver], page=1, total_items=1, total_pages=1))

        result = CliRunner().invoke(cli, ["get", "versions", "@r367113"])
        assert result.exit_code == 0
        assert "Versions of" in result.stdout

    def test_get_versions_release_no_master(self) -> None:
        """Release ref with no master_id produces error."""
        release = fake(id=367113, master_id=None, title="Some Single")
        self._set_client(fake_client(releases_get=lambda _id: release))

        result = CliRunner().invoke(cli, ["get", "versions", "@r367113"])
        assert text_error(result).startswith("✗ @r367113 is a release with no master. ")

    def test_get_type_mismatch(self) -> None:
        self._set_client(fake_client())
        result = CliRunner().invoke(cli, ["get", "artist", "@r367113"])
        assert text_error(result) == (
            "✗ @r367113 is a release, not an artist. "
            "Use an artist ref or ID with 'get artist'.\n"
        )

    def test_get_error_handling(self) -> None:
        def _raise(_id: int) -> None:
            raise RuntimeError("API down")

        self._set_client(fake_client(artists_get=_raise))
        result = CliRunner().invoke(cli, ["get", "artist", "@a1"])
        assert text_error(result) == "✗ Unexpected error: RuntimeError: API down\n"

    def test_get_release_verbose(self) -> None:
        artist = fake(id=3857, name="Nine Inch Nails", join=None)
        label = fake(id=33244, name="Rhino Records (2)", catalog_number="R2 75933")
        track = fake(
            position="1",
            title="Hamburger Lady",
            duration="4:12",
            type=None,
            artists=[fake(id=12589, name="Throbbing Gristle", join=None)],
        )
        release = fake(
            id=367113,
            title="The Downward Spiral",
            year=1994,
            artists=[artist],
            community=None,
            labels=[label],
            formats=None,
            genres=None,
            styles=None,
            num_for_sale=None,
            lowest_price=None,
            master_id=None,
            tracklist=[track],
            notes="Pressed at Sterling Sound.",
        )
        self._set_client(fake_client(releases_get=lambda _id: release))
        result = CliRunner().invoke(cli, ["get", "release", "@r367113", "--verbose"])
        assert result.exit_code == 0
        assert "Notes: Pressed at Sterling Sound." in result.stdout
        assert "[@a3857]" in result.stdout
        assert "[@l33244]" in result.stdout
        assert "[@a12589]" in result.stdout

    def test_get_release_no_verbose_hides_notes(self) -> None:
        release = fake(
            id=367113,
            title="The Downward Spiral",
            year=1994,
            artists=None,
            community=None,
            labels=None,
            formats=None,
            genres=None,
            styles=None,
            num_for_sale=None,
            lowest_price=None,
            master_id=None,
            tracklist=None,
            notes="Pressed at Sterling Sound.",
        )
        self._set_client(fake_client(releases_get=lambda _id: release))
        result = CliRunner().invoke(cli, ["get", "release", "@r367113"])
        assert result.exit_code == 0
        assert "Notes:" not in result.stdout

    def test_get_release_compact_text_and_json(self) -> None:
        release = fake_model(
            id=847868,
            title="The Downward Spiral",
            year=1994,
            artists=None,
            community=None,
            labels=None,
            formats=None,
            genres=None,
            styles=None,
            num_for_sale=None,
            lowest_price=None,
            master_id=None,
            tracklist=[
                fake(position="1", title="A", duration="4:30", type=None, artists=None),
                fake(position="2", title="B", duration="4:24", type=None, artists=None),
            ],
        )
        self._set_client(fake_client(releases_get=lambda _id: release))
        text = CliRunner().invoke(cli, ["get", "release", "@r847868", "-c"])
        assert text.exit_code == 0
        assert "Tracks: 2 (8:54)" in text.stdout
        assert "Tracklist:" not in text.stdout

        data = json.loads(
            CliRunner()
            .invoke(cli, ["get", "release", "@r847868", "-c", "--json"])
            .stdout
        )
        assert data["tracks"] == "2 (8:54)"
        assert "tracklist" not in data

    def test_get_with_raw_id(self) -> None:
        release = fake(
            id=367113,
            title="Test",
            year=None,
            artists=None,
            community=None,
            labels=None,
            formats=None,
            genres=None,
            styles=None,
            num_for_sale=None,
            lowest_price=None,
            master_id=None,
            tracklist=None,
        )
        requested: list[int] = []
        self._set_client(
            fake_client(releases_get=lambda id_: requested.append(id_) or release)
        )
        raw = CliRunner().invoke(cli, ["get", "release", "367113"])
        ref = CliRunner().invoke(cli, ["get", "release", "@r367113"])
        assert raw.exit_code == 0
        assert requested == [367113, 367113]
        assert raw.stdout == ref.stdout
        assert raw.stdout.startswith('@r367113 [release] "Test"')

    def test_get_releases_role_filter(self) -> None:
        """--role filters releases by credit role (case-insensitive substring)."""
        artist = listed(id=3857, name="Nine Inch Nails")
        self._set_client(fake_client(artists_get=lambda _id: artist))

        main_rel = fake(id=100, type="master", title="TDS", year=1994, role="Main")
        remix_rel = fake(
            id=200, type="master", title="Remix Album", year=1995, role="Remix"
        )
        compound_rel = fake(
            id=300,
            type="master",
            title="Collab",
            year=1996,
            role="Producer, Written-By",
        )
        self._serve_page(
            PageResult(
                items=[main_rel, remix_rel, compound_rel],
                page=1,
                total_items=3,
                total_pages=1,
            )
        )

        result = CliRunner().invoke(
            cli, ["get", "releases", "@a3857", "--role", "producer"]
        )
        assert result.exit_code == 0
        assert "Collab" in result.stdout
        assert "TDS" not in result.stdout
        assert "Remix Album" not in result.stdout

    def test_get_releases_role_no_match(self) -> None:
        """--role with no matching releases shows empty results."""
        artist = listed(id=3857, name="Nine Inch Nails")
        self._set_client(fake_client(artists_get=lambda _id: artist))

        rel = fake(id=100, type="master", title="TDS", year=1994, role="Main")
        self._serve_page(PageResult(items=[rel], page=1, total_items=1, total_pages=1))

        result = CliRunner().invoke(
            cli, ["get", "releases", "@a3857", "--role", "DJ Mix"]
        )
        assert result.exit_code == 0
        assert "TDS" not in result.stdout

    def test_get_releases_role_header_marks_the_filtered_bound(self) -> None:
        """--role filters client-side, so the total is an upper bound."""
        artist = listed(id=3857, name="Nine Inch Nails")
        self._set_client(fake_client(artists_get=lambda _id: artist))

        rel = fake(id=100, type="master", title="TDS", year=1994, role="Main")
        self._serve_page(
            PageResult(items=[rel], page=1, total_items=50, total_pages=10)
        )

        result = CliRunner().invoke(
            cli, ["get", "releases", "@a3857", "--role", "Main"]
        )
        assert result.exit_code == 0
        assert "of ≤50 results" in result.stdout

    def _set_exploding_client(self) -> None:
        """Any resource access proves validation ran after I/O started."""

        def _boom(_id: int) -> None:
            raise AssertionError("API call made before flag validation")

        self._set_client(
            fake_client(
                artists_get=_boom,
                labels_get=_boom,
                masters_get=_boom,
                releases_get=_boom,
            )
        )
        self._monkeypatch.setattr("agent_discogs.pagination.fetch_page", _boom)
        self._monkeypatch.setattr("agent_discogs.commands.get.fetch_page", _boom)

    def test_get_releases_role_rejects_page_before_io(self) -> None:
        self._set_exploding_client()
        result = CliRunner().invoke(
            cli, ["get", "releases", "@a3857", "--role", "Main", "--page", "2"]
        )
        assert text_error(result).startswith("✗ --page is not available")

    @pytest.mark.parametrize(
        "argv",
        [
            ["get", "releases", "@a3857", "--after", "2:0"],
            ["get", "versions", "@m3719", "--after", "2:0"],
            ["get", "release", "@r847868", "--after", "2:0"],
            ["get", "artist", "@a3857", "--after", "2:0"],
        ],
    )
    def test_get_after_rejected_unless_role_filtered(self, argv: list[str]) -> None:
        self._set_exploding_client()
        result = CliRunner().invoke(cli, argv)
        assert text_error(result).startswith("✗ --after only continues")

    def test_get_invalid_cursor_rejected_before_io(self) -> None:
        self._set_exploding_client()
        result = CliRunner().invoke(
            cli, ["get", "releases", "@a3857", "--role", "Main", "--after", "nope"]
        )
        assert text_error(result).startswith("✗ Invalid --after cursor")

    def _capture_fetch(self) -> None:
        """Fake `fetch_page` to request its page from the listing it is given,
        which a fake listing records, and return an empty page."""

        def _fetch(listing: Any, *, page: int, per_page: int) -> PageResult:
            listing(page=page, per_page=per_page)
            return PageResult(items=[], page=1, total_items=0, total_pages=1)

        self._monkeypatch.setattr("agent_discogs.commands.get.fetch_page", _fetch)
        self._monkeypatch.setattr("agent_discogs.pagination.fetch_page", _fetch)

    def test_get_releases_for_a_label(self) -> None:
        label = listed(id=647, name="Nothing Records")
        self._set_client(fake_client(labels_get=lambda _id: label))
        rel = fake_model(
            id=4401,
            title="Pretty Hate Machine",
            artist="Nine Inch Nails",
            year=0,
            catalog_number="0694903742",
            format="CD, Album",
        )

        def _fetch(listing: Any, *, page: int, per_page: int) -> PageResult:
            listing(page=page, per_page=per_page)
            return PageResult(items=[rel], page=1, total_items=2718, total_pages=544)

        self._monkeypatch.setattr("agent_discogs.commands.get.fetch_page", _fetch)
        result = CliRunner().invoke(cli, ["get", "releases", "@l647"])
        assert result.exit_code == 0
        assert label.releases.list.calls == [{"page": 1, "per_page": 5}]
        assert 'Releases on @l647 "Nothing Records" (page 1, 1 of 2,718 results)' in (
            result.stdout
        )
        assert (
            '@r4401 "Pretty Hate Machine" by Nine Inch Nails · 0694903742 · CD, Album'
            in result.stdout
        )

        data = json.loads(
            CliRunner().invoke(cli, ["get", "--json", "releases", "@l647"]).stdout
        )
        assert data["results"] == [
            {
                "ref": "@r4401",
                "title": "Pretty Hate Machine",
                "artist": "Nine Inch Nails",
                "catno": "0694903742",
                "format": "CD, Album",
            }
        ]  # year 0 is "unknown" on Discogs and is dropped

    def test_get_releases_sort_reaches_api(self) -> None:
        artist = listed(id=3857, name="NIN")
        self._set_client(fake_client(artists_get=lambda _id: artist))
        self._capture_fetch()
        result = CliRunner().invoke(
            cli, ["get", "releases", "@a3857", "--sort", "year", "--desc"]
        )
        assert result.exit_code == 0
        assert artist.releases.list.calls == [
            {"sort": "year", "sort_order": "desc", "page": 1, "per_page": 5}
        ]

    def test_get_releases_role_with_sort_passes_sort_to_scan(self) -> None:
        artist = listed(id=3857, name="NIN")
        self._set_client(fake_client(artists_get=lambda _id: artist))
        self._capture_fetch()
        result = CliRunner().invoke(
            cli, ["get", "releases", "@a3857", "--role", "Main", "--sort", "title"]
        )
        assert result.exit_code == 0
        # The client-side scan reads Discogs' largest pages.
        assert artist.releases.list.calls == [
            {"sort": "title", "sort_order": "asc", "page": 1, "per_page": 100}
        ]

    @pytest.mark.parametrize(
        ("argv", "message"),
        [
            (
                ["get", "releases", "@l647", "--role", "Main"],
                "Label catalogues cannot be role-filtered or sorted",
            ),
            (
                ["get", "releases", "@l647", "--sort", "year"],
                "Label catalogues cannot be role-filtered or sorted",
            ),
            (
                ["get", "versions", "@m3719", "--sort", "year"],
                "--sort 'year' is not valid for 'get versions'. Keys: released,",
            ),
            (
                ["get", "release", "@r847868", "--sort", "title"],
                "--sort does not apply to 'get release'.",
            ),
            (["get", "releases", "@a3857", "--desc"], "--desc needs --sort <key>."),
            (
                ["get", "releases", "@a3857", "--year", "1994"],
                "--year filters versions and label catalogues",
            ),
            (
                ["get", "release", "@r847868", "--year", "1994"],
                "--year does not apply to 'get release'.",
            ),
            (
                ["get", "releases", "@l647", "--year", "1999", "--page", "2"],
                "--page is not available while results are filtered client-side",
            ),
            (
                ["get", "releases", "@l647", "--after", "2:0"],
                "--after only continues a client-side filtered listing",
            ),
        ],
    )
    def test_sort_flag_errors_before_io(self, argv: list[str], message: str) -> None:
        self._set_exploding_client()
        result = CliRunner().invoke(cli, argv)
        assert message in text_error(result)

    @pytest.mark.parametrize(
        ("argv", "message"),
        [
            (
                ["get", "releases", "@a11", "--country", "FR", "--format", "Vinyl"],
                (
                    "--country, --format do not apply to 'get releases'. "
                    "'get releases' takes --after, --desc, --limit, --max-requests, "
                    "--owned, --page, --role, --sort, --wanted, --year."
                ),
            ),
            (
                ["get", "releases", "@l647", "--label", "Nothing"],
                "--label does not apply to 'get releases'.",
            ),
            (
                ["get", "versions", "@m3719", "--role", "Main"],
                "--role does not apply to 'get versions'.",
            ),
            (
                ["get", "release", "@r847868", "--role", "Main"],
                "--role does not apply to 'get release'.",
            ),
            # An explicit --limit equal to the default is still explicit.
            (
                ["get", "artist", "@a3857", "--limit", "5"],
                (
                    "--limit does not apply to 'get artist'. "
                    "'get artist' takes only --json and --full."
                ),
            ),
            (
                ["get", "label", "@l647", "--page", "2"],
                "--page does not apply to 'get label'.",
            ),
            (
                ["get", "credits", "@r847868", "--desc"],
                "--desc does not apply to 'get credits'.",
            ),
            (
                ["get", "tracklist", "@r847868", "--limit", "10", "--page", "2"],
                "--limit, --page do not apply to 'get tracklist'.",
            ),
            (
                ["get", "ids", "@r847868", "--page", "1"],
                "--page does not apply to 'get identifiers'.",
            ),
            (
                ["get", "master", "@m3719", "--verbose"],
                "--verbose does not apply to 'get master'.",
            ),
            (
                ["get", "releases", "@a3857", "-c"],
                "--compact does not apply to 'get releases'.",
            ),
            (
                ["get", "versions", "@m3719", "-v", "--compact"],
                "--compact, --verbose do not apply to 'get versions'.",
            ),
            (
                ["get", "release", "@r847868", "--limit", "5"],
                (
                    "--limit does not apply to 'get release'. "
                    "'get release' takes --compact, --master-year, --verbose."
                ),
            ),
            (
                ["get", "releases", "@a1", "@a2", "--role", "Main", "--after", "2:0"],
                "--after continues one listing, so it takes exactly one ref (2 given).",
            ),
            (
                [
                    "get",
                    "releases",
                    "@l1",
                    "@l2",
                    "--year",
                    "1999",
                    "--after",
                    "2:0",
                ],
                "--after continues one listing, so it takes exactly one ref (2 given).",
            ),
        ],
    )
    @pytest.mark.parametrize("json_output", [False, True], ids=["text", "json"])
    @pytest.mark.parametrize("mode", [[], ["--cached"], ["--fresh"]], ids=str)
    def test_inapplicable_flags_rejected_before_client(
        self, argv: list[str], message: str, json_output: bool, mode: list[str]
    ) -> None:
        built: list[str] = []
        self._monkeypatch.setattr(
            "agent_discogs.client.get_client", lambda: built.append("client")
        )
        self._monkeypatch.setattr(
            "agent_discogs.commands.get.get_client", lambda: built.append("command")
        )
        args = [*mode, *argv, *(["--json"] if json_output else [])]
        result = CliRunner().invoke(cli, args)
        if json_output:
            error = json_error(result)
            assert error["code"] == "invalid_argument"
            assert message in error["message"]
        else:
            assert message in text_error(result)
        assert built == []  # rejected before any client exists: no request

    def test_get_releases_label_year_scans_client_side_with_cursor(self) -> None:
        label = listed(id=647, name="Nothing Records")
        self._set_client(fake_client(labels_get=lambda _id: label))

        def _fetch(listing: Any, *, page: int, per_page: int) -> PageResult:
            listing(page=page, per_page=per_page)
            api_page = page
            rows = [
                fake_model(
                    id=api_page * 100 + i,
                    title=f"T{api_page}-{i}",
                    artist="A",
                    year=1999 if i % 3 == 0 else 2001,
                    catalog_number="",
                    format="CD",
                )
                for i in range(6)
            ]
            return PageResult(items=rows, page=api_page, total_items=60, total_pages=10)

        self._monkeypatch.setattr("agent_discogs.pagination.fetch_page", _fetch)
        result = CliRunner().invoke(
            cli, ["get", "releases", "@l647", "--year", "1999", "--limit", "2"]
        )
        assert result.exit_code == 0
        assert label.releases.list.calls == [{"page": 1, "per_page": 100}]
        assert 'Releases on @l647 "Nothing Records" (page 1, 2 of ≤60 results)' in (
            result.stdout
        )
        assert '@r100 "T1-0" by A 1999 · CD' in result.stdout
        assert '@r103 "T1-3" by A 1999 · CD' in result.stdout
        assert "2001" not in result.stdout

        data = json.loads(
            CliRunner()
            .invoke(cli, ["get", "--json", "releases", "@l647", "--year", "1999"])
            .stdout
        )
        assert data["pagination"]["filtered"] is True
        assert all(r["year"] == 1999 for r in data["results"])

    @pytest.mark.parametrize(
        ("rows", "shown", "note"),
        [
            ([(1, "C1"), (1, "C1"), (2, "C1")], [1, 2], "; 1 repeated row dropped"),
            ([(1, "C1")] * 3, [1], "; 2 repeated rows dropped"),
            ([(1, "C1"), (2, "C1"), (1, "C1")], [1, 2, 1], ""),
            ([(1, "C1"), (1, "C2")], [1, 1], ""),  # another catalogue number
        ],
        ids=["pair", "run of three", "apart", "different catno"],
    )
    def test_label_page_drops_a_row_equal_to_the_one_before(
        self, rows: list[tuple[int, str]], shown: list[int], note: str
    ) -> None:
        label = listed(id=647, name="Nothing Records")
        self._set_client(fake_client(labels_get=lambda _id: label))
        items = [_label_row(i, catno) for i, catno in rows]
        self._serve_page(PageResult(items, page=1, total_items=2718, total_pages=544))
        result = CliRunner().invoke(cli, ["get", "releases", "@l647"])
        assert result.exit_code == 0, result.output
        assert f"(page 1, {len(shown)} of 2,718 results{note})\n" in result.stdout
        assert _row_refs(result.stdout) == [f"@r{i}" for i in shown]
        assert "Next page: agent-discogs get releases @l647 --page 2" in result.stdout

    @pytest.mark.parametrize("full", [False, True])
    def test_label_page_json_counts_the_dropped_rows(self, full: bool) -> None:
        label = listed(id=647, name="Nothing Records")
        self._set_client(fake_client(labels_get=lambda _id: label))
        argv = ["get", "--json", "releases", "@l647", *(["--full"] if full else [])]
        items = [_label_row(1), _label_row(1), _label_row(2)]
        self._serve_page(PageResult(items, page=1, total_items=2718, total_pages=544))
        data = json.loads(CliRunner().invoke(cli, argv).stdout)
        assert data["pagination"] == {
            "page": 1,
            "total_items": 2718,
            "total_pages": 544,
            "duplicates_dropped": 1,
        }
        assert [row["id" if full else "ref"] for row in data["results"]] == (
            [1, 2] if full else ["@r1", "@r2"]
        )

        self._serve_page(PageResult(items[1:], page=1, total_items=2, total_pages=1))
        data = json.loads(CliRunner().invoke(cli, argv).stdout)
        assert "duplicates_dropped" not in data["pagination"]

    def test_label_year_scan_drops_repeats_and_still_fills_the_page(self) -> None:
        label = listed(id=647, name="Nothing Records")
        self._set_client(fake_client(labels_get=lambda _id: label))
        # A matching pair, a pair from another year (not counted), then two
        # distinct matches; the page of 2 fills on row 4.
        years = [(1, 1999), (1, 1999), (2, 2001), (2, 2001), (3, 1999), (4, 1999)]
        items = [_label_row(i, year=year) for i, year in years]
        self._serve_page(PageResult(items, page=1, total_items=60, total_pages=1))
        argv = ["get", "releases", "@l647", "--year", "1999", "--limit", "2"]
        result = CliRunner().invoke(cli, argv)
        assert result.exit_code == 0, result.output
        assert "(page 1, 2 of ≤60 results; 1 repeated row dropped)\n" in result.stdout
        assert _row_refs(result.stdout) == ["@r1", "@r3"]
        assert "--after 2:5" in result.stdout  # the row after @r3, as before

        data = json.loads(CliRunner().invoke(cli, [*argv, "--json"]).stdout)
        assert data["pagination"]["duplicates_dropped"] == 1
        assert data["pagination"]["next_cursor"] == "2:5"
        assert [row["ref"] for row in data["results"]] == ["@r1", "@r3"]

    def test_artist_releases_keep_equal_rows(self) -> None:
        artist = listed(id=3857, name="NIN")
        self._set_client(fake_client(artists_get=lambda _id: artist))
        row = fake_model(id=1, type="release", title="T", role="Main")
        self._serve_page(PageResult([row, row], page=1, total_items=2, total_pages=1))
        result = CliRunner().invoke(cli, ["get", "releases", "@a3857"])
        assert result.exit_code == 0, result.output
        assert "(page 1, 2 of 2 results)\n" in result.stdout
        assert _row_refs(result.stdout) == ["@r1", "@r1"]


class TestShortcutCommands:
    def test_tracks_shortcut(self, use_client: UseClient) -> None:
        track = fake(position="1", title="Track One", duration="3:00", type=None)
        release = fake(
            id=123,
            title="Album",
            year=2020,
            artists=None,
            tracklist=[track],
        )
        use_client(fake_client(releases_get=lambda _id: release))
        result = CliRunner().invoke(cli, ["tracks", "@r123"])
        assert result.exit_code == 0
        assert "Track One" in result.stdout

    def test_price_shortcut(self, use_client: UseClient) -> None:
        price = fake(value=50.00, currency="USD")
        release = fake(
            id=123,
            title="Album",
            year=2020,
            artists=None,
            price_suggestions=fake(get=lambda: fake(conditions={"Mint (M)": price})),
            marketplace_stats=fake(
                get=lambda: fake(num_for_sale=10, lowest_price=None)
            ),
        )
        use_client(fake_client(releases_get=lambda _id: release))
        result = CliRunner().invoke(cli, ["price", "@r123"])
        assert result.exit_code == 0
        assert "Price Guide:" in result.stdout
