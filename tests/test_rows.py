"""Tests for the row-reading helpers."""

from __future__ import annotations

import pytest

from agent_discogs.rows import artist_title, track_summary, urls_short
from tests.conftest import fake


class TestArtistTitle:
    """Rows built from looked-up releases are titled like search results."""

    def test_joins(self) -> None:
        release = fake(
            title="Coachella 2026",
            artists=[
                fake(name="Nine Inch Nails", join=","),
                fake(name="Boys Noize", join="&"),
                fake(name="Guest", join=""),
            ],
        )
        assert artist_title(release) == (
            "Nine Inch Nails, Boys Noize & Guest - Coachella 2026"
        )

    def test_no_artists_is_the_title(self) -> None:
        assert artist_title(fake(title="Untitled", artists=None)) == "Untitled"

    @pytest.mark.parametrize("title", [None, ""])
    def test_no_title_is_the_artist(self, title: str | None) -> None:
        """A record Discogs sent without a title is not titled `A - None`."""
        release = fake(title=title, artists=[fake(name="A", join="")])
        assert artist_title(release) == "A"

    @pytest.mark.parametrize("title", [None, ""])
    def test_neither_is_none(self, title: str | None) -> None:
        assert artist_title(fake(title=title, artists=None)) is None
        assert artist_title(fake(title=title, artists=[])) is None


class TestUrlsShort:
    def test_url_without_scheme(self) -> None:
        # No "//" means len(parts) == 1, so falls back to parts[0] (full string)
        assert urls_short(["example.com/path"]) == "example.com/path"

    def test_empty_urls(self) -> None:
        assert urls_short(None) == ""
        assert urls_short([]) == ""

    def test_urls_without_a_domain_are_skipped(self) -> None:
        assert urls_short(["", "https://", "https://www."]) == ""


class TestTrackSummary:
    def test_counts_tracks_and_sums_durations_past_an_hour(self) -> None:
        tracks = [
            fake(position="1", title="A", duration="30:00", type=None),
            fake(position="", title="Side B", duration="", type="heading"),
            fake(position="2", title="B", duration="35:01", type=None),
        ]
        assert track_summary(tracks) == "2 (65:01)"

    def test_any_unknown_duration_drops_the_total(self) -> None:
        """A partial sum would read as the release running time; count only."""
        tracks = [
            fake(position="1", title="A", duration="4:30", type=None),
            fake(position="2", title="B", duration="", type=None),
            fake(position="3", title="C", duration="?", type=None),
        ]
        assert track_summary(tracks) == "3"

    def test_hours_minutes_seconds_durations(self) -> None:
        tracks = [
            fake(position="1", title="A", duration="1:02:03", type=None),
            fake(position="2", title="B", duration="0:57", type=None),
        ]
        assert track_summary(tracks) == "2 (63:00)"

    def test_no_known_durations_gives_count_only(self) -> None:
        tracks = [fake(position="1", title="A", duration=None, type=None)]
        assert track_summary(tracks) == "1"

    def test_headings_only(self) -> None:
        assert track_summary([fake(title="Side A", duration="", type="heading")]) == "0"

    def test_index_track_counts_its_parts_and_sums_their_durations(self) -> None:
        suite = fake(
            title="Suite",
            duration="",
            type="index",
            sub_tracks=[
                fake(title="A", duration="3:00", type="track"),
                fake(title="B", duration="4:00", type="track"),
            ],
        )
        solo = fake(title="Solo", duration="5:00", type="track")
        assert track_summary([suite, solo]) == "3 (12:00)"

    def test_index_duration_stands_in_for_untimed_parts(self) -> None:
        """Discogs often times only the whole work; that is not a partial sum."""
        suite = fake(
            title="Suite",
            duration="10:00",
            type="index",
            sub_tracks=[
                fake(title="A", duration="", type="track"),
                fake(title="B", duration="", type="track"),
            ],
        )
        assert track_summary([suite]) == "2 (10:00)"

    def test_untimed_part_of_untimed_index_drops_the_total(self) -> None:
        suite = fake(
            title="Suite",
            duration="",
            type="index",
            sub_tracks=[
                fake(title="A", duration="3:00", type="track"),
                fake(title="B", duration="", type="track"),
            ],
        )
        assert track_summary([suite]) == "2"

    @pytest.mark.parametrize(
        "duration",
        ["4:²", "²", "1" * 4301 + ":00"],
        ids=["superscript", "bare-superscript", "too-many-digits"],
    )
    def test_digits_int_cannot_read_drop_the_total(self, duration: str) -> None:
        """`isdigit()` accepts `²` and any run of digits; `int()` does not."""
        tracks = [
            fake(position="1", title="X", duration=duration, type=None),
            fake(position="2", title="Y", duration="3:00", type=None),
        ]
        assert track_summary(tracks) == "2"

    @pytest.mark.parametrize(
        ("duration", "summary"),
        [("270", "1 (4:30)"), ("٤:٣٠", "1 (4:30)"), ("1_0:00", "1"), (" 4:30", "1")],
        ids=["bare-seconds", "arabic-indic", "underscore", "whitespace"],
    )
    def test_forms_read_as_before(self, duration: str, summary: str) -> None:
        tracks = [fake(position="1", title="A", duration=duration, type=None)]
        assert track_summary(tracks) == summary
