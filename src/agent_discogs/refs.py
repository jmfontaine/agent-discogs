"""Ref helpers — typed prefixes over Discogs IDs (@a3857, @r367113)."""

from __future__ import annotations

TYPE_TO_PREFIX = {
    "artist": "a",
    "label": "l",
    "master": "m",
    "release": "r",
}

PREFIX_TO_TYPE = {v: k for k, v in TYPE_TO_PREFIX.items()}


def make_ref(entity_type: str, entity_id: int) -> str:
    """Build a ref string from type and ID. e.g. ("artist", 3857) → "@a3857"."""
    prefix = TYPE_TO_PREFIX[entity_type]
    return f"@{prefix}{entity_id}"


def parse_ref(ref_string: str) -> tuple[str, int]:
    """Parse a ref string or raw ID into (type, id).

    Accepts:
        "@a3857"  → ("artist", 3857)
        "@r367113" → ("release", 367113)
        "367113"  → ("unknown", 367113)

    Raw numeric IDs return type "unknown" because the ID alone doesn't
    carry type information. This is intentional: AI agents may extract
    Discogs IDs from URLs or API responses without knowing our prefix
    convention. The caller (e.g. _resolve_ref) uses the command noun
    to determine the entity type instead.

    Raises ValueError on invalid format.
    """
    if ref_string.isdigit():
        return ("unknown", int(ref_string))

    if not ref_string.startswith("@"):
        raise ValueError(
            f"Invalid ref format: {ref_string}. Use @r12345 style or a numeric ID."
        )

    body = ref_string[1:]  # e.g. "a3857"
    if not body or body[0] not in PREFIX_TO_TYPE:
        raise ValueError(
            f"Invalid ref prefix in {ref_string}. "
            f"Valid prefixes: @a (artist), @r (release), @m (master), @l (label)."
        )

    prefix = body[0]
    id_str = body[1:]
    if not id_str.isdigit():
        raise ValueError(
            f"Invalid ref {ref_string}. Expected format: @a12345 (prefix + numeric ID)."
        )

    return (PREFIX_TO_TYPE[prefix], int(id_str))
