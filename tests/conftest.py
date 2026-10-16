"""Shared test setup: environment isolation, SDK stand-ins and harnesses,
and CLI result checks."""

from __future__ import annotations

import functools
import json
import re
import shlex
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx2
import pytest
from click.testing import CliRunner, Result
from discogs_sdk import Discogs

import agent_discogs.client as client_module
from agent_discogs import cli, trace
from agent_discogs.pagination import PageResult

pytest_plugins = ["pytester"]

# What a developer's shell may export that changes CLI output or SDK auth:
# the debug switch, and every credential the SDK falls back to when the
# client gets no explicit one (unsetting DISCOGS_TOKEN alone leaves OAuth on).
ISOLATED_ENV = (
    "AGENT_DISCOGS_DEBUG",
    "DISCOGS_TOKEN",
    "DISCOGS_CONSUMER_KEY",
    "DISCOGS_CONSUMER_SECRET",
    "DISCOGS_ACCESS_TOKEN",
    "DISCOGS_ACCESS_TOKEN_SECRET",
)

# The skills bundled under src/agent_discogs/skills, in the order `skills` lists them.
SKILLS = ("collection", "core", "marketplace", "pressings")

REPO_ROOT = Path(__file__).resolve().parent.parent
# The docs a user or an agent reads, whose refs test_doc_examples checks and
# whose `agent-discogs ...` commands test_doc_commands parses.
DOC_FILES = (
    REPO_ROOT / "README.md",
    *sorted((REPO_ROOT / "docs").glob("*.md")),
    REPO_ROOT / "skills" / "agent-discogs" / "SKILL.md",
    *sorted((REPO_ROOT / "src" / "agent_discogs" / "skills").rglob("*.md")),
)


@pytest.fixture(autouse=True)
def _isolate_env(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run offline tests without ISOLATED_ENV; live tests keep the real values."""
    if request.node.get_closest_marker("live"):
        return
    for name in ISOLATED_ENV:
        monkeypatch.delenv(name, raising=False)


def fake(**attrs: object) -> SimpleNamespace:
    """A stand-in for an SDK model or proxy carrying only the attributes the
    code under test reads. Prefer a real model where its fields allow it."""
    return SimpleNamespace(**attrs)


def fake_model(**attrs: object) -> SimpleNamespace:
    """A `fake` that also answers `model_dump()` (any `mode`), as `--full` reads."""
    model = SimpleNamespace(**attrs)
    model.model_dump = lambda **_: dict(attrs)
    return model


class Listing:
    """A fake SDK listing method (`client.search`, `<proxy>.releases.list`) for
    tests that fake `fetch_page`: it records the params of each call."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def __call__(self, **params: object) -> None:
        self.calls.append(params)


def listed(**attrs: object) -> SimpleNamespace:
    """A fake artist, label or master proxy with fake `releases`/`versions`."""
    return SimpleNamespace(
        **attrs, releases=fake(list=Listing()), versions=fake(list=Listing())
    )


def fake_client(
    *,
    artists_get: object = None,
    labels_get: object = None,
    masters_get: object = None,
    releases_get: object = None,
) -> SimpleNamespace:
    """A fake Discogs client with the given resource getters."""
    return SimpleNamespace(
        artists=SimpleNamespace(get=artists_get or (lambda _id: listed())),
        labels=SimpleNamespace(get=labels_get or (lambda _id: listed())),
        masters=SimpleNamespace(get=masters_get or (lambda _id: listed())),
        releases=SimpleNamespace(get=releases_get or (lambda _id: None)),
        search=Listing(),
    )


UseClient = Callable[[object], None]
ServePage = Callable[[PageResult], None]


@pytest.fixture
def use_client(monkeypatch: pytest.MonkeyPatch) -> UseClient:
    """Make `get_client()` return the given client in the `get` and `search`
    commands, the two that build one."""

    def _use(client: object) -> None:
        for module in ("get", "search"):
            monkeypatch.setattr(
                f"agent_discogs.commands.{module}.get_client", lambda: client
            )

    return _use


@pytest.fixture
def serve_page(monkeypatch: pytest.MonkeyPatch) -> ServePage:
    """Make every page a command reads, directly or through a filtered scan,
    come back as the given result. The command's listing is never called."""

    def _serve(result: PageResult) -> None:
        def _fetch(*_args: object, **_kwargs: object) -> PageResult:
            return result

        for module in ("commands.get", "commands.search", "pagination"):
            monkeypatch.setattr(f"agent_discogs.{module}.fetch_page", _fetch)

    return _serve


def text_error(result: Result) -> str:
    """Check a text-mode failure: exit 1 with nothing on stdout. Returns
    stderr, which holds the diagnostic (and the budget footer, if any)."""
    assert result.exit_code == 1, result.output
    assert result.stdout == ""
    return result.stderr


def json_error(result: Result) -> dict[str, Any]:
    """Check a `--json` failure: exit 1, and stdout is exactly the error
    envelope. stderr is left alone: it may carry the budget footer or the
    `--debug` panel. Returns the envelope's `error` object."""
    assert result.exit_code == 1, result.output
    document = json.loads(result.stdout)
    assert set(document) == {"error"}
    return document["error"]


class RealSdk:
    """What `real_sdk` sets up: the paths it sent (`sends`), the sleeps the
    client asked for (`sleeps`), and `respond`, the function that answers
    each request, which a test sets before it sends anything."""

    def __init__(self, cache_home: Path) -> None:
        self.cache_home = cache_home
        self.sends: list[str] = []
        self.sleeps: list[float] = []
        self.respond: Callable[[httpx2.Request], httpx2.Response] = _unanswered


def _unanswered(request: httpx2.Request) -> httpx2.Response:
    raise AssertionError(f"no response set for {request.url.path}")


@pytest.fixture
def real_sdk(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[RealSdk]:
    """`get_client()` builds the real `Discogs` client, with a token, its
    SQLite cache under `tmp_path` and `time.sleep` recorded; only the HTTP
    transport is faked."""
    sdk = RealSdk(tmp_path)

    def _transport(request: httpx2.Request) -> httpx2.Response:
        sdk.sends.append(request.url.path)
        return sdk.respond(request)

    http = httpx2.Client(transport=httpx2.MockTransport(_transport))
    monkeypatch.setenv("DISCOGS_TOKEN", "test-token")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setattr("time.sleep", sdk.sleeps.append)
    monkeypatch.setattr(client_module, "_client", None)
    monkeypatch.setattr(
        client_module, "Discogs", functools.partial(Discogs, http_client=http)
    )
    yield sdk
    if client_module._client is not None:
        client_module._client.close()
    http.close()


class _FakeAPI:
    """The real SDK client, with its in-memory cache and real `cache_only()` /
    `no_cache()`, over a mock transport: `sends` lists every request that
    reached the network as `(path, query params)`. `respond(path, params)`
    returns the JSON body, or an `httpx2.Response` to send as is. Like the
    CLI's own client, it never retries."""

    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        respond: Callable[[str, dict[str, str]], object],
    ) -> None:
        self.sends: list[tuple[str, dict[str, str]]] = []
        self._respond = respond
        http = httpx2.Client(transport=httpx2.MockTransport(self._handle))
        # The SDK caches nothing for an injected http_client without SDK
        # credentials, so the fake needs a token for cache flags to mean anything.
        client = Discogs(
            token="fake",  # noqa: S106  # placeholder, never reaches Discogs
            cache=True,
            http_client=http,
            on_request=trace.record,
            max_retries=0,
        )
        monkeypatch.setattr("agent_discogs.client._client", client)

    def _handle(self, request: httpx2.Request) -> httpx2.Response:
        params = dict(request.url.params)
        self.sends.append((request.url.path, params))
        body = self._respond(request.url.path, params)
        if isinstance(body, httpx2.Response):
            return body
        return httpx2.Response(200, json=body)


_LISTING_KEYS = {"search": "results", "releases": "releases", "versions": "versions"}


def _empty_listing(path: str, params: dict[str, str]) -> object:
    """Every listing is an empty page; every entity is `{"id": 1, ...}`."""
    key = _LISTING_KEYS.get(path.rsplit("/", 1)[-1])
    if key is None:
        return {"id": 1, "name": "Entity", "title": "Entity"}
    page = {"page": int(params.get("page", 1)), "pages": 1, "items": 0}
    return {"pagination": page, key: []}


class _Catalogue:
    """`rows` rows (default 300) behind every listing, titled `Row<id>e<edition>`.
    Only the last two rows of each API page pass the client-side filters
    (official format, role Main, year 1999, official version), so a `--limit
    2` filtered page fills on the last row of each 100-row scan page and every
    continuation needs a new request; with `sparse`, no row passes. Bumping
    `edition` stands in for Discogs changing data after a page was cached."""

    def __init__(self, rows: int = 300, *, sparse: bool = False) -> None:
        self.rows, self.sparse = rows, sparse
        self.edition = 1

    def __call__(self, path: str, params: dict[str, str]) -> object:
        key = _LISTING_KEYS.get(path.rsplit("/", 1)[-1])
        if key is None:
            return {"id": 1, "name": "Entity", "title": "Entity"}
        page, per = int(params["page"]), int(params["per_page"])
        rows = []
        for i in range((page - 1) * per, min(page * per, self.rows)):
            match = not self.sparse and i % per >= per - 2
            title = f"Row{i}e{self.edition}"
            row: dict[str, object] = {"id": i, "title": title, "type": "release"}
            if key == "results":
                row["format"] = ["Vinyl"] if match else ["Unofficial Release"]
            elif key == "releases":
                row["role"] = "Main" if match else "Remix"
                row["year"] = 1999 if match else 2000
            else:  # version rows print the label, not the title
                row["label"] = title
                row["format"] = "LP, Album" if match else "LP, Unofficial Release"
            rows.append(row)
        pagination = {"page": page, "pages": -(-self.rows // per), "items": self.rows}
        return {"pagination": pagination, key: rows}


def _tracks_fake(recorder: Callable[[], None]) -> SimpleNamespace:
    """A client serving `tracks @r123` whose lookup also runs `recorder`, the
    way the real client's `on_request` hook fires inside the SDK call."""
    track = fake_model(position="1", title="Song", duration="3:00", type=None)
    release = fake(id=123, title="Album", year=2020, artists=None, tracklist=[track])

    def _get(_id: int) -> object:
        recorder()
        return release

    return fake_client(releases_get=_get)


_ROW = re.compile(r"Row(\d+)e(\d+)")

# The `get collection` API (`_Collection`), its harness and result readers,
# shared by test_cli_collection.py and test_cli_collection_filters.py.
_USER = "/users/me"
_RECORDS = f"{_USER}/collection/folders/0/releases"
_FOLDER_LIST = f"{_USER}/collection/folders"
_FIELDS = f"{_USER}/collection/fields"
# The user's custom fields, by position: two grade dropdowns and the notes.
_FIELD_LIST = [
    {"id": 1, "name": "Media Condition", "type": "dropdown", "position": 1},
    {"id": 2, "name": "Sleeve Condition", "type": "dropdown", "position": 2},
    {"id": 3, "name": "Notes", "type": "textarea", "position": 3, "lines": 3},
]
_SEARCH = "/database/search"
# Folder 5 is "Vinyl", 1 is "Uncategorized"; 0 ("All") is never a record's.
# "To Sell" holds the records a test moves there through `odd`.
_FOLDERS = [
    {"id": 0, "name": "All", "count": 250},
    {"id": 1, "name": "Uncategorized", "count": 249},
    {"id": 5, "name": "Vinyl", "count": 1},
    {"id": 7, "name": "To Sell", "count": 0},
]


def _folder(folder_id: int) -> str:
    """The path of one folder's records."""
    return f"{_USER}/collection/folders/{folder_id}/releases"


# The Techno records: 12 of 250, on each of the three 100-record pages.
_TECHNO = (5, 17, 40, 99, 100, 130, 160, 199, 200, 210, 230, 249)
# The masters of the releases that have one: release 7 (records 3 and 120)
# is master 60, as release 1050 is when a test gives it one through `odd`;
# releases 1011 and 1012 are master 61. No other record has a `master_id`.
_MASTERS = {7: 60, 1011: 61, 1012: 61}

_VALUE_PATH = f"{_USER}/collection/value"
# The collection value as Discogs writes it, in the profile's currency.
_VALUE = {"minimum": "€75.50", "median": "€100.25", "maximum": "€1,250.00"}


def _too_many() -> httpx2.Response:
    return httpx2.Response(429, json={}, headers={"Retry-After": "30"})


class _Collection:
    """The token's user `me` owns `size` records, in collection order. Record
    `n` is release `1000 + n`, `Record <n>` by Artist (artist 4242) on Label
    (`CAT<n>`), an Electronic, House LP from 2001 in Uncategorized, added on
    2024-01-(n % 28 + 1), except that the `_TECHNO` records are Techno 12"
    singles from 1992 on Tresor, record 3 is release 7, rated 4, in Vinyl,
    record 120 is release 7 again, in Uncategorized, the releases in
    `_MASTERS` name their master, record 3 has a Media Condition and a Notes
    value, and `odd` replaces records by number. Each folder of `folders`
    (by default `_FOLDERS`) lists the records whose `folder_id` is its id,
    and folder 0 every record; the custom fields are `fields` (by default
    `_FIELD_LIST`). The profile adds `profile` (by default `curr_abbr: EUR`)
    and the collection value is `value` (by default `_VALUE`). `throttle`
    answers with a 429: `"identity"` the username, `"folders"` the folder
    list, `"fields"` the fields list, `"page:N"` page N of any folder. A
    search lists releases 1 and 2."""

    def __init__(
        self,
        size: int = 250,
        *,
        throttle: tuple[str, ...] = (),
        odd: dict[int, dict[str, Any]] | None = None,
        profile: dict[str, Any] | None = None,
        value: dict[str, Any] | None = None,
        folders: list[dict[str, Any]] | None = None,
        fields: list[dict[str, Any]] | None = None,
    ) -> None:
        self.size, self.throttle, self.odd = size, throttle, odd or {}
        self.profile = {"curr_abbr": "EUR"} if profile is None else profile
        self.value = _VALUE if value is None else value
        self.folders = _FOLDERS if folders is None else folders
        self.fields = _FIELD_LIST if fields is None else fields

    def __call__(self, path: str, params: dict[str, str]) -> object:
        if path == "/oauth/identity":
            if "identity" in self.throttle:
                return _too_many()
            return {"id": 7, "username": "me"}
        if path == _FIELDS:
            if "fields" in self.throttle:
                return _too_many()
            return {"fields": self.fields}
        if path == _FOLDER_LIST:
            if "folders" in self.throttle:
                return _too_many()
            return {"folders": self.folders}
        folder = re.fullmatch(rf"{_USER}/collection/folders/(\d+)/releases", path)
        if folder:
            page = int(params["page"])
            if f"page:{page}" in self.throttle:
                return _too_many()
            return self._page(int(folder[1]), page, int(params["per_page"]))
        if path == _USER:
            return {"id": 7, "username": "me", **self.profile}
        if path == _VALUE_PATH:
            return self.value
        if path == _SEARCH:
            rows = [{"id": i, "title": f"Title {i}", "type": "release"} for i in (1, 2)]
            pages = {"page": 1, "pages": 1, "items": 2, "per_page": 100}
            return {"pagination": pages, "results": rows}
        raise AssertionError(path)

    def _page(self, folder: int, page: int, per: int) -> object:
        """Page `page` of folder `folder`: every record for folder 0, else
        the records whose `folder_id` is `folder`."""
        records = [self.odd.get(n) or self.record(n) for n in range(self.size)]
        if folder:
            records = [r for r in records if r.get("folder_id") == folder]
        items = records[(page - 1) * per : page * per]
        pages = {"page": page, "pages": -(-len(records) // per), "items": len(records)}
        return {"pagination": {**pages, "per_page": per}, "releases": items}

    @staticmethod
    def record(n: int) -> dict[str, Any]:
        techno = n in _TECHNO
        release = {3: 7, 120: 7}.get(n, 1000 + n)
        return {
            "id": release,
            "instance_id": 10_000 + n,
            "folder_id": 5 if n == 3 else 1,
            "rating": 4 if n == 3 else 0,
            "date_added": f"2024-01-{n % 28 + 1:02d}T10:00:00-08:00",
            "basic_information": {
                "id": release,
                "title": f"Record {n}",
                "year": 1992 if techno else 2001,
                "artists": [{"id": 4242, "name": "Artist", "join": ""}],
                "labels": [
                    {"name": "Tresor" if techno else "Label", "catno": f"CAT{n}"}
                ],
                "formats": [
                    {
                        "name": "Vinyl",
                        "qty": "1",
                        "descriptions": ['12"', "33 ⅓ RPM"] if techno else ["LP"],
                    }
                ],
                "genres": ["Electronic"],
                "styles": ["Techno"] if techno else ["House"],
            }
            | ({"master_id": _MASTERS[release]} if release in _MASTERS else {}),
        } | (
            {
                "notes": [
                    {"field_id": 1, "value": "Very Good Plus (VG+)"},
                    {"field_id": 3, "value": "Shelf A3"},
                ]
            }
            if n == 3
            else {}
        )


class _Harness:
    """`_FakeAPI` over `collection`. With `token`, the environment holds the
    credentials of a user, as `get collection` needs."""

    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        collection: _Collection | None = None,
        *,
        token: bool = True,
    ) -> None:
        if token:
            monkeypatch.setenv("DISCOGS_TOKEN", "test-token")
        self.api = _FakeAPI(monkeypatch, collection or _Collection())

    def run(self, *args: str) -> tuple[Result, list[tuple[str, dict[str, str]]]]:
        start = len(self.api.sends)
        result = CliRunner().invoke(cli, list(args))
        return result, list(self.api.sends[start:])


def _paths(sent: list[tuple[str, dict[str, str]]]) -> list[str]:
    return [path for path, _ in sent]


def _pages(sent: list[tuple[str, dict[str, str]]]) -> list[int]:
    return [int(params["page"]) for path, params in sent if path == _RECORDS]


def _document(result: Result) -> dict[str, Any]:
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def _refs(result: Result) -> list[str]:
    return [row["ref"] for row in _document(result)["results"]]


def _continuation(result: Result) -> list[str]:
    """The argv of the `Continue scan:` or `Next page:` footer."""
    line = next(
        line
        for line in result.stdout.splitlines()
        if line.startswith(("Continue scan:", "Next page:"))
    )
    return shlex.split(line.split(":", 1)[1])[1:]  # drop "agent-discogs"


_TECHNO_REFS = [f"@r{1000 + n}" for n in _TECHNO]


# The account `--owned`, `--wanted` and `get owned` read (`_Account`) and its
# harness, shared by test_cli_owned.py and test_cli_get_owned.py.
class _Account:
    """Discogs as the token's user `me` sees it. Every listing holds releases
    1-5 (an artist's release 4 is a master; versions 2 and 3 are tagged
    "Unofficial Release", which a versions `format` filter matches); the
    "All" folder holds `size` records, all of other releases except release 2
    once (record 10), release 3 twice (records 20 and 150, the second on page
    2) and release 6 once, in no folder Discogs named (record 30). Each
    record names its release's master (`masters`): releases 2 and 3 are
    pressings of master 77, release 6 has master id 0, and the others none.
    The wantlist holds `wants` wants, all of other releases except release 1
    (want 5, page 1) and release 4 (want 120, page 2). Version, label and
    artist rows carry Discogs' `stats`, the user's `in_collection` being the
    copies the folder holds and `in_wantlist` 1 for a wanted release, except
    the row of release `unstated`, whose `stats.user` is null. `throttle`
    answers with a 429: `"identity"` the username, `"collection:N"`
    collection page N, `"folders"` the folder list, `"wants:N"` wants page
    N, and `"search:2"` a second search page (the search then has two). With
    `label_repeats`, a label catalogue sends releases 2 and 3 twice in a row
    each."""

    def __init__(
        self,
        size: int = 250,
        *,
        wants: int = 250,
        throttle: tuple[str, ...] = (),
        unstated: int | None = None,
        label_repeats: bool = False,
    ) -> None:
        self.size, self.throttle, self.unstated = size, throttle, unstated
        self.label_repeats = label_repeats
        # record: (release id, folder id)
        self.copies = {10: (2, 1), 20: (3, 5), 30: (6, None), 150: (3, 1)}
        self.masters = {2: 77, 3: 77, 6: 0}
        self.wants = [{5: 1, 120: 4}.get(n, 3000 + n) for n in range(wants)]

    def _stats(self, release_id: int) -> dict[str, Any]:
        held = sum(rid == release_id for rid, _ in self.copies.values())
        user = {"in_collection": held, "in_wantlist": int(release_id in self.wants)}
        return {
            "community": {"in_collection": 9, "in_wantlist": 3},
            "user": None if release_id == self.unstated else user,
        }

    def _wants_page(self, page: int, per: int) -> object:
        if f"wants:{page}" in self.throttle:
            return _too_many()
        ids = self.wants[(page - 1) * per : page * per]
        wants = [{"id": rid, "basic_information": {"id": rid}} for rid in ids]
        size = len(self.wants)
        pages = {"page": page, "pages": -(-size // per), "items": size}
        return {"pagination": {**pages, "per_page": per}, "wants": wants}

    def __call__(self, path: str, params: dict[str, str]) -> object:
        if path == "/oauth/identity":
            if "identity" in self.throttle:
                return _too_many()
            return {"id": 7, "username": "me"}
        if path == f"{_USER}/wants":
            return self._wants_page(int(params["page"]), int(params["per_page"]))
        if path == _FOLDER_LIST:
            return _too_many() if "folders" in self.throttle else {"folders": _FOLDERS}
        if path == _RECORDS:
            return self._folder_page(int(params["page"]), int(params["per_page"]))
        if path.startswith(f"{_USER}/collection/releases/"):
            release_id = int(path.rsplit("/", 1)[-1])
            items = [
                {"id": rid, "instance_id": n, "folder_id": folder}
                for n, (rid, folder) in sorted(self.copies.items())
                if rid == release_id
            ]
            pages = {"page": 1, "pages": 1, "items": len(items), "per_page": 100}
            return {"pagination": pages, "releases": items}
        key = path.rsplit("/", 1)[-1]
        # Version and label catalogue rows carry no type; they are releases.
        rows: list[dict[str, Any]] = [
            {"id": i, "title": f"Title {i}"} for i in range(1, 6)
        ]
        if key in ("versions", "releases"):
            for row in rows:
                row["stats"] = self._stats(row["id"])
        if key == "versions":
            for row in rows:
                unofficial = row["id"] in (2, 3)
                row["format"] = "CD, Unofficial Release" if unofficial else "CD"
            # The community's counts, which the row shows before the mark.
            rows[1]["stats"]["community"] = {"in_collection": 41, "in_wantlist": 284}
            if "format" in params:
                rows = [r for r in rows if params["format"] in r["format"].split(", ")]
            return {"pagination": _one_page(len(rows)), "versions": rows}
        if key == "releases" and path.startswith("/labels/"):
            if self.label_repeats:
                twice = (2, 3)
                rows = [
                    r for row in rows for r in [row] * (2 if row["id"] in twice else 1)
                ]
            return {"pagination": _one_page(len(rows)), "releases": rows}
        for row in rows:
            row["type"] = "release"
        if key == "releases":  # an artist's: release 4 is a master
            rows[3]["type"] = "master"
            return {"pagination": _one_page(5), "releases": rows}
        if key == "search":
            if "search:2" not in self.throttle:
                return {"pagination": _one_page(5), "results": rows}
            if params["page"] == "2":
                return _too_many()
            pages = {"page": 1, "pages": 2, "items": 105, "per_page": 100}
            return {"pagination": pages, "results": rows}
        return {"id": 1, "name": "Entity", "title": "Entity"}

    def _folder_page(self, page: int, per: int) -> object:
        if f"collection:{page}" in self.throttle:
            return _too_many()
        items = []
        for n in range((page - 1) * per, min(page * per, self.size)):
            rid, folder = self.copies.get(n, (1000 + n, 1))
            release = {"id": rid, "master_id": self.masters.get(rid)}
            items.append(
                {
                    "id": rid,
                    "instance_id": n,
                    "folder_id": folder,
                    "basic_information": release,
                }
            )
        pages = {"page": page, "pages": -(-self.size // per), "items": self.size}
        return {"pagination": {**pages, "per_page": per}, "releases": items}


def _one_page(items: int) -> dict[str, int]:
    return {"page": 1, "pages": 1, "items": items, "per_page": 5}


class _AccountHarness:
    """`_FakeAPI` over `account`. With `token`, the environment holds the
    credentials of a user, as `--owned` and `get owned` need."""

    def __init__(
        self, monkeypatch: pytest.MonkeyPatch, account: _Account, *, token: bool = True
    ) -> None:
        if token:
            monkeypatch.setenv("DISCOGS_TOKEN", "test-token")
        self.api = _FakeAPI(monkeypatch, account)

    def run(self, *args: str) -> tuple[Result, list[str]]:
        start = len(self.api.sends)
        result = CliRunner().invoke(cli, list(args))
        return result, [path for path, _ in self.api.sends[start:]]

    def pages(self) -> list[int]:
        return [int(p["page"]) for path, p in self.api.sends if path == _RECORDS]
