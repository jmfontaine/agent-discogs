"""The task scenarios of the bundled skills: every Tasks entry's status, the
index of task IDs in `core`, and each working recipe run as written against
a small Discogs world, with the answer it promises and its request count.

An entry's cost formula is asserted on the world's sizes, so a recipe that
gets cheaper, or a flag it uses that disappears, fails the test named after
its task ID."""

from __future__ import annotations

import json
import random
import re
import shlex
from collections import Counter
from collections.abc import Callable
from typing import Any, NamedTuple

import pytest
from click.testing import CliRunner

from agent_discogs import cli
from tests.conftest import REPO_ROOT, SKILLS, _FakeAPI

_SKILLS_DIR = REPO_ROOT / "src" / "agent_discogs" / "skills"

# The skill each task belongs to.
SKILL_OF = {
    **dict.fromkeys(("A1", "A2", "A3", "A5", "A6", "E4", "F1", "F2"), "pressings"),
    **dict.fromkeys(
        ("A4", "B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8", "B9", "B10", "B11"),
        "collection",
    ),
    **dict.fromkeys(("B12", "C1", "C2", "C3", "D6", "F5", "G4"), "collection"),
    **dict.fromkeys(
        ("C4", "C5", "C6", "C7", "C8", "D1", "D2", "D3", "D4", "D5", "E1", "E2"),
        "marketplace",
    ),
    **dict.fromkeys(("E3", "E5", "H2"), "marketplace"),
    **dict.fromkeys(("F3", "F4", "G1", "G2", "G3", "H1", "I1", "I2"), "core"),
}
# The tasks nothing can do today; every other task has a recipe test below.
NOT_POSSIBLE = ("B10", "C7", "D3", "E3", "F4", "H1", "H2")
# What the docs write in place of a value the user supplies.
PLACEHOLDERS = {
    "<etching>": "INTD-92346",
    "<folder>": "CDs",
    "<seller>": "melomane",
    "<your username>": "me",
}

_HEADING = re.compile(r"^#{1,3} ")
_ENTRY = re.compile(r"^### ([A-Z]\d+) \S")
_STATUS = re.compile(r"^(works, costly|works|not possible)(?: \(([^()]+)\))?$")
# A `#` that starts an issue reference: not a heading, link anchor or code.
_ISSUE = re.compile(r"(?<![\w.#/-])#(\S*)")


class Task(NamedTuple):
    id: str
    skill: str
    location: str
    lines: list[str]

    @property
    def statuses(self) -> list[str]:
        line = next(line for line in self.lines if line.startswith("Status: "))
        return line.removeprefix("Status: ").split("; ")

    @property
    def commands(self) -> list[str]:
        """The `agent-discogs ...` lines of the entry's fenced blocks."""
        found, fenced = [], False
        for line in self.lines:
            if line.startswith("```"):
                fenced = not fenced
            elif fenced and line.startswith("agent-discogs "):
                found.append(line)
        return found


def _tasks() -> list[Task]:
    found = []
    for skill in SKILLS:
        root = _SKILLS_DIR / skill
        for path in [root / "SKILL.md", *sorted(root.glob("references/*.md"))]:
            lines = path.read_text(encoding="utf-8").splitlines()
            starts = [i for i, line in enumerate(lines) if _HEADING.match(line)]
            for start, end in zip(starts, [*starts[1:], len(lines)], strict=True):
                if match := _ENTRY.match(lines[start]):
                    location = f"{path.relative_to(_SKILLS_DIR)}:{start + 1}"
                    found.append(Task(match[1], skill, location, lines[start:end]))
    return found


TASKS = _tasks()
_BY_ID = {task.id: task for task in TASKS}

# Task ID: the statuses its entry gives, one per part. Filled by `recipe`.
STATUSES: dict[str, tuple[str, ...]] = dict.fromkeys(NOT_POSSIBLE, ("not possible",))


def recipe(task_id: str, *statuses: str) -> Callable[[Callable[..., None]], Any]:
    """Mark the test of task `task_id`'s recipe, whose entry gives `statuses`."""

    def mark(test: Callable[..., None]) -> Any:
        assert task_id not in STATUSES, task_id
        STATUSES[task_id] = statuses
        return test

    return mark


# The world. The entries' cost formulas are applied to its sizes.
COLLECTION = 150  # copies in the "All" folder: 2 pages of 100
WANTS = 120  # wants: 2 pages
LABEL_ROWS = 130  # rows of Nothing Records' catalogue: 2 pages
FLOOD_ROWS = 6_500  # rows of Flood's discography: 65 pages, 65 as Producer
LISTINGS = 120  # listings of any seller's inventory: 2 pages

TDS = "The Downward Spiral"
NIN = {"id": 3857, "name": "Nine Inch Nails", "join": ""}
NOTHING = {"id": 647, "name": "Nothing Records", "catno": "INT-92346"}
# The versions of master 3719; 900003 shares 847868's barcode.
VERSIONS = [847868, 352665, *range(900001, 900011)]
VINYL = {352665, 900002, 900004, 900006, 900008, 900010}
# Copy number: (release id, folder id). Every other copy n is release
# 2000 + n, in Uncategorized (1).
COPIES = {0: (847868, 5), 1: (352665, 1), 140: (847868, 1)}
FOLDERS = [
    {"id": 0, "name": "All", "count": COLLECTION},
    {"id": 1, "name": "Uncategorized", "count": COLLECTION - 1},
    {"id": 5, "name": "CDs", "count": 1},
]
FIELDS = [
    {"id": 1, "name": "Media Condition", "type": "dropdown", "position": 1},
    {"id": 2, "name": "Shelf", "type": "textarea", "position": 2},
]
# Want number: release id. Every other want n is release 4000 + n.
WANTED = {3: 352665, 110: 900003}
# Listing number: release id. Every other listing n is release 6000 + n, so
# listings 20 and 21 are two copies of release 6020.
LISTED = {7: 900003, 21: 6020, 50: 4050, 115: 4100}
GRADES = ["Mint (M)", "Near Mint (NM or M-)", "Very Good Plus (VG+)"]


def pages(items: int) -> int:
    """The API pages of 100 that hold `items`."""
    return -(-items // 100)


def copy_of(n: int) -> int:
    return COPIES.get(n, (2000 + n, 1))[0]


def want_of(n: int) -> int:
    return WANTED.get(n, 4000 + n)


def owned(release_id: int) -> int:
    return sum(copy_of(n) == release_id for n in range(COLLECTION))


def wanted(release_id: int) -> bool:
    return any(want_of(n) == release_id for n in range(WANTS))


def for_sale(release_id: int) -> int:
    return release_id % 4


def lowest(release_id: int) -> float:
    return 5.0 + release_id % 40


def _page(items: list[Any], params: dict[str, str], key: str) -> dict[str, Any]:
    page, per = int(params.get("page", 1)), int(params.get("per_page", 50))
    pagination = {
        "page": page,
        "pages": max(1, -(-len(items) // per)),
        "items": len(items),
        "per_page": per,
    }
    return {"pagination": pagination, key: items[(page - 1) * per : page * per]}


def _stats(release_id: int) -> dict[str, Any]:
    return {
        "community": {"in_collection": 900, "in_wantlist": 300},
        "user": {
            "in_collection": owned(release_id),
            "in_wantlist": int(wanted(release_id)),
        },
    }


def _tds(release_id: int) -> dict[str, Any]:
    """The release fields a collection record or a want carries."""
    return {
        "id": release_id,
        "title": TDS,
        "year": 2004 if release_id == 352665 else 1994,
        "artists": [NIN],
        "labels": [NOTHING],
        "formats": [{"name": "Vinyl" if release_id in VINYL else "CD"}],
        "genres": ["Electronic", "Rock"],
        "styles": ["Industrial"],
        "master_id": 3719,
    }


def label_of(n: int) -> dict[str, Any]:
    """The label of copy `n`: Nothing Records for The Downward Spiral and
    every third copy, else one of five others."""
    if copy_of(n) in VERSIONS or n % 3 == 0:
        return NOTHING
    return {"id": 700 + n % 5, "name": f"Label {n % 5}"}


def artist_of(n: int) -> dict[str, Any]:
    return (
        NIN if copy_of(n) in VERSIONS else {"id": 5000 + n % 40, "name": f"A{n % 40}"}
    )


def year_of(n: int) -> int:
    return _tds(copy_of(n))["year"] if copy_of(n) in VERSIONS else 1960 + n % 40


def jazz(n: int) -> bool:
    return copy_of(n) not in VERSIONS and n % 10 == 5


def _record(n: int) -> dict[str, Any]:
    """Copy `n`: one of the three Downward Spiral copies, else a vinyl LP
    by one of 40 artists, Jazz when n % 10 == 5, one year a copy from 1960."""
    release_id, folder = COPIES.get(n, (2000 + n, 1))
    info = (
        _tds(release_id)
        if release_id in VERSIONS
        else {
            "id": release_id,
            "title": f"Record {n}",
            "year": year_of(n),
            "artists": [{**artist_of(n), "join": ""}],
            "labels": [{**label_of(n), "catno": f"CAT{n}"}],
            "formats": [{"name": "Vinyl", "descriptions": ["LP"]}],
            "genres": ["Jazz"] if jazz(n) else ["Electronic"],
            "styles": ["Hard Bop"] if jazz(n) else ["House"],
        }
    )
    record: dict[str, Any] = {
        "id": release_id,
        "instance_id": 10_000 + n,
        "folder_id": folder,
        "rating": 0,
        "date_added": f"2026-{n % 9 + 1:02d}-01T10:00:00-07:00",
        "basic_information": info,
    }
    if n == 0:
        record["notes"] = [
            {"field_id": 1, "value": "Very Good Plus (VG+)"},
            {"field_id": 2, "value": "A3"},
        ]
    return record


def _want(n: int) -> dict[str, Any]:
    release_id = want_of(n)
    if release_id in VERSIONS:
        return {"id": release_id, "basic_information": _tds(release_id)}
    info = {
        "id": release_id,
        "title": f"Wanted {n}",
        "year": 1980,
        "artists": [{"id": 6000, "name": "Wanted Artist", "join": ""}],
        "genres": ["Jazz"],
    }
    return {"id": release_id, "basic_information": info}


def _release(release_id: int) -> dict[str, Any]:
    """`/releases/{id}`: a pressing of The Downward Spiral in full, any other
    release bare."""
    if release_id not in VERSIONS:
        return {"id": release_id, "title": f"Release {release_id}", "year": 1990}
    vinyl = release_id in VINYL
    return {
        **_tds(release_id),
        "country": "US",
        "formats": [
            {"name": "Vinyl", "qty": "2", "descriptions": ["LP", "Album"]}
            if vinyl
            else {"name": "CD", "qty": "1", "descriptions": ["Album"]}
        ],
        "notes": "Mastered at Future Disc.",
        "identifiers": [
            {"type": "Barcode", "value": "606949235024"},
            {"type": "Matrix / Runout", "value": f"INTD-92346 {release_id} TB"},
        ],
        "extraartists": [
            {"id": 20661, "name": "Flood", "role": "Producer"},
            {"id": 31, "name": "Tom Baker (2)", "role": "Mastered By"},
        ],
        "tracklist": [
            {"position": str(i), "title": f"Track {i}", "duration": "4:39"}
            for i in range(1, 15)
        ],
        "community": {"have": 7618, "want": 1355},
        "images": [{"type": "primary", "uri": f"https://img/{release_id}.jpg"}],
    }


def _search(params: dict[str, str]) -> dict[str, Any]:
    if params.get("type") == "master":
        rows = [{"id": 3719, "type": "master", "title": f"Nine Inch Nails - {TDS}"}]
        return _page(rows, params, "results")
    ids = [847868, 900003] if "barcode" in params else [847868, 352665]
    if "catno" in params:
        ids = [847868]
    rows = [
        {
            "id": i,
            "type": "release",
            "title": f"Nine Inch Nails - {TDS}",
            "year": "1994",
            "country": "US",
            "label": ["Nothing Records"],
            "catno": "INT-92346",
            "format": ["Vinyl", "LP"] if i in VINYL else ["CD", "Album"],
            "master_id": 3719,
            "community": {"have": 7618, "want": 1355},
        }
        for i in ids
    ]
    return _page(rows, params, "results")


def _version(release_id: int) -> dict[str, Any]:
    return {
        "id": release_id,
        "title": TDS,
        "released": "2004" if release_id == 352665 else "1994",
        "country": "US",
        "label": "Nothing Records",
        "catno": "INT-92346",
        "format": "Vinyl, LP, Album" if release_id in VINYL else "CD, Album",
        "stats": _stats(release_id),
    }


def _listing(n: int) -> dict[str, Any]:
    release_id = LISTED.get(n, 6000 + n)
    return {
        "id": 70_000 + n,
        "status": "For Sale",
        "condition": GRADES[n % 3],
        "sleeve_condition": GRADES[n % 3],
        "price": {"value": 20.0 + n % 30, "currency": "EUR"},
        "ships_from": "France",
        "posted": "2026-03-01T10:00:00-07:00",
        "release": {
            "id": release_id,
            "artist": "Artist",
            "title": f"Album {n}",
            "format": "LP, Album",
            "thumbnail": f"https://img/{release_id}-150.jpg",
        },
    }


def _flood(i: int) -> dict[str, Any]:
    role = "Producer" if i % 100 == 7 else "Mixed By"
    return {"id": 8000 + i, "type": "release", "title": f"Work {i}", "role": role}


def _catalogue(release_id: int) -> dict[str, Any]:
    return {
        "id": release_id,
        "title": TDS if release_id in VERSIONS else f"Catalogue {release_id}",
        "artist": "Nine Inch Nails",
        "catno": f"NOTHING {release_id}",
        "stats": _stats(release_id),
    }


_ME = "/users/me"
_FOLDER = re.compile(rf"{_ME}/collection/folders/(\d+)/releases")
_CATALOGUE = [847868, 352665, *range(3002, 3000 + LABEL_ROWS)]
_VALUE = {"minimum": "€310.00", "median": "€455.50", "maximum": "€1,020.00"}


def _account(path: str, params: dict[str, str]) -> object:
    """The token's user `me`: profile, collection and wantlist."""
    if path == "/oauth/identity":
        return {"id": 7, "username": "me"}
    if path == _ME:
        return {"id": 7, "username": "me", "curr_abbr": "EUR"}
    if path == f"{_ME}/collection/folders":
        return {"folders": FOLDERS}
    if path == f"{_ME}/collection/fields":
        return {"fields": FIELDS}
    if path == f"{_ME}/collection/value":
        return _VALUE
    if path == f"{_ME}/wants":
        return _page([_want(n) for n in range(WANTS)], params, "wants")
    folder = _FOLDER.fullmatch(path)
    assert folder, path
    records = [_record(n) for n in range(COLLECTION)]
    if folder_id := int(folder[1]):
        records = [r for r in records if r["folder_id"] == folder_id]
    return _page(records, params, "releases")


def world(path: str, params: dict[str, str]) -> object:
    """Discogs as `me` sees it: their account, The Downward Spiral's master
    3719 and its pressings, Flood's discography, Nothing Records' catalogue,
    and any seller's inventory, theirs included."""
    if path.endswith("/inventory"):
        return _page([_listing(n) for n in range(LISTINGS)], params, "listings")
    if path == "/oauth/identity" or path.startswith(_ME + "/") or path == _ME:
        return _account(path, params)
    if path == "/database/search":
        return _search(params)
    if path == "/masters/3719":
        return {"id": 3719, "title": TDS, "year": 1994, "artists": [NIN]}
    if path == "/masters/3719/versions":
        rows = [_version(i) for i in VERSIONS]
        if "format" in params:
            rows = [r for r in rows if params["format"] in r["format"].split(", ")]
        return _page(rows, params, "versions")
    if path == "/artists/20661":
        return {"id": 20661, "name": "Flood"}
    if path == "/artists/20661/releases":
        return _page([_flood(i) for i in range(FLOOD_ROWS)], params, "releases")
    if path == "/labels/647":
        return {"id": 647, "name": "Nothing Records"}
    if path == "/labels/647/releases":
        return _page([_catalogue(i) for i in _CATALOGUE], params, "releases")
    kind, _, number = path.rpartition("/")
    release_id = int(number)
    if kind == "/releases":
        return _release(release_id)
    if kind == "/marketplace/stats":
        currency = params.get("curr_abbr", "EUR")
        price = {"value": lowest(release_id), "currency": currency}
        return {
            "num_for_sale": for_sale(release_id) or None,
            "lowest_price": price if for_sale(release_id) else None,
            "blocked_from_sale": False,
        }
    assert kind == "/marketplace/price_suggestions", path
    values = (40.0, 32.0, 24.5)
    pairs = zip(GRADES, values, strict=True)
    return {grade: {"currency": "EUR", "value": value} for grade, value in pairs}


Sent = list[tuple[str, dict[str, str]]]


class Run(NamedTuple):
    stdout: str
    sent: Sent

    def row(self, ref: str) -> str:
        """The output line that starts with `ref`."""
        return next(line for line in self.stdout.splitlines() if line.startswith(ref))

    def refs(self) -> list[str]:
        """The ref starting each row, in order."""
        return [
            line.split()[0] for line in self.stdout.splitlines() if line[:2] == "@r"
        ]

    def json(self) -> Any:
        return json.loads(self.stdout)

    def continuation(self) -> list[str]:
        """The argv of the printed `Next page:` or `Continue scan:`."""
        line = next(
            line
            for line in self.stdout.splitlines()
            if line.startswith(("Next page:", "Continue scan:"))
        )
        return shlex.split(line.split(":", 1)[1])[1:]


class World:
    """The real SDK client, with a token and an in-memory cache, over
    `world`; `cold()` starts again with an empty cache."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._monkeypatch = monkeypatch
        monkeypatch.setenv("DISCOGS_TOKEN", "test-token")
        self.cold()

    def cold(self) -> None:
        self._api = _FakeAPI(self._monkeypatch, world)

    def run(self, argv: list[str]) -> Run:
        start = len(self._api.sends)
        result = CliRunner().invoke(cli, argv)
        assert result.exit_code == 0, result.output
        return Run(result.stdout, list(self._api.sends[start:]))


@pytest.fixture
def discogs(monkeypatch: pytest.MonkeyPatch) -> World:
    return World(monkeypatch)


def commands(task_id: str) -> list[list[str]]:
    """The argv of each command of the task's entry, its placeholders filled."""
    found = []
    for line in _BY_ID[task_id].commands:
        for placeholder, value in PLACEHOLDERS.items():
            line = line.replace(placeholder, value)  # noqa: PLW2901
        found.append(shlex.split(line)[1:])
    return found


def page(argv: list[str], number: int) -> list[str]:
    return [*argv, "--page", str(number)]


def paths(sent: Sent) -> list[str]:
    return [path for path, _ in sent]


# The entries.


def test_each_task_has_one_entry_in_its_skill() -> None:
    entries = Counter(task.id for task in TASKS)
    assert {i: n for i, n in entries.items() if n != 1} == {}
    assert {task.id: task.skill for task in TASKS} == SKILL_OF


def test_core_indexes_each_task_under_its_skill() -> None:
    text = (_SKILLS_DIR / "core" / "SKILL.md").read_text(encoding="utf-8")
    section = text.split("\n## Tasks\n", 1)[1].split("\n## ", 1)[0]
    index = {}
    for line in section.splitlines():
        if match := re.fullmatch(r"- (\w+)\b.*: ([A-Z]\d+(?:, [A-Z]\d+)*)", line):
            for task_id in match[2].split(", "):
                assert task_id not in index, f"{task_id} indexed twice"
                index[task_id] = match[1]
    assert index == SKILL_OF


@pytest.mark.parametrize("task", TASKS, ids=[t.id for t in TASKS])
def test_entry_states_its_status_and_what_would_change_it(task: Task) -> None:
    parts = [_STATUS.fullmatch(part) for part in task.statuses]
    assert all(parts), f"{task.location}: {task.statuses}"
    kinds = tuple(part[1] for part in parts if part)
    assert kinds == STATUSES[task.id], task.location
    if len(kinds) > 1:  # each part names what it covers
        assert all(part and part[2] for part in parts), task.location
    if {"works, costly", "not possible"} & set(kinds):
        assert any(line.startswith("Would need: ") for line in task.lines)
    if "not possible" in kinds:
        assert any(line.startswith("Why not") for line in task.lines)
    # A working part shows its commands and their cost; nothing else does.
    assert bool(task.commands) == any(k.startswith("works") for k in kinds)
    assert any(line.startswith("Cost: ") for line in task.lines) == bool(task.commands)


@pytest.mark.parametrize("task", TASKS, ids=[t.id for t in TASKS])
def test_entry_cites_issues_by_number(task: Task) -> None:
    for line in task.lines[1:]:
        for reference in _ISSUE.findall(line):
            assert re.fullmatch(r"\d+", reference), f"{task.location}: #{reference}"


def test_every_task_has_a_status() -> None:
    assert set(STATUSES) == set(SKILL_OF)


# The recipes, as the entries write them. pressings:


@recipe("A1", "works")
def test_a1_barcode_with_ownership_and_wants(discogs: World) -> None:
    (search,) = commands("A1")
    run = discogs.run(search)
    assert len(run.sent) == 1 + 1 + pages(COLLECTION) + pages(WANTS)
    assert run.row("@r847868").endswith("→ @m3719 · owned ×2 · not wanted")  # noqa: RUF001
    assert run.row("@r900003").endswith("→ @m3719 · not owned · wanted")


@recipe("A2", "works, costly")
def test_a2_runout_then_identifiers(discogs: World) -> None:
    search, identifiers = commands("A2")
    run = discogs.run(search)
    assert run.sent[0][1]["barcode"] == "INTD-92346"
    assert len(run.sent) == 1
    run = discogs.run(identifiers)
    assert len(run.sent) == 2  # 1 per candidate
    assert "Matrix / Runout: INTD-92346 352665 TB" in run.stdout


@recipe("A3", "works, costly")
def test_a3_versions_then_credits_and_runouts(discogs: World) -> None:
    versions, compare = commands("A3")
    run = discogs.run(versions)
    assert len(run.sent) == 2
    assert dict(run.sent)["/masters/3719/versions"]["sort"] == "released"
    assert run.stdout.count(" · have ") == len(VERSIONS)
    run = discogs.run(compare)
    assert len(run.sent) == 2  # 1 per release
    for line in ("Notes:", "Mastered By: Tom Baker (2)", "Matrix / Runout:"):
        assert run.stdout.count(line) == 2


@recipe("A5", "works")
def test_a5_catalog_number_on_a_label(discogs: World) -> None:
    (search,) = commands("A5")
    run = discogs.run(search)
    assert len(run.sent) == 1
    assert run.sent[0][1]["catno"] == "INT-92346"
    assert "Nothing Records INT-92346" in run.row("@r847868")


@recipe("A6", "works")
def test_a6_identifiers_and_notes(discogs: World) -> None:
    (get,) = commands("A6")
    run = discogs.run(get)
    assert len(run.sent) == 1
    assert "Notes: Mastered at Future Disc." in run.stdout
    assert "Barcode: 606949235024" in run.stdout


@recipe("E4", "works, costly")
def test_e4_one_search_per_barcode(discogs: World) -> None:
    (search,) = commands("E4")
    run = discogs.run(search)
    assert len(run.sent) == 1
    assert run.refs() == ["@r847868", "@r900003"]
    assert discogs.run(["--cached", *search]).sent == []


@recipe("F1", "works, costly")
def test_f1_track_counts_then_durations(discogs: World) -> None:
    search, compare, tracks = commands("F1")
    assert len(discogs.run(search).sent) == 1
    run = discogs.run(compare)
    assert len(run.sent) == 2  # 1 per candidate
    assert run.stdout.count("Tracks: 14 (65:06)") == 2
    run = discogs.run(tracks)
    assert run.sent == []
    assert "14. Track 14 (4:39)" in run.stdout


@recipe("F2", "works")
def test_f2_master_year(discogs: World) -> None:
    (get,) = commands("F2")
    run = discogs.run(get)
    assert paths(run.sent) == ["/releases/352665", "/masters/3719"]
    assert "Master: @m3719 (1994)" in run.stdout


# collection:


@recipe("A4", "works", "works, costly")
def test_a4_owned_pressings_then_their_price(discogs: World) -> None:
    owned_pressings, price = commands("A4")
    run = discogs.run(owned_pressings)
    assert len(run.sent) == 1 + pages(COLLECTION) + 1
    assert run.stdout.startswith("@m3719 owned ×3 in 2 releases\n")  # noqa: RUF001
    run = discogs.run(price)
    assert len(run.sent) == 3 * 2
    assert run.stdout.count("Very Good Plus (VG+):") == 2


@recipe("B1", "works")
def test_b1_records_by_genre_and_format(discogs: World) -> None:
    (scan,) = commands("B1")
    run = discogs.run(scan)
    assert len(run.sent) == 2 + pages(COLLECTION)
    refs = run.refs()
    assert refs == [f"@r{copy_of(n)}" for n in range(COLLECTION) if jazz(n)]


@recipe("B2", "works")
def test_b2_counts_by_decade(discogs: World) -> None:
    (count,) = commands("B2")
    run = discogs.run(count)
    assert len(run.sent) == 1 + pages(COLLECTION)
    decades = Counter(f"{year_of(n) // 10 * 10}s" for n in range(COLLECTION))
    for decade, records in decades.items():
        assert f"\n{decade}: {records}\n" in run.stdout


@recipe("B3", "works", "works, costly")
def test_b3_random_record(discogs: World, monkeypatch: pytest.MonkeyPatch) -> None:
    def last(population: range, k: int) -> list[int]:
        return list(population)[-k:]

    monkeypatch.setattr(random, "sample", last)
    anywhere, in_folder, in_genre = commands("B3")
    run = discogs.run(anywhere)  # the pick is on the last page
    assert len(run.sent) == 2 + 1 + 1
    assert run.row(f"@r{copy_of(COLLECTION - 1)} ")
    discogs.cold()
    run = discogs.run(in_folder)
    assert len(run.sent) == 2 + 1
    assert run.row("@r847868").endswith("folder CDs · added 2026-01-01")
    discogs.cold()
    run = discogs.run(in_genre)
    assert len(run.sent) == 2 + pages(COLLECTION)
    last_jazz = max(n for n in range(COLLECTION) if jazz(n))
    assert run.row(f"@r{copy_of(last_jazz)} ")


@recipe("B4", "works")
def test_b4_duplicates(discogs: World) -> None:
    releases, masters = commands("B4")
    run = discogs.run(releases)
    assert len(run.sent) == 2 + pages(COLLECTION)
    assert "owned ×2 · folders CDs, Uncategorized" in run.row("@r847868")  # noqa: RUF001
    run = discogs.run(masters)
    assert run.sent == []
    assert "\n@m3719 owned ×3 in 2 releases\n" in run.stdout  # noqa: RUF001


@recipe("B5", "works")
def test_b5_any_pressing_owned(discogs: World) -> None:
    search, owned_pressings = commands("B5")
    assert discogs.run(search).row("@m3719")
    run = discogs.run(owned_pressings)
    assert len(run.sent) == 1 + pages(COLLECTION) + 1
    assert run.stdout.startswith("@m3719 owned ×3 in 2 releases\n")  # noqa: RUF001


@recipe("B6", "works, costly")
def test_b6_export_with_genres_styles_and_year(discogs: World) -> None:
    (export,) = commands("B6")
    first = discogs.run(export)
    second = discogs.run(page(export, 2))
    assert len(first.sent) + len(second.sent) == 2 + pages(COLLECTION)
    rows = first.json()["results"] + second.json()["results"]
    assert len(rows) == COLLECTION
    assert rows[5]["genres"] == ["Jazz"]
    assert rows[5]["styles"] == ["Hard Bop"]
    assert rows[5]["year"] == year_of(5)


@recipe("B7", "works, costly")
def test_b7_backup_collection_and_wantlist(discogs: World) -> None:
    collection, wantlist = commands("B7")
    runs = [
        discogs.run(page(argv, n)) for argv in (collection, wantlist) for n in (1, 2)
    ]
    assert sum(len(run.sent) for run in runs) == 1 + pages(COLLECTION) + pages(WANTS)
    records = [r for run in runs[:2] for r in run.json()["results"]]
    assert [r["instance_id"] for r in records] == [
        10_000 + n for n in range(COLLECTION)
    ]
    assert sum(len(run.json()["results"]) for run in runs[2:]) == WANTS


@recipe("B8", "works")
def test_b8_shelf_from_custom_fields(discogs: World) -> None:
    (scan,) = commands("B8")
    run = discogs.run(scan)
    assert len(run.sent) == 3 + pages(COLLECTION)
    assert run.row("@r847868").endswith(
        "folder CDs · added 2026-01-01 · Media Condition: VG+ · Shelf: A3"
    )


@recipe("B9", "works")
def test_b9_recently_added(discogs: World) -> None:
    (newest,) = commands("B9")
    run = discogs.run(newest)
    assert len(run.sent) == 3
    assert run.sent[-1][1] | {"sort": "added", "sort_order": "desc"} == run.sent[-1][1]


@recipe("B11", "works")
def test_b11_sorted_by_artist(discogs: World) -> None:
    (sort,) = commands("B11")
    run = discogs.run(sort)
    assert len(run.sent) == 3
    assert run.sent[-1][1]["sort"] == "artist"  # Discogs does the ordering
    assert len(discogs.run(run.continuation()).sent) == 1


@recipe("B12", "works, costly")
def test_b12_mirror_keyed_on_instance_id(discogs: World) -> None:
    (mirror,) = commands("B12")
    first, second = discogs.run(mirror), discogs.run(page(mirror, 2))
    assert len(first.sent) + len(second.sent) == 1 + pages(COLLECTION)
    rows = first.json()["results"] + second.json()["results"]
    copies = [row for row in rows if row["id"] == 847868]
    assert [row["instance_id"] for row in copies] == [10_000, 10_140]


@recipe("C1", "works")
def test_c1_collection_value(discogs: World) -> None:
    (value,) = commands("C1")
    run = discogs.run(value)
    assert len(run.sent) == 3
    assert run.stdout == (
        "Collection value (EUR): min 310.00 · median 455.50 · max 1020.00\n"
    )


@recipe("C2", "not possible", "works, costly")
def test_c2_folder_estimate(discogs: World) -> None:
    folder, stats = commands("C2")
    run = discogs.run(folder)
    assert len(run.sent) == 3
    assert [row["ref"] for row in run.json()["results"]] == ["@r847868"]
    run = discogs.run(stats)
    assert len(run.sent) == 2 * 2
    assert run.row("@r352665").endswith("for sale: 1 from EUR 30.00")


@recipe("C3", "works, costly")
def test_c3_lowest_prices_in_dollars(discogs: World) -> None:
    collection, stats = commands("C3")
    assert len(discogs.run(collection).sent) == 2 + 1
    run = discogs.run(stats)
    assert len(run.sent) == 2 * 2
    assert {params.get("curr_abbr") for _, params in run.sent} == {None, "USD"}
    assert run.row("@r352665").endswith("for sale: 1 from USD 30.00")


@recipe("D6", "works, costly")
def test_d6_label_catalogue_marked_owned(discogs: World) -> None:
    (catalogue,) = commands("D6")
    first = discogs.run(catalogue)
    second = discogs.run(first.continuation())
    assert len(first.sent) + len(second.sent) == 1 + pages(LABEL_ROWS)
    assert first.row("@r847868").endswith("· owned ×2")  # noqa: RUF001
    assert first.row("@r3002").endswith("· not owned")


@recipe("F5", "works")
def test_f5_distinct_artists(discogs: World) -> None:
    (count,) = commands("F5")
    run = discogs.run(count)
    assert len(run.sent) == 1 + pages(COLLECTION)
    artists = {artist_of(n)["id"] for n in range(COLLECTION)}
    assert run.json()["distinct"] == len(artists)
    assert len(run.json()["counts"]) == 10


@recipe("G4", "works, costly")
def test_g4_top_label_then_what_is_missing(discogs: World) -> None:
    count, catalogue = commands("G4")
    run = discogs.run(count)
    assert len(run.sent) == 1 + pages(COLLECTION)
    on_nothing = sum(label_of(n) == NOTHING for n in range(COLLECTION))
    assert f"\nNothing Records [@l647]: {on_nothing}\n" in run.stdout
    run = discogs.run(catalogue)
    assert len(run.sent) == 1 + 1
    assert run.row("@r3002").endswith("· not owned")


# marketplace:


@recipe("C4", "works")
def test_c4_suggested_price_per_grade(discogs: World) -> None:
    (price,) = commands("C4")
    run = discogs.run(price)
    assert len(run.sent) == 3
    assert "Very Good Plus (VG+):          EUR 24.50" in run.stdout


@recipe("C5", "works")
def test_c5_for_sale_count_and_lowest_price(discogs: World) -> None:
    (stats,) = commands("C5")
    run = discogs.run(stats)
    assert len(run.sent) == 2
    assert run.row("@r847868").endswith("want 1,355 · for sale: 0")


@recipe("C6", "works, costly")
def test_c6_cheapest_pressing(discogs: World) -> None:
    (versions,) = commands("C6")
    run = discogs.run(versions)
    assert len(run.sent) == 2 + len(VINYL)
    for release_id in VINYL:
        sale = for_sale(release_id)
        tail = (
            f"for sale: {sale} from EUR {lowest(release_id):.2f}"
            if sale
            else "for sale: 0"
        )
        assert run.row(f"@r{release_id}").endswith(tail)


@recipe("C8", "works", "works, costly")
def test_c8_have_and_want(discogs: World) -> None:
    one, collection, several = commands("C8")
    run = discogs.run(one)
    assert len(run.sent) == 2
    assert "have 7,618 · want 1,355" in run.row("@r847868")
    assert len(discogs.run(collection).sent) == 2 + 1
    run = discogs.run(several)
    assert paths(run.sent) == ["/releases/352665", "/marketplace/stats/352665"]


@recipe("D1", "works", "works, costly")
def test_d1_wantlist_then_rows_to_filter(discogs: World) -> None:
    listing, full = commands("D1")
    run = discogs.run(listing)
    assert len(run.sent) == 1 + 1
    assert run.row("@r352665").startswith('@r352665 "Nine Inch Nails - The Downward')
    run = discogs.run(full)
    assert run.sent == []  # the same page, cached
    assert run.json()["results"][0]["basic_information"]["genres"] == ["Jazz"]
    assert len(discogs.run(page(full, 2)).sent) == pages(WANTS) - 1


@recipe("D2", "works, costly", "not possible")
def test_d2_wants_under_a_price(discogs: World) -> None:
    (check,) = commands("D2")
    run = discogs.run(check)
    kept, checked = [], 0
    for n in range(WANTS):
        if len(kept) == 5:
            break
        checked += 1
        release_id = want_of(n)
        if for_sale(release_id) and lowest(release_id) <= 25:
            kept.append(f"@r{release_id}")
    assert len(run.sent) == 1 + 1 + checked
    assert run.refs() == kept


@recipe("D4", "works, costly", "not possible")
def test_d4_one_seller_and_my_wants(discogs: World) -> None:
    (inventory,) = commands("D4")
    run = discogs.run(inventory)
    assert len(run.sent) == 1 + pages(WANTS) + pages(LISTINGS)
    listed = [LISTED.get(n, 6000 + n) for n in range(LISTINGS)]
    assert run.refs() == [f"@r{release}" for release in listed if wanted(release)]


@recipe("D5", "works")
def test_d5_barcode_on_my_wantlist(discogs: World) -> None:
    (search,) = commands("D5")
    run = discogs.run(search)
    assert len(run.sent) == 1 + 1 + pages(WANTS)
    assert run.row("@r900003").endswith("· wanted")
    assert run.row("@r847868").endswith("· not wanted")


@recipe("E1", "works, costly", "not possible")
def test_e1_listings_then_suggestions(discogs: World) -> None:
    inventory, price = commands("E1")
    run = discogs.run(inventory)
    assert paths(run.sent) == ["/users/me/inventory"]
    row = run.json()["results"][2]
    assert (row["price"], row["condition"]) == (22.0, "VG+")
    assert len(discogs.run(price).sent) == 3 * 2


@recipe("E2", "works", "works, costly")
def test_e2_thumbnails_then_full_images(discogs: World) -> None:
    inventory, release = commands("E2")
    run = discogs.run(inventory)
    assert len(run.sent) == 1
    thumbnails = [row["release"]["thumbnail"] for row in run.json()["results"]]
    assert thumbnails[0] == "https://img/6000-150.jpg"
    run = discogs.run(release)
    assert len(run.sent) == 1
    assert run.json()["images"][0]["uri"] == "https://img/847868.jpg"


@recipe("E5", "works, costly")
def test_e5_duplicate_listings(discogs: World) -> None:
    (inventory,) = commands("E5")
    runs = [discogs.run(inventory), discogs.run(page(inventory, 2))]
    assert sum(len(run.sent) for run in runs) == pages(LISTINGS)
    refs = Counter(row["ref"] for run in runs for row in run.json()["results"])
    assert [ref for ref, n in refs.items() if n > 1] == ["@r6020"]


# core:


@recipe("F3", "works")
def test_f3_names_as_sent(discogs: World) -> None:
    (names,) = commands("F3")
    run = discogs.run(names)
    assert len(run.sent) == 1
    assert "Mastered By: Tom Baker (2) [@a31]" in run.stdout


@recipe("G1", "works, costly")
def test_g1_produced_and_engineered(discogs: World) -> None:
    produced, search, roles = commands("G1")
    run = discogs.run(produced)
    assert len(run.sent) == 60  # the name + 59 of the 65 discography pages
    assert run.stdout.count("· Producer") == 59
    run = discogs.run(search)
    assert len(run.sent) == 1
    assert run.sent[0][1]["credit"] == "Flood"
    run = discogs.run(roles)
    assert len(run.sent) == 1
    assert "Producer: Flood [@a20661]" in run.stdout


@recipe("G2", "works")
def test_g2_releases_with_a_track(discogs: World) -> None:
    (search,) = commands("G2")
    run = discogs.run(search)
    assert len(run.sent) == 1
    assert run.sent[0][1]["track"] == "Hurt"


@recipe("G3", "works")
def test_g3_label_catalogue(discogs: World) -> None:
    (catalogue,) = commands("G3")
    first = discogs.run(catalogue)
    second = discogs.run(first.continuation())
    assert len(first.sent) + len(second.sent) == 1 + pages(LABEL_ROWS)
    rows = first.refs() + second.refs()
    assert len(rows) == LABEL_ROWS


@recipe("I1", "works, costly")
def test_i1_capped_runs_and_the_continuation(discogs: World) -> None:
    first_run, continued = commands("I1")
    run = discogs.run(first_run)
    assert len(run.sent) == 60
    assert "scan stopped at --max-requests 60" in run.stdout.splitlines()[0]
    assert run.continuation() == continued
    run = discogs.run(continued)
    assert len(run.sent) == pages(FLOOD_ROWS) - 59  # the header is cached
    assert run.stdout.count("· Producer") == FLOOD_ROWS // 100 - 59


@recipe("I2", "works")
def test_i2_no_username_needed(discogs: World) -> None:
    (wantlist,) = commands("I2")
    run = discogs.run(wantlist)
    assert paths(run.sent) == ["/oauth/identity", "/users/me/wants"]
    assert run.stdout.startswith("Wantlist: me ")
