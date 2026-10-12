"""Shared test setup: environment isolation, SDK stand-ins and harnesses,
and CLI result checks."""

from __future__ import annotations

import functools
import json
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx2
import pytest
from click.testing import Result
from discogs_sdk import Discogs

import agent_discogs.client as client_module
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
