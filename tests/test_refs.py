"""Tests for ref parsing and formatting."""

from __future__ import annotations

import pytest

from agent_discogs.refs import make_ref, parse_ref


class TestMakeRef:
    def test_artist(self) -> None:
        assert make_ref("artist", 3857) == "@a3857"

    def test_release(self) -> None:
        assert make_ref("release", 367113) == "@r367113"

    def test_master(self) -> None:
        assert make_ref("master", 4917) == "@m4917"

    def test_label(self) -> None:
        assert make_ref("label", 281) == "@l281"


class TestParseRef:
    def test_artist_ref(self) -> None:
        assert parse_ref("@a3857") == ("artist", 3857)

    def test_release_ref(self) -> None:
        assert parse_ref("@r367113") == ("release", 367113)

    def test_master_ref(self) -> None:
        assert parse_ref("@m4917") == ("master", 4917)

    def test_label_ref(self) -> None:
        assert parse_ref("@l281") == ("label", 281)

    def test_raw_id(self) -> None:
        assert parse_ref("367113") == ("unknown", 367113)

    def test_invalid_no_at(self) -> None:
        with pytest.raises(ValueError, match="Invalid ref format"):
            parse_ref("badref")

    def test_invalid_prefix(self) -> None:
        with pytest.raises(ValueError, match="Invalid ref prefix"):
            parse_ref("@x123")

    def test_invalid_non_numeric(self) -> None:
        with pytest.raises(ValueError, match="Invalid ref"):
            parse_ref("@abc")


class TestRoundTrip:
    def test_roundtrip(self) -> None:
        for entity_type in ("artist", "release", "master", "label"):
            ref = make_ref(entity_type, 12345)
            parsed_type, parsed_id = parse_ref(ref)
            assert parsed_type == entity_type
            assert parsed_id == 12345
