"""Shared test setup: environment isolation, SDK stand-ins and harnesses,
and CLI result checks."""

from __future__ import annotations

import functools
import json
import re
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx2
import pytest
from click.testing import Result
from discogs_sdk import Discogs

import agent_discogs.client as client_module
from agent_discogs import trace
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
