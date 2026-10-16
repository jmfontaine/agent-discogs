"""Unit tests of the `get collection` filter pattern (`collection.matcher`)."""

from __future__ import annotations

import pytest

from agent_discogs.collection import matcher


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
