"""Doc examples must be real: every ref in the docs parses, sits next to prose
that does not contradict it, and (live) resolves to the entity the docs claim.

The docs once cited a master that 404s and a release that was a different
album. Agents copy these examples verbatim, so a stale ID sends them in
circles. The non-live checks run always; the live check is opt-in
(`just test-live`) and needs DISCOGS_TOKEN.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from agent_discogs import _HELP_TEXT
from agent_discogs.refs import parse_ref

ROOT = Path(__file__).resolve().parent.parent
DOC_FILES = [
    ROOT / "README.md",
    ROOT / "AGENTS.md",
    ROOT / "skills" / "agent-discogs" / "SKILL.md",
    *sorted((ROOT / "src" / "agent_discogs" / "skills").rglob("*.md")),
]

# Every ref that may appear in docs, and what it must resolve to. Adding a new
# example ID means adding it here, which is the point: examples are deliberate.
KNOWN_REFS: dict[str, tuple[str, str]] = {
    "@r847868": ("release", "The Downward Spiral"),
    "@r352665": ("release", "The Downward Spiral"),
    "@r20755": ("release", "Blue Monday"),
    "@m3719": ("master", "The Downward Spiral"),
    "@a3857": ("artist", "Nine Inch Nails"),
    "@l647": ("label", "Nothing Records"),
    "@a20661": ("artist", "Flood"),
}

# Entity names the docs mention, by kind. A ref whose ±CONTEXT-line window
# names a *different* entity of its own kind is a contradiction ("Pantera"
# next to a Nine Inch Nails release). Names near a ref of another kind are
# fine: a label name beside a release ref is normal.
_FAMILY = {"release": "title", "master": "title", "artist": "artist", "label": "label"}
VOCABULARY: dict[str, set[str]] = {
    "title": {
        "The Downward Spiral",
        "Pretty Hate Machine",
        "Blue Monday",
        "Plastic Dreams",
        "Closer To God",
        "Down In It",
        "Vulgar Display Of Power",
    },
    "artist": {"Nine Inch Nails", "New Order", "Trent Reznor", "Jaydee", "Pantera"},
    "label": {
        "Nothing Records",
        "Interscope Records",
        "Factory",
        "R & S Records",
        "PlayTime Records",
    },
}
CONTEXT = 5

_REF_RE = re.compile(r"@[a-z]?\d+")
# Placeholder refs in prose, e.g. "@r123" in the ref-mismatch example.
_PLACEHOLDER_IDS = {"123"}


def _sources() -> dict[str, list[str]]:
    sources: dict[str, list[str]] = {
        str(p.relative_to(ROOT)): p.read_text(encoding="utf-8").splitlines()
        for p in DOC_FILES
    }
    sources["_HELP_TEXT"] = list(str(_HELP_TEXT).splitlines())
    return sources


def _occurrences() -> list[tuple[str, str, str]]:
    """(location, ref, context) for every ref occurrence in the docs."""
    found = []
    for name, lines in _sources().items():
        for i, line in enumerate(lines):
            for ref in dict.fromkeys(_REF_RE.findall(line)):
                if ref.lstrip("@arml") in _PLACEHOLDER_IDS:
                    continue
                ctx = "\n".join(lines[max(0, i - CONTEXT) : i + CONTEXT + 1])
                found.append((f"{name}:{i + 1}", ref, ctx))
    return found


OCCURRENCES = _occurrences()


def test_docs_contain_refs() -> None:
    assert OCCURRENCES, "no refs found in docs; DOC_FILES is probably wrong"


@pytest.mark.parametrize(
    ("location", "ref", "context"),
    OCCURRENCES,
    ids=[f"{o[0]} {o[1]}" for o in OCCURRENCES],
)
def test_doc_ref_is_known_and_prose_agrees(
    location: str, ref: str, context: str
) -> None:
    assert ref in KNOWN_REFS, f"{ref} at {location} is not in KNOWN_REFS"
    entity_type, expected = KNOWN_REFS[ref]
    assert parse_ref(ref)[0] == entity_type, location

    family = _FAMILY[entity_type]
    named = {n for n in VOCABULARY[family] if n in context}
    assert named <= {expected}, (
        f"{ref} at {location} is {expected!r} but the surrounding text names "
        f"{sorted(named - {expected})}"
    )


@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("DISCOGS_TOKEN"), reason="needs DISCOGS_TOKEN")
@pytest.mark.parametrize(("ref", "expected"), sorted(KNOWN_REFS.items()))
def test_doc_ref_resolves_to_named_entity(ref: str, expected: tuple[str, str]) -> None:
    from agent_discogs.client import get_client

    entity_type, entity_id = parse_ref(ref)
    resource = getattr(get_client(), f"{entity_type}s")
    entity = resource.get(entity_id)
    name = entity.title if entity_type in ("release", "master") else entity.name
    assert entity_type == expected[0]
    assert name == expected[1], f"{ref} is {name!r}, docs say {expected[1]!r}"
