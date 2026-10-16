"""Unit tests of `collection`: the `get collection` filter pattern
(`matcher`) and the user's own count on a list row (`user_stat`)."""

from __future__ import annotations

from typing import Any

import pytest
from discogs_sdk.models.artist import ArtistRelease
from discogs_sdk.models.master import MasterVersion

from agent_discogs.collection import matcher, user_stat


@pytest.mark.parametrize(
    ("pattern", "matches", "misses"),
    [
        # A plain value is a whole value, any case.
        ("techno", ["Techno", "TECHNO"], ["Tech", "Techno House", " Techno"]),
        # `*` at the start, middle and end, alone, and several.
        ("*bop", ["Hard Bop", "Bebop", "bop"], ["Bop Jazz"]),
        ("miles*", ["Miles Davis", "miles"], ["Davis, Miles"]),
        ("kind*blue", ["Kind of Blue", "KindBlue"], ["Kind of Blue Train"]),
        ("*", ["", "anything"], []),
        ("*live*at*", ["Live at Leeds", "Alive at Five", "LIVEAT"], ["Live in"]),
        ("**", ["", "x"], []),
        # `\*` is a literal asterisk wherever it is.
        ("\\*NSYNC", ["*NSYNC", "*nsync"], ["NSYNC", "XNSYNC"]),
        ("a\\*b", ["a*b"], ["ab", "axb"]),
        ("star\\*", ["Star*"], ["Star", "Stars"]),
        ("\\**", ["*NSYNC", "*"], ["NSYNC"]),
        # `\\` is a literal backslash; a trailing lone `\` is one too.
        ("a\\\\b", ["a\\b"], ["ab", "a\\\\b"]),
        ("back\\", ["back\\"], ["back"]),
        # `\` before an ordinary character is that character.
        ("\\Artist", ["Artist"], ["\\Artist"]),
        # `?` and `[` are literal.
        ("Are You Experienced?", ["are you experienced?"], ["Are You Experienced"]),
        ("[Untitled]", ["[Untitled]"], ["U", "Untitled", "t"]),
        ("?", ["?"], ["a"]),
        # Regular-expression metacharacters are literal.
        ("a.c", ["A.C"], ["abc"]),
        ("1+1", ["1+1"], ["11"]),
        ("(live)", ["(Live)"], ["live"]),
        ("$^|{2}", ["$^|{2}"], [""]),
        # Case folding, not lowercasing.
        ("STRASSE", ["Straße"], ["Strase"]),
        ("straße", ["STRASSE"], []),
        # Not accent-insensitive.
        ("bjork", [], ["Björk"]),
        ("bj*rk", ["Björk"], []),
        # A line break in a value is a character like any other.
        ("a*b", ["a\nb"], []),
    ],
)
def test_matcher(pattern: str, matches: list[str], misses: list[str]) -> None:
    match = matcher(pattern)
    assert [value for value in matches if match(value)] == matches
    assert [value for value in misses if match(value)] == []


def _stats(user: Any) -> dict[str, Any]:
    return {"community": {"in_collection": 9, "in_wantlist": 3}, "user": user}


def _artist_row(**fields: Any) -> ArtistRelease:
    return ArtistRelease.model_validate(
        {"id": 1, "title": "x", "type": "release", **fields}
    )


@pytest.mark.parametrize(
    ("user", "expected"),
    [
        ({"in_collection": 2, "in_wantlist": 1}, 2),
        ({"in_collection": 0, "in_wantlist": 1}, 0),
        ({"in_wantlist": 1}, None),
        (None, None),
    ],
)
def test_a_modeled_row(user: Any, expected: int | None) -> None:
    """A version row: the SDK models `stats` (`VersionStats`)."""
    row = MasterVersion.model_validate({"id": 1, "title": "x", "stats": _stats(user)})
    assert user_stat(row, "in_collection") == expected


@pytest.mark.parametrize(
    ("user", "expected"),
    [
        ({"in_collection": 2, "in_wantlist": 1}, 2),
        ({"in_collection": "2", "in_wantlist": 1}, None),
        ({"in_collection": True, "in_wantlist": 1}, None),
        ({"in_collection": 1.0, "in_wantlist": 1}, None),
        ({}, None),
        (None, None),
        ("2", None),
    ],
)
def test_a_row_with_plain_stats(user: Any, expected: int | None) -> None:
    """An artist or label row: the model declares no `stats`, so the field is
    the dict Discogs sent."""
    row = _artist_row(stats=_stats(user))
    assert isinstance((row.model_extra or {})["stats"], dict)
    assert user_stat(row, "in_collection") == expected


@pytest.mark.parametrize("stats", [None, "x", []])
def test_a_row_without_usable_stats(stats: Any) -> None:
    assert user_stat(_artist_row(stats=stats), "in_collection") is None


def test_a_row_without_stats() -> None:
    assert user_stat(_artist_row(), "in_collection") is None
