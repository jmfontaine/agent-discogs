"""Relative Markdown links in the docs resolve: the file exists and, for a
`#anchor`, a heading of the target has that GitHub slug.

A statement kept in one place and linked from the others is only as good as
its links, so a renamed heading or a moved page fails here with the file and
line of each link it breaks. Absolute links (`https://...`) are not checked.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import pytest

from tests.conftest import DOC_FILES, REPO_ROOT

FILES = (*DOC_FILES, REPO_ROOT / "AGENTS.md")

_FENCE = re.compile(r"^\s*(```|~~~)")
_HEADING = re.compile(r"^#{1,6}\s+(.*?)\s*#*\s*$")
_CODE_SPAN = re.compile(r"(`+).*?\1")
_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


def _prose(path: Path) -> list[tuple[int, str]]:
    """The lines of `path` outside fenced code blocks, numbered from 1."""
    lines, fenced = [], False
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if _FENCE.match(line):
            fenced = not fenced
        elif not fenced:
            lines.append((number, line))
    return lines


def slug(heading: str) -> str:
    """GitHub's anchor for a heading: lowercase, every character but
    letters, digits, `_`, `-` and spaces dropped, spaces to hyphens."""
    return re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")


def anchors(path: Path) -> set[str]:
    """The anchors of the headings of `path`; a repeated slug gets `-1`,
    `-2`, ... after the first, as GitHub numbers them."""
    seen: Counter[str] = Counter()
    found = set()
    for _, line in _prose(path):
        heading = _HEADING.match(line)
        if heading:
            base = slug(heading[1])
            found.add(f"{base}-{seen[base]}" if seen[base] else base)
            seen[base] += 1
    return found


def links(path: Path) -> list[tuple[int, str]]:
    """The relative link targets of `path`, by line, code spans left out."""
    return [
        (number, target)
        for number, line in _prose(path)
        for target in _LINK.findall(_CODE_SPAN.sub("", line))
        if not re.match(r"[a-z][a-z0-9+.-]*:", target)
    ]


def broken(path: Path, root: Path = REPO_ROOT) -> list[str]:
    """Why each relative link of `path` does not resolve, each named by the
    path relative to `root`, the line and the target."""
    problems = []
    for number, target in links(path):
        file, _, anchor = target.partition("#")
        resolved = (path.parent / file).resolve() if file else path
        where = f"{path.relative_to(root)}:{number}: {target}"
        if not resolved.exists():
            problems.append(f"{where}: no such file")
        elif anchor and resolved.suffix == ".md" and anchor not in anchors(resolved):
            problems.append(f"{where}: no heading with this anchor")
    return problems


@pytest.mark.parametrize("path", FILES, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_relative_links_resolve(path: Path) -> None:
    assert broken(path) == []


@pytest.mark.parametrize(
    ("heading", "anchor"),
    [
        ("`--cached` and `--fresh`", "--cached-and---fresh"),
        ("Marking rows with `--owned`", "marking-rows-with---owned"),
        ("What commands cost", "what-commands-cost"),
        ("snake_case and Ünïcode", "snake_case-and-ünïcode"),
    ],
)
def test_slug(heading: str, anchor: str) -> None:
    assert slug(heading) == anchor


def test_a_renamed_heading_breaks_its_links(tmp_path: Path) -> None:
    page = tmp_path / "page.md"
    page.write_text("# Title\n\n## Old name\n\nSee [it](#old-name).\n")
    assert broken(page, tmp_path) == []
    page.write_text("# Title\n\n## New name\n\nSee [it](#old-name).\n")
    assert broken(page, tmp_path) == [
        "page.md:5: #old-name: no heading with this anchor"
    ]


def test_repeated_headings_and_code_are_handled(tmp_path: Path) -> None:
    page = tmp_path / "page.md"
    page.write_text(
        "## Notes\n\n## Notes\n\n```\n## Fenced\n[x](#fenced)\n```\n\n"
        "[a](#notes) [b](#notes-1) `[c](#nowhere)` [d](https://example.com/#x)\n"
        "[e](missing.md)\n"
    )
    assert broken(page, tmp_path) == ["page.md:11: missing.md: no such file"]
