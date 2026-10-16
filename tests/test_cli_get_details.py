"""Tests for `get` with several refs, and for the release details its
views show: unknown dates, format entries, date added, index tracks, track
credits and the master's year."""

from __future__ import annotations

import json

import httpx2
import pytest
from click.testing import CliRunner
from discogs_sdk import (
    Master,
    NotFoundError,
    Release,
    SearchResult,
)

from agent_discogs import cli
from agent_discogs.pagination import PageResult
from tests.conftest import (
    UseClient,
    _FakeAPI,
    fake,
    fake_client,
    fake_model,
    json_error,
    text_error,
)


class TestMultiRef:
    """`get`, `tracks`, and `price` accept several refs: one command, N blocks."""

    @pytest.fixture(autouse=True)
    def _patch_get(self, use_client: UseClient) -> None:
        releases = {
            847868: fake_model(
                id=847868,
                title="The Downward Spiral",
                year=1994,
                artists=None,
                community=None,
                labels=[fake(id=647, name="Nothing Records", catalog_number="92346-2")],
                formats=None,
                genres=None,
                styles=None,
                num_for_sale=None,
                lowest_price=None,
                master_id=None,
                tracklist=[fake(position="1", title="A", duration="1:00", type=None)],
            ),
            12453760: fake_model(
                id=12453760,
                title="The Downward Spiral",
                year=1994,
                artists=None,
                community=None,
                labels=[
                    fake(id=647, name="Nothing Records", catalog_number="7 92346-2")
                ],
                formats=None,
                genres=None,
                styles=None,
                num_for_sale=None,
                lowest_price=None,
                master_id=None,
                tracklist=[fake(position="1", title="A", duration="1:00", type=None)],
            ),
        }
        self.calls: list[int] = []

        def _get(release_id: int) -> object:
            self.calls.append(release_id)
            if release_id not in releases:
                raise NotFoundError("nope", status_code=404, response_body={})
            return releases[release_id]

        use_client(fake_client(releases_get=_get))

    def test_text_blocks_in_order_separated_by_blank_line(self) -> None:
        result = CliRunner().invoke(
            cli, ["get", "release", "@r847868", "@r12453760", "-c"]
        )
        assert result.exit_code == 0
        assert self.calls == [847868, 12453760]
        first, second = result.stdout.strip().split("\n\n")
        assert first.startswith("@r847868 [release]")
        assert "Label: Nothing Records [@l647] (92346-2)" in first
        assert second.startswith("@r12453760 [release]")
        assert "Label: Nothing Records [@l647] (7 92346-2)" in second

    def test_failed_ref_is_an_ordered_inline_block(self) -> None:
        """Errors are stdout blocks in ref order, blank-separated like successes."""
        result = CliRunner().invoke(
            cli,
            ["get", "release", "@r847868", "@r1", "@r12453760", "-c"],
        )
        assert result.exit_code == 1
        assert self.calls == [847868, 1, 12453760]
        blocks = result.stdout.strip().split("\n\n")
        assert len(blocks) == 3
        assert blocks[0].startswith("@r847868 [release]")
        assert blocks[1] == (
            '✗ Release @r1 not found.\n  Try: agent-discogs search "<title>"'
        )
        assert blocks[2].startswith("@r12453760 [release]")
        assert result.stderr == ""

    def test_json_list_with_error_items(self) -> None:
        result = CliRunner().invoke(
            cli, ["get", "--json", "-c", "release", "@r847868", "@r1"]
        )
        assert result.exit_code == 1
        data = json.loads(result.stdout)
        assert isinstance(data, list)
        assert data[0]["ref"] == "@r847868"
        assert data[0]["tracks"] == "1 (1:00)"
        assert data[1] == {
            "ref": "@r1",
            "error": {
                "code": "not_found",
                "message": "Release @r1 not found.",
                "hint": 'Try: agent-discogs search "<title>"',
                "status": 404,
            },
        }

    def test_single_ref_json_shape_is_unchanged(self) -> None:
        result = CliRunner().invoke(cli, ["get", "--json", "release", "@r847868"])
        assert isinstance(json.loads(result.stdout), dict)

        result = CliRunner().invoke(cli, ["get", "--json", "release", "@r1"])
        assert result.exit_code == 1
        assert set(json.loads(result.stdout)) == {"error"}

    def test_shortcuts_take_several_refs(self, use_client: UseClient) -> None:
        result = CliRunner().invoke(cli, ["tracks", "@r847868", "@r12453760"])
        assert result.exit_code == 0
        assert result.stdout.count("Tracklist: ") == 2

        def _release(release_id: int) -> object:
            if release_id == 1:
                raise NotFoundError("nope", status_code=404, response_body={})
            return fake(
                id=release_id,
                title=f"R{release_id}",
                artists=None,
                year=None,
                price_suggestions=fake(
                    get=lambda: fake(
                        conditions={
                            "Mint (M)": fake(value=float(release_id), currency="USD")
                        }
                    )
                ),
                marketplace_stats=fake(
                    get=lambda: fake(num_for_sale=1, lowest_price=None)
                ),
            )

        use_client(fake_client(releases_get=_release))
        result = CliRunner().invoke(cli, ["price", "@r10", "@r1", "@r20"])
        assert result.exit_code == 1
        out = result.stdout
        # Price guides contain their own blank lines, so assert order by
        # position: guide 10, then the inline error, then guide 20.
        markers = [
            out.index('Price Guide: @r10 "R10" by Unknown Artist'),
            out.index("USD 10.00"),
            out.index("\n\n✗ Price @r1 not found.\n  Try: agent-discogs search"),
            out.index('\n\nPrice Guide: @r20 "R20" by Unknown Artist'),
            out.index("USD 20.00"),
        ]
        assert markers == sorted(markers)
        assert result.stderr == ""

        data = json.loads(
            CliRunner().invoke(cli, ["price", "--json", "@r10", "@r1"]).stdout
        )
        assert data[0]["suggestions"] == {
            "Mint (M)": {"value": 10.0, "currency": "USD"}
        }
        assert data[1]["error"]["code"] == "not_found"

    def test_ref_cap(self) -> None:
        refs = [f"@r{n}" for n in range(11)]
        result = CliRunner().invoke(cli, ["get", "release", *refs])
        assert text_error(result) == "✗ At most 10 refs per command (11 given).\n"
        assert self.calls == []

    def test_no_ref_is_a_usage_error(self) -> None:
        result = CliRunner().invoke(cli, ["get", "release"])
        assert result.exit_code == 2
        assert result.stdout == ""
        assert result.stderr.endswith("\n\nError: Missing argument 'REF...'.\n")


class TestUnknownDates:
    """Discogs sends year 0 / `"0"` for unknown and pads unknown date parts
    with `-00`; neither may reach an agent."""

    @pytest.fixture(autouse=True)
    def _patch_clients(
        self, monkeypatch: pytest.MonkeyPatch, use_client: UseClient
    ) -> None:
        release = Release.model_validate(
            {"id": 1, "title": "R", "year": 0, "released": "1994-05-00"}
        )
        master = Master.model_validate({"id": 3, "title": "M", "year": 0})
        client = fake_client(
            releases_get=lambda _id: release, masters_get=lambda _id: master
        )
        use_client(client)
        item = SearchResult.model_validate(
            {"id": 10, "type": "release", "title": "R", "year": "0"}
        )

        def page(*_a: object, **_kw: object) -> PageResult:
            return PageResult(items=[item], page=1, total_items=1, total_pages=1)

        monkeypatch.setattr("agent_discogs.commands.search.fetch_page", page)
        monkeypatch.setattr("agent_discogs.pagination.fetch_page", page)

    def test_year_zero_is_omitted_from_text(self) -> None:
        release = CliRunner().invoke(cli, ["get", "release", "@r1"])
        assert release.stdout.splitlines()[0] == '@r1 [release] "R" by Unknown Artist'
        master = CliRunner().invoke(cli, ["get", "master", "@m3"])
        assert master.stdout.splitlines()[0] == '@m3 [master] "M" by Unknown Artist'
        search = CliRunner().invoke(cli, ["search", "release", "R"])
        assert search.stdout.splitlines()[2] == '@r10 "R"'

    def test_unknown_parts_are_omitted_from_json(self) -> None:
        def run(*args: str) -> object:
            return json.loads(CliRunner().invoke(cli, list(args)).stdout)

        assert run("get", "--json", "release", "@r1") == {
            "ref": "@r1",
            "title": "R",
            "released": "1994-05",
        }
        assert run("get", "--json", "master", "@m3") == {"ref": "@m3", "title": "M"}
        assert run("search", "--json", "R") == {
            "pagination": {
                "page": 1,
                "total_items": 1,
                "total_pages": 1,
                "filtered": True,
                "capped": False,
                "next_cursor": None,
            },
            "results": [{"ref": "@r10", "type": "release", "title": "R"}],
        }


class TestFormatEntries:
    """Each format entry keeps its quantity, descriptions, and free text, and
    entries stay separate: a double LP plus a CD is not one flat list."""

    @pytest.fixture(autouse=True)
    def _patch_get(self, use_client: UseClient) -> None:
        release = Release.model_validate(
            {
                "id": 1,
                "title": "R",
                "formats": [
                    {
                        "name": "Vinyl",
                        "qty": "2",
                        "descriptions": ["LP", "Album"],
                        "text": "180g, Gatefold",
                    },
                    {"name": "CD", "qty": "1", "descriptions": ["Album"]},
                    {"name": "All Media", "qty": "1", "text": "Box"},
                    {"name": "Box Set", "qty": "1"},
                ],
            }
        )
        use_client(fake_client(releases_get=lambda _id: release))

    expected = (
        "2×Vinyl (LP, Album, 180g, Gatefold); "  # noqa: RUF001
        "CD (Album); All Media (Box); Box Set"
    )

    def test_text(self) -> None:
        output = CliRunner().invoke(cli, ["get", "release", "@r1"]).stdout
        assert f"Format: {self.expected}" in output.splitlines()

    def test_json(self) -> None:
        result = CliRunner().invoke(cli, ["get", "--json", "release", "@r1"])
        assert json.loads(result.stdout)["formats"] == self.expected


class TestDateAdded:
    """`date_added` is when the release joined Discogs; shown in its own offset."""

    @pytest.fixture(autouse=True)
    def _patch_get(self, use_client: UseClient) -> None:
        base = {"title": "R", "country": "US", "released": "1994-03-08"}
        releases = {
            # 23:30 at -07:00 is already 2026-10-04 in UTC.
            1: Release.model_validate(
                {**base, "id": 1, "date_added": "2026-10-03T23:30:00-07:00"}
            ),
            2: Release.model_validate({**base, "id": 2}),
        }
        use_client(fake_client(releases_get=releases.__getitem__))

    def test_text_shows_the_date_beside_the_release_date(self) -> None:
        output = CliRunner().invoke(cli, ["get", "release", "@r1"]).stdout
        assert (
            "Country: US · Released: 1994-03-08 · Added: 2026-10-03"
            in output.splitlines()
        )

    def test_json_carries_rfc3339_timestamp(self) -> None:
        result = CliRunner().invoke(cli, ["get", "--json", "release", "@r1"])
        assert json.loads(result.stdout)["added"] == "2026-10-03T23:30:00-07:00"

    def test_missing_date_is_omitted(self) -> None:
        output = CliRunner().invoke(cli, ["get", "release", "@r2"]).stdout
        assert "Country: US · Released: 1994-03-08" in output.splitlines()
        result = CliRunner().invoke(cli, ["get", "--json", "release", "@r2"])
        data = json.loads(result.stdout)
        assert data["released"] == "1994-03-08"
        assert "added" not in data


def _suite_release() -> Release:
    """The #29 probe as a real SDK model: index track "Suite" with two parts."""
    return Release.model_validate(
        {
            "id": 1,
            "title": "Suites",
            "tracklist": [
                {
                    "position": "1",
                    "title": "Suite",
                    "duration": "",
                    "type_": "index",
                    "sub_tracks": [
                        {
                            "position": "1.a",
                            "title": "Part A",
                            "duration": "3:00",
                            "type_": "track",
                        },
                        {
                            "position": "1.b",
                            "title": "Part B",
                            "duration": "4:00",
                            "type_": "track",
                        },
                    ],
                },
                {
                    "position": "2",
                    "title": "Solo",
                    "duration": "5:00",
                    "type_": "track",
                },
            ],
        }
    )


class TestIndexTracks:
    """Index tracks (suites, medleys) carry their parts in `sub_tracks`."""

    @pytest.fixture(autouse=True)
    def _patch_get(self, use_client: UseClient) -> None:
        release = _suite_release()
        use_client(fake_client(releases_get=lambda _id: release))

    def test_text_tracklist_indents_sub_tracks(self) -> None:
        result = CliRunner().invoke(cli, ["get", "tracklist", "@r1"])
        assert result.exit_code == 0
        assert result.stdout.splitlines()[2:] == [
            "  1. Suite",
            "    1.a. Part A (3:00)",
            "    1.b. Part B (4:00)",
            "  2. Solo (5:00)",
        ]

    def test_json_tracklist_nests_sub_tracks_under_index(self) -> None:
        result = CliRunner().invoke(cli, ["get", "--json", "tracklist", "@r1"])
        assert result.exit_code == 0
        assert json.loads(result.stdout)["tracklist"] == [
            {
                "pos": "1",
                "title": "Suite",
                "type": "index",
                "sub_tracks": [
                    {"pos": "1.a", "title": "Part A", "duration": "3:00"},
                    {"pos": "1.b", "title": "Part B", "duration": "4:00"},
                ],
            },
            {"pos": "2", "title": "Solo", "duration": "5:00"},
        ]

    def test_compact_counts_leaf_tracks_and_sums_their_durations(self) -> None:
        text = CliRunner().invoke(cli, ["get", "release", "-c", "@r1"])
        assert text.exit_code == 0
        assert "Tracks: 3 (12:00)" in text.stdout
        data = json.loads(
            CliRunner().invoke(cli, ["get", "--json", "release", "-c", "@r1"]).stdout
        )
        assert data["tracks"] == "3 (12:00)"


def _credited_release(release_credits: list[dict[str, object]]) -> Release:
    """A real SDK release whose tracks and sub-tracks carry their own credits.

    Track 1 credits Bob (Guitar) and Alan Moulder (Engineer); sub-track 2.a of
    the index track "Suite" credits Bob (Guitar), Ann (Mixed By), and Flood
    (Producer).
    """

    def credit(artist_id: int, name: str, role: str) -> dict[str, object]:
        return {"id": artist_id, "name": name, "role": role, "tracks": ""}

    return Release.model_validate(
        {
            "id": 1,
            "title": "Session",
            "extraartists": release_credits,
            "tracklist": [
                {
                    "position": "1",
                    "title": "One",
                    "type_": "track",
                    "extraartists": [
                        credit(2, "Bob", "Guitar"),
                        credit(2768, "Alan Moulder", "Engineer"),
                    ],
                },
                {
                    "position": "",
                    "title": "Suite",
                    "type_": "index",
                    "sub_tracks": [
                        {
                            "position": "2.a",
                            "title": "Part A",
                            "type_": "track",
                            "extraartists": [
                                credit(2, "Bob", "Guitar"),
                                credit(3, "Ann", "Mixed By"),
                                credit(20661, "Flood", "Producer"),
                            ],
                        }
                    ],
                },
            ],
        }
    )


class TestTrackCredits:
    """Credits on tracks and sub-tracks appear wherever release credits do."""

    @pytest.fixture(autouse=True)
    def _patch_get(self, use_client: UseClient) -> None:
        self._use_client = use_client

    def _serve(self, release: object) -> None:
        self._use_client(fake_client(releases_get=lambda _id: release))

    def _invoke(self, *args: str) -> str:
        result = CliRunner().invoke(cli, list(args))
        assert result.exit_code == 0, result.output
        return result.stdout

    def test_track_only_credits_in_every_mode(self) -> None:
        release = _credited_release([])
        self._serve(release)
        roles = [
            "Engineer: Alan Moulder [@a2768] (1)",
            "Guitar: Bob [@a2] (1, 2.a)",
            "Mixed By: Ann [@a3] (2.a)",
            "Producer: Flood [@a20661] (2.a)",
        ]
        assert self._invoke("get", "credits", "@r1").splitlines() == [
            'Credits: @r1 "Session" (4)',
            "",
            *roles,
        ]
        assert "\n".join(roles) in self._invoke("get", "release", "-v", "@r1")

        by_role = {
            "Engineer": [{"ref": "@a2768", "name": "Alan Moulder", "tracks": "1"}],
            "Guitar": [{"ref": "@a2", "name": "Bob", "tracks": "1, 2.a"}],
            "Mixed By": [{"ref": "@a3", "name": "Ann", "tracks": "2.a"}],
            "Producer": [{"ref": "@a20661", "name": "Flood", "tracks": "2.a"}],
        }
        assert json.loads(self._invoke("get", "--json", "credits", "@r1")) == {
            "ref": "@r1",
            "credits": by_role,
        }
        verbose = json.loads(self._invoke("get", "--json", "release", "-v", "@r1"))
        assert verbose["credits"] == by_role

        one, suite = release.tracklist or []
        part = (suite.sub_tracks or [])[0]
        assert json.loads(
            self._invoke("get", "--json", "--full", "credits", "@r1")
        ) == {
            "credits": [],
            "track_credits": [
                {
                    "position": "1",
                    "title": "One",
                    "extra_artists": [c.model_dump() for c in one.extra_artists or []],
                },
                {
                    "position": "2.a",
                    "title": "Part A",
                    "extra_artists": [c.model_dump() for c in part.extra_artists or []],
                },
            ],
        }

    def test_release_and_track_credits_each_shown_once_with_scope(self) -> None:
        """A release-wide credit absorbs the same credit on a track; a scoped
        one gains the tracks that repeat it, listing each track once."""
        self._serve(
            _credited_release(
                [
                    {"id": 20661, "name": "Flood", "role": "Producer", "tracks": ""},
                    {
                        "id": 2768,
                        "name": "Alan Moulder",
                        "role": "Engineer",
                        "tracks": "1, 3",
                    },
                ]
            )
        )
        assert self._invoke("get", "credits", "@r1").splitlines() == [
            'Credits: @r1 "Session" (4)',
            "",
            "Engineer: Alan Moulder [@a2768] (1, 3)",
            "Guitar: Bob [@a2] (1, 2.a)",
            "Mixed By: Ann [@a3] (2.a)",
            "Producer: Flood [@a20661]",
        ]
        assert json.loads(self._invoke("get", "--json", "credits", "@r1"))[
            "credits"
        ] == {
            "Engineer": [{"ref": "@a2768", "name": "Alan Moulder", "tracks": "1, 3"}],
            "Guitar": [{"ref": "@a2", "name": "Bob", "tracks": "1, 2.a"}],
            "Mixed By": [{"ref": "@a3", "name": "Ann", "tracks": "2.a"}],
            "Producer": [{"ref": "@a20661", "name": "Flood"}],
        }


class _Masters:
    """`/releases/{id}` answers with its `master_id` from `releases` (None:
    no master), `/masters/{id}` with its `year` from `years` (1959 unless
    given; None: no year). A path in `status` answers with that status."""

    def __init__(
        self,
        releases: dict[int, int | None],
        years: dict[int, object] | None = None,
        status: dict[str, int] | None = None,
    ) -> None:
        self.releases, self.years = releases, years or {}
        self.status = status or {}

    def __call__(self, path: str, _params: dict[str, str]) -> object:
        if path in self.status:
            return httpx2.Response(self.status[path], json={"message": "Nope."})
        kind, _, number = path.strip("/").partition("/")
        entity_id = int(number)
        if kind == "releases":
            master_id = self.releases[entity_id]
            release = {"id": entity_id, "title": f"Release {entity_id}"}
            return release | ({"master_id": master_id} if master_id else {})
        year = self.years.get(entity_id, 1959)
        return {"id": entity_id, "title": "Kind Of Blue"} | (
            {} if year is None else {"year": year}
        )


class TestMasterYear:
    """`get release --master-year` reads the master after the release, one
    request shared with `get master`, and shows its year."""

    def _api(self, monkeypatch: pytest.MonkeyPatch, masters: _Masters) -> _FakeAPI:
        return _FakeAPI(monkeypatch, masters)

    @staticmethod
    def _paths(api: _FakeAPI) -> list[str]:
        return [path for path, _params in api.sends]

    def test_without_the_flag_only_the_release_is_read(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        api = self._api(monkeypatch, _Masters({5928185: 5460}))
        result = CliRunner().invoke(cli, ["get", "release", "@r5928185"])
        assert result.exit_code == 0, result.output
        assert api.sends == [("/releases/5928185", {})]
        assert "Master: @m5460" in result.stdout.splitlines()

    def test_the_master_year_costs_one_cached_request(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        api = self._api(monkeypatch, _Masters({5928185: 5460}))
        argv = ["get", "release", "@r5928185", "--master-year"]
        result = CliRunner().invoke(cli, argv)
        assert result.exit_code == 0, result.output
        assert self._paths(api) == ["/releases/5928185", "/masters/5460"]
        assert "Master: @m5460 (1959)" in result.stdout.splitlines()
        again = CliRunner().invoke(cli, argv)
        assert again.stdout == result.stdout
        assert len(api.sends) == 2  # the second run is all cache hits

    @pytest.mark.parametrize(
        ("flags", "master_sends"), [([], 1), (["--fresh"], 2)], ids=["cached", "fresh"]
    )
    def test_releases_of_one_master_share_its_request(
        self, monkeypatch: pytest.MonkeyPatch, flags: list[str], master_sends: int
    ) -> None:
        api = self._api(monkeypatch, _Masters({5928185: 5460, 1: 5460}))
        result = CliRunner().invoke(
            cli, [*flags, "get", "release", "@r5928185", "@r1", "--master-year"]
        )
        assert result.exit_code == 0, result.output
        assert self._paths(api).count("/masters/5460") == master_sends
        assert result.stdout.splitlines().count("Master: @m5460 (1959)") == 2

    def test_a_release_without_master_sends_nothing_more(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        api = self._api(monkeypatch, _Masters({7: None}))
        result = CliRunner().invoke(cli, ["get", "release", "@r7", "--master-year"])
        assert result.exit_code == 0, result.output
        assert self._paths(api) == ["/releases/7"]
        assert "Master:" not in result.stdout
        data = json.loads(
            CliRunner()
            .invoke(cli, ["get", "release", "@r7", "--master-year", "--json"])
            .stdout
        )
        assert "master_year" not in data
        assert "master_year_error" not in data

    @pytest.mark.parametrize("year", [0, None], ids=["zero", "absent"])
    def test_an_unknown_master_year_prints_no_year(
        self, monkeypatch: pytest.MonkeyPatch, year: object
    ) -> None:
        self._api(monkeypatch, _Masters({5928185: 5460}, years={5460: year}))
        result = CliRunner().invoke(
            cli, ["get", "release", "@r5928185", "--master-year"]
        )
        assert result.exit_code == 0, result.output
        assert "Master: @m5460" in result.stdout.splitlines()
        assert "✗" not in result.stdout
        data = json.loads(
            CliRunner()
            .invoke(cli, ["get", "release", "@r5928185", "--master-year", "--json"])
            .stdout
        )
        assert data["master"] == "@m5460"
        assert "master_year" not in data
        assert "master_year_error" not in data

    def test_a_deleted_master_is_reported_inline(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        masters = _Masters({5928185: 5460, 1: 6000}, status={"/masters/5460": 404})
        self._api(monkeypatch, masters)
        argv = ["get", "release", "@r5928185", "@r1", "--master-year"]
        result = CliRunner().invoke(cli, argv)
        assert result.exit_code == 0, result.output
        lines = result.stdout.splitlines()
        assert '@r5928185 [release] "Release 5928185" by Unknown Artist' in lines
        assert "Master: @m5460 · ✗ Master @m5460 not found." in lines
        assert "Master: @m6000 (1959)" in lines
        result = CliRunner().invoke(cli, [*argv, "--json"])
        assert result.exit_code == 0, result.output
        first, second = json.loads(result.stdout)
        assert first["title"] == "Release 5928185"
        assert first["master"] == "@m5460"
        assert "master_year" not in first
        assert first["master_year_error"]["code"] == "not_found"
        assert first["master_year_error"]["message"] == "Master @m5460 not found."
        assert second["master_year"] == 1959

    def test_a_cached_miss_on_the_master_is_reported_inline(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        api = self._api(monkeypatch, _Masters({5928185: 5460, 1: 6000}))
        # Cache both releases and one master, not the other.
        CliRunner().invoke(cli, ["get", "release", "@r5928185"])
        CliRunner().invoke(cli, ["get", "release", "@r1", "--master-year"])
        sent = len(api.sends)
        argv = ["--cached", "get", "release", "@r5928185", "@r1", "--master-year"]
        result = CliRunner().invoke(cli, argv)
        assert result.exit_code == 0, result.output
        assert len(api.sends) == sent
        lines = result.stdout.splitlines()
        assert '@r5928185 [release] "Release 5928185" by Unknown Artist' in lines
        assert "Master: @m5460 · ✗ Not cached: GET /masters/5460" in lines
        assert "Master: @m6000 (1959)" in lines
        result = CliRunner().invoke(cli, [*argv, "--json"])
        assert result.exit_code == 0, result.output
        first, second = json.loads(result.stdout)
        assert "master_year" not in first
        assert first["master_year_error"]["code"] == "cache_miss"
        assert second["master_year"] == 1959
        assert len(api.sends) == sent

    @pytest.mark.parametrize("status", [429, 500])
    def test_any_other_master_failure_fails_the_ref(
        self, monkeypatch: pytest.MonkeyPatch, status: int
    ) -> None:
        masters = _Masters({5928185: 5460, 1: 6000}, status={"/masters/5460": status})
        api = self._api(monkeypatch, masters)
        argv = ["get", "release", "@r5928185", "@r1", "--master-year"]
        result = CliRunner().invoke(cli, [*argv, "--json"])
        assert result.exit_code == 1
        first, second = json.loads(result.stdout)
        assert set(first) == {"ref", "error"}
        assert first["ref"] == "@r5928185"
        assert second["master_year"] == 1959
        # One send each: no retry.
        assert self._paths(api).count("/masters/5460") == 1
        result = CliRunner().invoke(cli, ["--fresh", *argv])
        assert result.exit_code == 1
        assert "Release 5928185" not in result.stdout
        assert result.stdout.startswith("✗ ")
        assert "Master: @m6000 (1959)" in result.stdout.splitlines()

    def test_a_missing_release_sends_no_master_request(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        masters = _Masters({5928185: 5460}, status={"/releases/5928185": 404})
        api = self._api(monkeypatch, masters)
        result = CliRunner().invoke(
            cli, ["get", "release", "@r5928185", "--master-year"]
        )
        assert "Release @r5928185 not found." in text_error(result)
        assert self._paths(api) == ["/releases/5928185"]

    def test_json_shapes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._api(monkeypatch, _Masters({5928185: 5460}))
        base = ["get", "release", "@r5928185", "--json"]
        data = json.loads(CliRunner().invoke(cli, [*base, "--master-year"]).stdout)
        keys = list(data)
        assert keys[keys.index("master") + 1] == "master_year"
        assert data["master_year"] == 1959
        full = json.loads(
            CliRunner().invoke(cli, [*base, "--full", "--master-year"]).stdout
        )
        assert full["master_id"] == 5460
        assert full["master_year"] == 1959
        assert "master_year" not in json.loads(CliRunner().invoke(cli, base).stdout)
        plain = json.loads(CliRunner().invoke(cli, [*base, "--full"]).stdout)
        assert "master_year" not in plain

    @pytest.mark.parametrize(
        "argv",
        [
            ["get", "master", "@m3719", "--master-year"],
            ["get", "versions", "@m3719", "--master-year"],
            ["get", "release", "@r1", "--master-year", "--max-requests", "2"],
        ],
        ids=["master", "versions", "max-requests"],
    )
    def test_rejected_where_it_does_not_apply(
        self, monkeypatch: pytest.MonkeyPatch, argv: list[str]
    ) -> None:
        api = self._api(monkeypatch, _Masters({1: 5460}))
        error = json_error(CliRunner().invoke(cli, [*argv, "--json"]))
        assert error["code"] == "invalid_argument"
        assert api.sends == []

    def test_rejection_message_on_get_master(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._api(monkeypatch, _Masters({}))
        result = CliRunner().invoke(cli, ["get", "master", "@m3719", "--master-year"])
        assert (
            "--master-year does not apply to 'get master'. "
            "'get master' takes only --json and --full."
        ) in text_error(result)
