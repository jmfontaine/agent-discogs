"""SDK models → the JSON an agent actually needs.

Each text view in `formatting.py` has a sibling projector here that returns
exactly the fields that view shows, plus refs. `--json` serialises these;
`--json --full` bypasses them for the raw SDK `model_dump()`. A raw release is
~33 KB of image URLs and bookkeeping; its projection is ~1.5 KB.
"""

from __future__ import annotations

from typing import Any

from agent_discogs.formatting import (
    _community_have,
    _format_string,
    _search_result_type,
    _truncate,
    _urls_short,
    credits_by_role,
)
from agent_discogs.refs import make_ref


def drop_empty(value: Any) -> Any:
    """Recursively drop None/""/[]/{} so consumers never null-check; 0/False stay."""
    if isinstance(value, dict):
        cleaned = {k: drop_empty(v) for k, v in value.items()}
        return {k: v for k, v in cleaned.items() if v not in (None, "", [], {})}
    if isinstance(value, list):
        return [drop_empty(v) for v in value]
    return value


def _artist_refs(artists: list[Any] | None) -> list[dict[str, Any]]:
    return [
        {
            "ref": make_ref("artist", a.id) if getattr(a, "id", None) else None,
            "name": getattr(a, "name", None),
            "join": getattr(a, "join", None),
        }
        for a in artists or []
    ]


def _tracks(tracklist: list[Any] | None) -> list[dict[str, Any]]:
    return [
        {
            "pos": getattr(t, "position", None),
            "title": getattr(t, "title", None),
            "duration": getattr(t, "duration", None),
            "type": getattr(t, "type_", None)
            if getattr(t, "type_", None) == "heading"
            else None,
            "artists": _artist_refs(getattr(t, "artists", None)),
        }
        for t in tracklist or []
    ]


def project_search_result(r: Any) -> dict[str, Any]:
    entity_type = _search_result_type(r)
    label = getattr(r, "label", None)
    master_id = getattr(r, "master_id", None)
    return drop_empty(
        {
            "ref": make_ref(entity_type, r.id),
            "type": entity_type,
            "title": r.title,
            "year": getattr(r, "year", None),
            "country": getattr(r, "country", None),
            "label": label[0] if label else None,
            "catno": getattr(r, "catalog_number", None),
            "format": getattr(r, "format", None),
            "have": _community_have(r),
            "master": make_ref("master", master_id)
            if entity_type == "release" and master_id
            else None,
        }
    )


def project_release(rel: Any, *, verbose: bool = False) -> dict[str, Any]:
    community = getattr(rel, "community", None)
    rating = getattr(community, "rating", None)
    master_id = getattr(rel, "master_id", None)
    data = {
        "ref": make_ref("release", rel.id),
        "title": rel.title,
        "artists": _artist_refs(getattr(rel, "artists", None)),
        "year": getattr(rel, "year", None),
        "released": getattr(rel, "released", None),
        "country": getattr(rel, "country", None),
        "labels": [
            {
                "ref": make_ref("label", lb.id) if getattr(lb, "id", None) else None,
                "name": getattr(lb, "name", None),
                "catno": getattr(lb, "catalog_number", None),
            }
            for lb in getattr(rel, "labels", None) or []
        ],
        "formats": _format_string(getattr(rel, "formats", None)),
        "genres": getattr(rel, "genres", None),
        "styles": getattr(rel, "styles", None),
        "master": make_ref("master", master_id) if master_id else None,
        "community": {
            "have": getattr(community, "have", None),
            "want": getattr(community, "want", None),
            "rating": getattr(rating, "average", None),
            "votes": getattr(rating, "count", None),
        },
        "market": {
            "for_sale": getattr(rel, "num_for_sale", None),
            "lowest": getattr(rel, "lowest_price", None),
        },
        "tracklist": _tracks(getattr(rel, "tracklist", None)),
    }
    if verbose:
        # Same facets the text view adds under -v: notes, credits, identifiers.
        data["notes"] = (getattr(rel, "notes", None) or "").strip()
        data["credits"] = project_credits(rel)
        data["identifiers"] = project_identifiers(rel)
    return drop_empty(data)


def project_credits(rel: Any) -> dict[str, list[dict[str, Any]]]:
    """`{role: [{ref, name, tracks}]}`, grouped exactly like the text view."""
    return drop_empty(
        {
            role: [
                {
                    "ref": make_ref("artist", c.id) if getattr(c, "id", None) else None,
                    "name": getattr(c, "name", None),
                    "tracks": getattr(c, "tracks", None),
                }
                for c in people
            ]
            for role, people in credits_by_role(rel).items()
        }
    )


def project_identifiers(rel: Any) -> list[dict[str, Any]]:
    return drop_empty(
        [
            {
                "type": getattr(i, "type", None),
                "value": getattr(i, "value", None),
                "description": getattr(i, "description", None),
            }
            for i in getattr(rel, "identifiers", None) or []
        ]
    )


def project_tracklist(rel: Any) -> dict[str, Any]:
    return drop_empty(
        {
            "ref": make_ref("release", rel.id),
            "title": getattr(rel, "title", None),
            "tracklist": _tracks(getattr(rel, "tracklist", None)),
        }
    )


def project_price(release: Any, suggestions: Any, stats: Any) -> dict[str, Any]:
    conditions: Any = getattr(suggestions, "conditions", None) or {}
    if not isinstance(conditions, dict):
        conditions = conditions()
    lowest = getattr(stats, "lowest_price", None)
    return drop_empty(
        {
            "ref": make_ref("release", release.id),
            "title": getattr(release, "title", None),
            "suggestions": {
                cond: getattr(price, "value", None)
                for cond, price in conditions.items()
            },
            "market": {
                "for_sale": getattr(stats, "num_for_sale", None),
                "lowest": getattr(lowest, "value", None),
                "currency": getattr(lowest, "currency", None),
            },
        }
    )


def project_artist(artist: Any) -> dict[str, Any]:
    """Text parity: active members only (format_artist hides former ones)."""
    return drop_empty(
        {
            "ref": make_ref("artist", artist.id),
            "name": artist.name,
            "profile": _truncate(getattr(artist, "profile", None)),
            "urls": _urls_short(getattr(artist, "urls", None)),
            "members": [
                {
                    "ref": make_ref("artist", m.id) if getattr(m, "id", None) else None,
                    "name": getattr(m, "name", None),
                }
                for m in getattr(artist, "members", None) or []
                if getattr(m, "active", True)
            ],
        }
    )


def project_label(label: Any) -> dict[str, Any]:
    return drop_empty(
        {
            "ref": make_ref("label", label.id),
            "name": label.name,
            "profile": _truncate(getattr(label, "profile", None)),
            "urls": _urls_short(getattr(label, "urls", None)),
            "sub_labels": [
                {
                    "ref": make_ref("label", s.id) if getattr(s, "id", None) else None,
                    "name": getattr(s, "name", None),
                }
                for s in getattr(label, "sub_labels", None) or []
            ],
        }
    )


def project_master(master: Any) -> dict[str, Any]:
    main = getattr(master, "main_release", None)
    return drop_empty(
        {
            "ref": make_ref("master", master.id),
            "title": master.title,
            "artists": _artist_refs(getattr(master, "artists", None)),
            "year": getattr(master, "year", None),
            "genres": getattr(master, "genres", None),
            "styles": getattr(master, "styles", None),
            "main_release": make_ref("release", main) if main else None,
            "market": {
                "for_sale": getattr(master, "num_for_sale", None),
                "lowest": getattr(master, "lowest_price", None),
            },
            "tracklist": _tracks(getattr(master, "tracklist", None)),
        }
    )


def project_master_version(ver: Any) -> dict[str, Any]:
    return drop_empty(
        {
            "ref": make_ref("release", ver.id),
            "title": getattr(ver, "title", None),
            "released": getattr(ver, "released", None),
            "country": getattr(ver, "country", None),
            "label": getattr(ver, "label", None),
            "catno": getattr(ver, "catalog_number", None),
            "format": getattr(ver, "format", None),
            "have": _community_have(ver),
        }
    )


def project_artist_release(rel: Any) -> dict[str, Any]:
    entity_type = getattr(rel, "type", "release")
    return drop_empty(
        {
            "ref": make_ref(entity_type, rel.id),
            "type": entity_type,
            "title": rel.title,
            "year": getattr(rel, "year", None),
            "role": getattr(rel, "role", None),
            "label": getattr(rel, "label", None),
            "format": getattr(rel, "format", None),
        }
    )
