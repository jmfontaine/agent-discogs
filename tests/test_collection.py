"""Tests for `collection.user_stat()`: the user's own count on a list row."""

from __future__ import annotations

from typing import Any

import pytest
from discogs_sdk.models.artist import ArtistRelease
from discogs_sdk.models.master import MasterVersion

from agent_discogs.collection import user_stat


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
