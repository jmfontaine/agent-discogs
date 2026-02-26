"""Tests for formatting functions."""

from __future__ import annotations

from types import SimpleNamespace

from agent_discogs.formatting import (
    format_artist,
    format_artist_releases,
    format_label,
    format_master,
    format_master_versions,
    format_price_guide,
    format_release,
    format_search_results,
    format_status,
    format_tracklist,
)


def _fake(**kwargs: object) -> SimpleNamespace:
    """Create a fake object with given attributes."""
    return SimpleNamespace(**kwargs)


class TestArtistString:
    def test_artist_with_join(self) -> None:
        from agent_discogs.formatting import _artist_string

        a1 = _fake(name="Trent Reznor", join="&")
        a2 = _fake(name="Atticus Ross", join=None)
        result = _artist_string([a1, a2])
        assert result == "Trent Reznor & Atticus Ross"

    def test_empty_artists(self) -> None:
        from agent_discogs.formatting import _artist_string

        assert _artist_string(None) == "Unknown Artist"
        assert _artist_string([]) == "Unknown Artist"


class TestLabelString:
    def test_no_catno(self) -> None:
        from agent_discogs.formatting import _label_string

        lbl = _fake(name="Nothing Records", catalog_number=None)
        assert _label_string([lbl]) == "Nothing Records"

    def test_empty_labels(self) -> None:
        from agent_discogs.formatting import _label_string

        assert _label_string(None) == ""
        assert _label_string([]) == ""


class TestTruncate:
    def test_long_text(self) -> None:
        from agent_discogs.formatting import _truncate

        text = "a " * 200  # 400 chars
        result = _truncate(text)
        assert result.endswith("...")
        assert len(result) <= 304  # 300 + "..."

    def test_short_text(self) -> None:
        from agent_discogs.formatting import _truncate

        assert _truncate("short") == "short"

    def test_none_text(self) -> None:
        from agent_discogs.formatting import _truncate

        assert _truncate(None) == ""

    def test_empty_text(self) -> None:
        from agent_discogs.formatting import _truncate

        assert _truncate("") == ""


class TestSearchResultType:
    def test_empty_type(self) -> None:
        from agent_discogs.formatting import _search_result_type

        r = _fake(type="")
        assert _search_result_type(r) == "release"

    def test_no_type_attr(self) -> None:
        from agent_discogs.formatting import _search_result_type

        class NoType:
            pass

        r = NoType()
        assert _search_result_type(r) == "release"


class TestUrlsShort:
    def test_url_without_scheme(self) -> None:
        from agent_discogs.formatting import _urls_short

        # No "//" means len(parts) == 1, so falls back to parts[0] (full string)
        assert _urls_short(["example.com/path"]) == "example.com/path"

    def test_non_string_url(self) -> None:
        from agent_discogs.formatting import _urls_short

        # Non-string triggers AttributeError → continue
        assert _urls_short([123]) == ""  # type: ignore[list-item]

    def test_empty_urls(self) -> None:
        from agent_discogs.formatting import _urls_short

        assert _urls_short(None) == ""
        assert _urls_short([]) == ""


class TestFormatSearchResults:
    def test_artist_search(self) -> None:
        results = [
            _fake(
                id=3857,
                type="artist",
                title="Nine Inch Nails",
                year=None,
                label=None,
                format=None,
            ),
            _fake(
                id=4779331,
                type="artist",
                title="Dave Heath",
                year=None,
                label=None,
                format=None,
            ),
        ]
        output = format_search_results(
            results=results,
            query="Nine Inch Nails",
            type_filter="artist",
            page=1,
            total_results=47,
            next_page_cmd='agent-discogs search artist "Nine Inch Nails" --page 2',
        )
        assert 'Search: artist "Nine Inch Nails"' in output
        assert "page 1, 2 of 47 results" in output
        assert '@a3857 [artist] "Nine Inch Nails"' in output
        assert '@a4779331 [artist] "Dave Heath"' in output
        assert "Next page:" in output

    def test_release_search(self) -> None:
        results = [
            _fake(
                id=367113,
                type="release",
                title="Nine Inch Nails - The Downward Spiral",
                year="1994",
                label=["Nothing Records"],
                format=["Vinyl", "LP"],
            ),
        ]
        output = format_search_results(
            results=results,
            query="The Downward Spiral",
            type_filter="release",
            page=1,
            total_results=312,
            next_page_cmd=None,
        )
        assert "@r367113 [release]" in output
        assert "(1994)" in output
        assert "Nothing Records" in output
        assert "Next page:" not in output

    def test_no_results(self) -> None:
        output = format_search_results(
            results=[],
            query="nonexistent",
            type_filter=None,
            page=1,
            total_results=0,
            next_page_cmd=None,
        )
        assert "0 of 0 results" in output


class TestFormatRelease:
    def test_full_release(self) -> None:
        rating = _fake(average=4.38, count=4521)
        community = _fake(rating=rating, have=89234, want=45678)
        label = _fake(name="Nothing Records", catalog_number="INT-92346")
        fmt = _fake(name="Vinyl", descriptions=["LP", "Album"])
        track1 = _fake(
            position="A1", title="Mr. Self Destruct", duration="4:09", type_=None
        )
        track2 = _fake(position="A2", title="Piggy", duration="4:24", type_=None)
        artist = _fake(name="Nine Inch Nails", join=None)

        release = _fake(
            id=367113,
            title="The Downward Spiral",
            year=1994,
            artists=[artist],
            community=community,
            labels=[label],
            formats=[fmt],
            genres=["Electronic", "Rock"],
            styles=["Industrial"],
            num_for_sale=1247,
            lowest_price=5.00,
            master_id=4917,
            tracklist=[track1, track2],
        )

        output = format_release(release)
        assert (
            '@r367113 [release] "The Downward Spiral" by Nine Inch Nails (1994)'
            in output
        )
        assert "Label: Nothing Records (INT-92346)" in output
        assert "Format: Vinyl, LP, Album" in output
        assert "Genres: Electronic, Rock" in output
        assert "Rating: 4.38/5 (4,521 votes)" in output
        assert "Have: 89,234" in output
        assert "Master: @m4917" in output
        assert "A1. Mr. Self Destruct (4:09)" in output

    def test_minimal_release(self) -> None:
        release = _fake(
            id=99999,
            title="Unknown",
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
        output = format_release(release)
        assert '@r99999 [release] "Unknown" by Unknown Artist' in output


class TestFormatArtist:
    def test_artist_with_urls(self) -> None:
        artist = _fake(
            id=3857,
            name="Nine Inch Nails",
            profile="Industrial rock band",
            urls=["https://nin.com", "https://en.wikipedia.org/wiki/Nine_Inch_Nails"],
            members=None,
        )
        output = format_artist(artist)
        assert '@a3857 [artist] "Nine Inch Nails"' in output
        assert "Profile: Industrial rock band" in output
        assert "nin.com" in output
        assert "en.wikipedia.org" in output


class TestFormatMaster:
    def test_master(self) -> None:
        artist = _fake(name="Nine Inch Nails", join=None)
        track = _fake(
            position="1", title="Mr. Self Destruct", duration="4:09", type_=None
        )
        master = _fake(
            id=4917,
            title="The Downward Spiral",
            year=1994,
            artists=[artist],
            genres=["Electronic", "Rock"],
            styles=["Industrial"],
            main_release=367113,
            num_for_sale=5476,
            lowest_price=0.36,
            tracklist=[track],
        )
        output = format_master(master)
        assert (
            '@m4917 [master] "The Downward Spiral" by Nine Inch Nails (1994)' in output
        )
        assert "Main release: @r367113" in output
        assert "1. Mr. Self Destruct (4:09)" in output


class TestFormatLabel:
    def test_label(self) -> None:
        sub = _fake(name="Nothing Studios")
        label = _fake(
            id=2919,
            name="Nothing Records",
            profile="Industrial label",
            urls=["https://nothing.com"],
            sub_labels=[sub],
        )
        output = format_label(label)
        assert '@l2919 [label] "Nothing Records"' in output
        assert "Profile: Industrial label" in output
        assert "Sub-labels: Nothing Studios" in output


class TestFormatTracklist:
    def test_tracklist(self) -> None:
        artist = _fake(name="Nine Inch Nails", join=None)
        tracks = [
            _fake(
                position="A1", title="Mr. Self Destruct", duration="4:09", type_=None
            ),
            _fake(position="A2", title="Piggy", duration="4:24", type_=None),
        ]
        release = _fake(
            id=367113,
            title="The Downward Spiral",
            year=1994,
            artists=[artist],
            tracklist=tracks,
        )
        output = format_tracklist(release)
        assert "Tracklist:" in output
        assert "A1. Mr. Self Destruct (4:09)" in output
        assert "A2. Piggy (4:24)" in output


class TestFormatPriceGuide:
    def test_price_guide(self) -> None:
        artist = _fake(name="Nine Inch Nails", join=None)
        release = _fake(
            id=367113, title="The Downward Spiral", year=1994, artists=[artist]
        )

        mint_price = _fake(value=150.00, currency="USD")
        nm_price = _fake(value=85.00, currency="USD")
        conditions = {
            "Mint (M)": mint_price,
            "Near Mint (NM or M-)": nm_price,
        }
        price_suggestions = _fake(conditions=conditions)

        lowest = _fake(value=5.00, currency="USD")
        marketplace_stats = _fake(num_for_sale=1247, lowest_price=lowest)

        output = format_price_guide(release, price_suggestions, marketplace_stats)
        assert "Price Guide:" in output
        assert "$150.00" in output
        assert "$85.00" in output
        assert "For sale: 1,247" in output
        assert "Lowest: $5.00" in output


class TestFormatArtistReleases:
    def test_artist_releases(self) -> None:
        releases = [
            _fake(
                id=4917,
                type="master",
                title="The Downward Spiral",
                year=1994,
                role="Main",
            ),
            _fake(
                id=367113,
                type="release",
                title="Pretty Hate Machine",
                year=1989,
                role="Main",
            ),
        ]
        output = format_artist_releases(
            releases,
            "@a3857",
            "Nine Inch Nails",
            page=1,
            total_results=342,
            next_page_cmd="agent-discogs get releases @a3857 --page 2",
        )
        assert 'Releases by @a3857 "Nine Inch Nails"' in output
        assert "@m4917 [master]" in output
        assert "@r367113 [release]" in output
        assert "Next page:" in output


class TestFormatMasterVersions:
    def test_master_versions(self) -> None:
        versions = [
            _fake(
                id=367113,
                released="1994",
                country="US",
                label="Nothing Records",
                catno="INT-92346",
                format="Vinyl, LP",
            ),
            _fake(
                id=9876543,
                released="2017",
                country="US",
                label="Interscope",
                catno="",
                format="Vinyl, 2xLP, 180g",
            ),
        ]
        output = format_master_versions(
            versions,
            "@m4917",
            "The Downward Spiral",
            page=1,
            total_results=498,
            next_page_cmd="agent-discogs get versions @m4917 --page 2",
        )
        assert 'Versions of @m4917 "The Downward Spiral"' in output
        assert "@r367113 [release]" in output
        assert "US" in output
        assert "Nothing Records INT-92346" in output
        assert "Next page:" in output


class TestFormatStatus:
    def test_authenticated(self) -> None:
        output = format_status("0.1.0", True)
        assert "v0.1.0" in output
        assert "token (authenticated)" in output

    def test_unauthenticated(self) -> None:
        output = format_status("0.1.0", False)
        assert "none (unauthenticated" in output

    def test_with_cache_dir(self) -> None:
        from pathlib import Path

        output = format_status("0.1.0", True, cache_dir=Path("/tmp/test-cache"))
        assert "Cache: /tmp/test-cache (1h TTL)" in output

    def test_without_cache_dir(self) -> None:
        output = format_status("0.1.0", True)
        assert "Cache:" not in output


class TestFormatReleaseVerbose:
    def test_verbose_with_notes(self) -> None:
        release = _fake(
            id=1,
            title="Album",
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
            notes="Pressed at Sterling Sound.",
        )
        output = format_release(release, verbose=True)
        assert "Notes: Pressed at Sterling Sound." in output

    def test_not_verbose_with_notes(self) -> None:
        release = _fake(
            id=1,
            title="Album",
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
            notes="Pressed at Sterling Sound.",
        )
        output = format_release(release)
        assert "Notes:" not in output

    def test_verbose_without_notes_attr(self) -> None:
        release = _fake(
            id=1,
            title="Album",
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
        output = format_release(release, verbose=True)
        assert "Notes:" not in output

    def test_verbose_empty_notes(self) -> None:
        release = _fake(
            id=1,
            title="Album",
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
            notes="   ",
        )
        output = format_release(release, verbose=True)
        assert "Notes:" not in output


class TestFormatArtistEdgeCases:
    def test_artist_with_members(self) -> None:
        m1 = _fake(name="Trent Reznor", active=True)
        m2 = _fake(name="Robin Finck", active=True)
        m3 = _fake(name="Chris Vrenna", active=False)
        artist = _fake(
            id=3857,
            name="Nine Inch Nails",
            profile=None,
            urls=None,
            members=[m1, m2, m3],
        )
        output = format_artist(artist)
        assert "Trent Reznor" in output
        assert "Robin Finck" in output
        assert "Chris Vrenna" not in output

    def test_artist_all_inactive_members(self) -> None:
        m1 = _fake(name="Gone", active=False)
        artist = _fake(id=1, name="Band", profile=None, urls=None, members=[m1])
        output = format_artist(artist)
        assert "Members:" not in output


class TestFormatTracklistEdgeCases:
    def test_heading_track(self) -> None:
        tracks = [
            _fake(position="", title="Side A", duration="", type_="heading"),
            _fake(position="A1", title="Song", duration="3:00", type_=None),
        ]
        release = _fake(id=1, title="Album", year=None, artists=None, tracklist=tracks)
        output = format_tracklist(release)
        assert "  Side A" in output
        assert "A1. Song (3:00)" in output

    def test_track_no_position(self) -> None:
        tracks = [
            _fake(position="", title="Untitled", duration="2:00", type_=None),
        ]
        release = _fake(id=1, title="Album", year=None, artists=None, tracklist=tracks)
        output = format_tracklist(release)
        assert "  Untitled (2:00)" in output
        assert "." not in output.split("Untitled")[0].split("\n")[-1]

    def test_empty_tracklist(self) -> None:
        release = _fake(id=1, title="Album", year=None, artists=None, tracklist=None)
        output = format_tracklist(release)
        assert "(no tracklist available)" in output

    def test_empty_list_tracklist(self) -> None:
        release = _fake(id=1, title="Album", year=None, artists=None, tracklist=[])
        output = format_tracklist(release)
        assert "(no tracklist available)" in output


class TestFormatMasterEdgeCases:
    def test_heading_track_in_master(self) -> None:
        artist = _fake(name="Artist", join=None)
        tracks = [
            _fake(position="", title="Side A", duration="", type_="heading"),
            _fake(position="1", title="Song", duration="3:00", type_=None),
        ]
        master = _fake(
            id=1,
            title="Album",
            year=None,
            artists=[artist],
            genres=None,
            styles=None,
            main_release=None,
            num_for_sale=None,
            lowest_price=None,
            tracklist=tracks,
        )
        output = format_master(master)
        assert "  Side A" in output
        assert "1. Song (3:00)" in output

    def test_track_no_position_in_master(self) -> None:
        artist = _fake(name="Artist", join=None)
        tracks = [
            _fake(position="", title="Untitled", duration="", type_=None),
        ]
        master = _fake(
            id=1,
            title="Album",
            year=None,
            artists=[artist],
            genres=None,
            styles=None,
            main_release=None,
            num_for_sale=None,
            lowest_price=None,
            tracklist=tracks,
        )
        output = format_master(master)
        assert "  Untitled" in output

    def test_market_without_lowest_price(self) -> None:
        artist = _fake(name="Artist", join=None)
        master = _fake(
            id=1,
            title="Album",
            year=None,
            artists=[artist],
            genres=None,
            styles=None,
            main_release=None,
            num_for_sale=100,
            lowest_price=None,
            tracklist=None,
        )
        output = format_master(master)
        assert "Market: 100 for sale" in output
        assert "from $" not in output


class TestFormatReleaseEdgeCases:
    def test_heading_track_in_release(self) -> None:
        tracks = [
            _fake(position="", title="Side A", duration="", type_="heading"),
            _fake(position="A1", title="Song", duration="3:00", type_=None),
        ]
        release = _fake(
            id=1,
            title="Album",
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
            tracklist=tracks,
        )
        output = format_release(release)
        assert "  Side A" in output
        assert "A1. Song (3:00)" in output

    def test_track_no_position_in_release(self) -> None:
        tracks = [
            _fake(position="", title="Untitled", duration="", type_=None),
        ]
        release = _fake(
            id=1,
            title="Album",
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
            tracklist=tracks,
        )
        output = format_release(release)
        assert "  Untitled" in output

    def test_market_without_lowest_price(self) -> None:
        release = _fake(
            id=1,
            title="Album",
            year=None,
            artists=None,
            community=None,
            labels=None,
            formats=None,
            genres=None,
            styles=None,
            num_for_sale=50,
            lowest_price=None,
            master_id=None,
            tracklist=None,
        )
        output = format_release(release)
        assert "Market: 50 for sale" in output
        assert "from $" not in output

    def test_community_no_rating(self) -> None:
        community = _fake(rating=None, have=100, want=50)
        release = _fake(
            id=1,
            title="Album",
            year=None,
            artists=None,
            community=community,
            labels=None,
            formats=None,
            genres=None,
            styles=None,
            num_for_sale=None,
            lowest_price=None,
            master_id=None,
            tracklist=None,
        )
        output = format_release(release)
        assert "Rating:" not in output
        assert "Have: 100" in output


class TestFormatPriceGuideEdgeCases:
    def test_callable_conditions(self) -> None:
        release = _fake(id=1, title="Album", year=None, artists=None)
        price = _fake(value=25.00)
        conditions_dict = {"Mint (M)": price}
        price_suggestions = _fake(conditions=lambda: conditions_dict)
        marketplace_stats = _fake(num_for_sale=None, lowest_price=None)

        output = format_price_guide(release, price_suggestions, marketplace_stats)
        assert "$25.00" in output

    def test_empty_conditions(self) -> None:
        release = _fake(id=1, title="Album", year=None, artists=None)
        price_suggestions = _fake(conditions={})
        marketplace_stats = _fake(num_for_sale=None, lowest_price=None)

        output = format_price_guide(release, price_suggestions, marketplace_stats)
        assert "No price suggestions available" in output

    def test_marketplace_no_lowest(self) -> None:
        release = _fake(id=1, title="Album", year=None, artists=None)
        price_suggestions = _fake(conditions={})
        marketplace_stats = _fake(num_for_sale=42, lowest_price=None)

        output = format_price_guide(release, price_suggestions, marketplace_stats)
        assert "For sale: 42" in output
        assert "Lowest:" not in output

    def test_marketplace_lowest_no_value(self) -> None:
        release = _fake(id=1, title="Album", year=None, artists=None)
        price_suggestions = _fake(conditions={})
        marketplace_stats = _fake(num_for_sale=10, lowest_price=_fake(value=None))

        output = format_price_guide(release, price_suggestions, marketplace_stats)
        assert "For sale: 10" in output
        assert "Lowest:" not in output


class TestTrackArtistPrefix:
    def test_track_with_artists(self) -> None:
        from agent_discogs.formatting import _track_artist_prefix

        track = _fake(
            artists=[_fake(name="Throbbing Gristle", join=None)],
        )
        assert _track_artist_prefix(track) == "Throbbing Gristle - "

    def test_track_with_multiple_artists(self) -> None:
        from agent_discogs.formatting import _track_artist_prefix

        track = _fake(
            artists=[
                _fake(name="Trent Reznor", join="&"),
                _fake(name="Atticus Ross", join=None),
            ],
        )
        assert _track_artist_prefix(track) == "Trent Reznor & Atticus Ross - "

    def test_track_without_artists(self) -> None:
        from agent_discogs.formatting import _track_artist_prefix

        track = _fake(position="A1", title="Song", duration="3:00", type_=None)
        assert _track_artist_prefix(track) == ""

    def test_track_with_empty_artists(self) -> None:
        from agent_discogs.formatting import _track_artist_prefix

        track = _fake(artists=[])
        assert _track_artist_prefix(track) == ""


class TestFormatTrackLines:
    def test_tracks_with_artists(self) -> None:
        from agent_discogs.formatting import _format_track_lines

        tracks = [
            _fake(
                position="A1",
                title="Hamburger Lady",
                duration="4:12",
                type_=None,
                artists=[_fake(name="Throbbing Gristle", join=None)],
            ),
            _fake(
                position="A2",
                title="Blue Monday",
                duration="7:29",
                type_=None,
                artists=[_fake(name="New Order", join=None)],
            ),
        ]
        lines = _format_track_lines(tracks)
        assert lines == [
            "  A1. Throbbing Gristle - Hamburger Lady (4:12)",
            "  A2. New Order - Blue Monday (7:29)",
        ]

    def test_tracks_without_artists(self) -> None:
        from agent_discogs.formatting import _format_track_lines

        tracks = [
            _fake(position="A1", title="Song", duration="3:00", type_=None),
        ]
        lines = _format_track_lines(tracks)
        assert lines == ["  A1. Song (3:00)"]

    def test_heading_ignores_artists(self) -> None:
        from agent_discogs.formatting import _format_track_lines

        tracks = [
            _fake(
                position="",
                title="Side A",
                duration="",
                type_="heading",
                artists=[_fake(name="Ignored", join=None)],
            ),
        ]
        lines = _format_track_lines(tracks)
        assert lines == ["  Side A"]


class TestFormatReleaseTrackArtists:
    def test_va_compilation(self) -> None:
        """VA compilation shows per-track artists."""
        va_artist = _fake(name="Various", join=None)
        track1 = _fake(
            position="A1",
            title="Hamburger Lady",
            duration="4:12",
            type_=None,
            artists=[_fake(name="Throbbing Gristle", join=None)],
        )
        track2 = _fake(
            position="A2",
            title="Blue Monday",
            duration="7:29",
            type_=None,
            artists=[_fake(name="New Order", join=None)],
        )
        release = _fake(
            id=452218,
            title="Some Bizzare Album",
            year=1981,
            artists=[va_artist],
            community=None,
            labels=None,
            formats=None,
            genres=None,
            styles=None,
            num_for_sale=None,
            lowest_price=None,
            master_id=None,
            tracklist=[track1, track2],
        )
        output = format_release(release)
        assert "A1. Throbbing Gristle - Hamburger Lady (4:12)" in output
        assert "A2. New Order - Blue Monday (7:29)" in output


class TestFormatTracklistTrackArtists:
    def test_va_tracklist(self) -> None:
        """format_tracklist shows per-track artists."""
        track = _fake(
            position="1",
            title="Warm Leatherette",
            duration="3:24",
            type_=None,
            artists=[_fake(name="The Normal", join=None)],
        )
        release = _fake(
            id=1,
            title="Comp",
            year=1980,
            artists=[_fake(name="Various", join=None)],
            tracklist=[track],
        )
        output = format_tracklist(release)
        assert "1. The Normal - Warm Leatherette (3:24)" in output


class TestFormatMasterTrackArtists:
    def test_va_master(self) -> None:
        """format_master shows per-track artists."""
        track = _fake(
            position="1",
            title="Warm Leatherette",
            duration="3:24",
            type_=None,
            artists=[_fake(name="The Normal", join=None)],
        )
        master = _fake(
            id=1,
            title="Comp",
            year=1980,
            artists=[_fake(name="Various", join=None)],
            genres=None,
            styles=None,
            main_release=None,
            num_for_sale=None,
            lowest_price=None,
            tracklist=[track],
        )
        output = format_master(master)
        assert "1. The Normal - Warm Leatherette (3:24)" in output


class TestFormatMasterVersionsEdgeCases:
    def test_version_no_next_page(self) -> None:
        versions = [
            _fake(
                id=1,
                released="",
                country="",
                label="",
                catno="",
                format="",
            ),
        ]
        output = format_master_versions(
            versions, "@m1", "Album", page=1, total_results=1, next_page_cmd=None
        )
        assert "Next page:" not in output

    def test_version_label_only(self) -> None:
        versions = [
            _fake(
                id=1,
                released="",
                country="",
                label="SomeLabel",
                catno="",
                format="",
            ),
        ]
        output = format_master_versions(
            versions, "@m1", "Album", page=1, total_results=1, next_page_cmd=None
        )
        assert "SomeLabel" in output


class TestFormatArtistReleasesEdgeCases:
    def test_release_no_year_no_role(self) -> None:
        releases = [
            _fake(id=1, type="release", title="Test", year=None, role=""),
        ]
        output = format_artist_releases(
            releases, "@a1", "Artist", page=1, total_results=1, next_page_cmd=None
        )
        assert "Next page:" not in output
        assert '@r1 [release] "Test"' in output
