# discogs-sdk: request observability, rate-limit budget, cache-only mode

Repository: `/Users/jmf/Projects/Personal/discogs-sdk` (Python SDK for the Discogs
API, currently `0.4.0`). Read that repo's `AGENTS.md` before starting; the
conventions below that matter most are restated here so this plan stands alone.

This plan produces release `0.5.0`. A companion plan (`PLAN.md` in
`/Users/jmf/Projects/Personal/agent-discogs`) consumes the API defined here; the
public contract in section 2 is shared between the two and must be implemented
exactly as written.

## 1. Why

Discogs throttles by source IP: 60 requests per minute with credentials, 25
without, as a moving 60-second window. Every response carries three headers:

- `X-Discogs-Ratelimit` - requests allowed per window
- `X-Discogs-Ratelimit-Used` - requests made in the current window
- `X-Discogs-Ratelimit-Remaining` - requests left in the current window

Today the SDK never reads them. `RateLimitError` (429) carries only
`retry_after`. The SDK also retries 429 automatically (bounded by
`max_retries`, honouring `Retry-After`), so a caller that exhausts the budget
usually does not see an error at all; it sees requests that stall for up to a
minute. Nothing lets a caller pace itself before that happens.

Three observability gaps to close, all in the request boundary `_send()`:

1. **Rate-limit budget** - expose the parsed headers as a typed value, updated
   on live network responses only.
2. **Per-request events** - a structured callback per logical request: cache
   hit or network, status, elapsed time, attempts, whether it was stored, and
   the rate limit it reported. The SDK already logs all of this as unstructured
   `logging` debug lines; the callback is the structured equivalent.
3. **Cache-only execution** - a scope in which a request that cannot be served
   from the response cache fails fast instead of going to the network. Mirror of
   the existing `no_cache()`. Lets a caller "try everything for free first".

## 2. Public contract (shared with agent-discogs)

New module `src/discogs_sdk/_events.py` (stdlib imports only, so
`_exceptions.py` can import it without cycles):

```python
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class RateLimit:
    """Discogs rate-limit headers from one live response."""

    limit: int  # X-Discogs-Ratelimit
    used: int  # X-Discogs-Ratelimit-Used
    remaining: int  # X-Discogs-Ratelimit-Remaining

    @classmethod
    def from_headers(cls, headers: Mapping[str, str]) -> RateLimit | None:
        """None unless all three headers are present and integers."""


@dataclass(frozen=True, slots=True)
class RequestEvent:
    """One logical request as seen by `_send()`; emitted once per call."""

    method: str  # upper-case
    url: str  # fully resolved, query included
    status_code: int
    source: Literal["network", "cache"]
    elapsed_ms: float  # network: final attempt only; cache: lookup time
    attempts: int  # network: >= 1 (1 = no retry); cache: 0
    stored: bool  # written to the response cache by this call
    ratelimit: RateLimit | None  # always None for source == "cache"
```

Additions to existing public surface:

```python
class Discogs / AsyncDiscogs:
    def __init__(self, *, ..., on_request: Callable[[RequestEvent], None] | None = None)

    @property
    def ratelimit(self) -> RateLimit | None:
        """Last RateLimit parsed from a live network response, or None."""

    def cache_only(self) -> ContextManager[Self]            # Discogs
    def cache_only(self) -> AsyncContextManager[Self]       # AsyncDiscogs


class RateLimitError(DiscogsAPIError):
    ratelimit: RateLimit | None     # new; the headers on the 429 itself


class CacheMissError(DiscogsError):
    """Raised inside `cache_only()` for a request the cache cannot serve."""

    method: str
    url: str
```

Exports added to `src/discogs_sdk/__init__.py` and `__all__`: `RateLimit`,
`RequestEvent`, `CacheMissError`.

### 2.1 Semantics (normative)

- `RateLimit.from_headers()` is case-insensitive on header names (httpx2
  `Headers` already is; a plain `dict` from the cache is not - look up both
  forms or normalise). Any missing or non-integer header returns `None`.
- `client.ratelimit` is updated only from **network** responses whose headers
  parse; a response without the headers (Discogs documents "some exceptions")
  leaves the previous value in place. Cache hits never touch it. Initial value
  `None`.
- `on_request` is invoked **synchronously inside `_send()`**, in both clients,
  exactly once per `_send()` call that produces a response:
  - on a cache hit, before returning;
  - on the final network response (2xx or error), **before**
    `_raise_for_response()` so the event exists even when the call raises;
  - **not** for intermediate retried attempts (the final event carries
    `attempts`), and **not** when the call ends in `DiscogsConnectionError`
    (there is no response; the exception carries the message).
  Exceptions raised by the callback propagate to the caller unchanged; the SDK
  does not swallow them. Document that the callback must be cheap and must not
  block, since the async client calls it from the event loop.
- `stored` is `True` only when this call wrote the entry (cache enabled, not
  bypassed, GET/HEAD, 2xx).
- `cache_only()` uses the same `ContextVar`-of-client-ids pattern as
  `no_cache()` (nests, restores on exit including on exception, per-task/thread
  state). Inside the scope, `_send()` raises `CacheMissError(method, url)`
  **before any network I/O** whenever the request cannot be served from the
  cache: a miss, an expired entry, a non-cacheable method, caching disabled, or
  an active `no_cache()` scope. `cache_only()` inside `no_cache()` therefore
  fails every request; that is the correct reading of both scopes and needs no
  special case. `url` in the exception is the fully resolved request URL.
- `RateLimitError.ratelimit` is parsed from the 429 response's own headers; it
  is `None` when absent.
- Existing `logging` lines (`Cache hit`, `HTTP request`, `HTTP response`,
  `Retrying ...`) are unchanged.

## 3. Repository conventions that constrain the work

- `src/discogs_sdk/_async/` is the source of truth. `src/discogs_sdk/_sync/`
  is **generated** by `scripts/generate_sync.py`; never edit `_sync/` by hand.
  Sync/async divergence uses the branch directive:
  ```python
  if True:  # ASYNC
      await asyncio.sleep(delay)
  else:
      time.sleep(delay)
  ```
  The generator keeps only the `else` branch for sync. `no_cache()` in
  `_async/_client.py` (around line 303) shows the directive used to switch
  between `@asynccontextmanager` and `@contextmanager`; copy that shape for
  `cache_only()`. Run `just generate-sync` after every change to `_async/`;
  `just sync-check` is part of `just qa`.
- Non-I/O logic lives in `BaseClient` (`src/discogs_sdk/_base_client.py`).
  Put the state (`_ratelimit`, `_on_request`) and the observe helper there so
  both clients share one implementation.
- Tests: `tests/async/` and `tests/sync/` are both hand-maintained; every new
  async test gets a sync twin. HTTP mocking is `respx` at the transport level
  via the `respx_mock` fixture; **every `respx.mock(...)` must pass
  `using="httpcore2"`**, and mocked responses are built with
  `respx.MockResponse(...)`, never `httpx2.Response(...)` (both rules carry a
  `KLUDGE:` in `tests/conftest.py`). `pytest-asyncio` runs in `asyncio_mode="auto"`.
- All public exports go through `src/discogs_sdk/__init__.py`.
- Examples and docs use Nine Inch Nails data (artist 3857, release 352665,
  master 3719, label 647).
- Python floor is 3.10, so `slots=True` on dataclasses is allowed. Line
  length 88. Public API must stay fully typed (`py.typed`).
- Commit messages are conventional commits; `cliff.toml` builds the changelog
  from them at release time. There is no hand-edited CHANGELOG file.

## 4. Implementation steps

Work in this order; each step leaves the suite green.

### 4.1 `src/discogs_sdk/_events.py` (new)

Implement `RateLimit` and `RequestEvent` exactly as in section 2.
`from_headers` implementation sketch:

```python
_LIMIT, _USED, _REMAINING = (
    "x-discogs-ratelimit",
    "x-discogs-ratelimit-used",
    "x-discogs-ratelimit-remaining",
)


@classmethod
def from_headers(cls, headers: Mapping[str, str]) -> RateLimit | None:
    lowered = {k.lower(): v for k, v in headers.items()}
    try:
        return cls(int(lowered[_LIMIT]), int(lowered[_USED]), int(lowered[_REMAINING]))
    except (KeyError, ValueError):
        return None
```

(Lower-casing a handful of headers per request is acceptable; httpx2 headers
are already lower-cased on iteration, so the comprehension is cheap.)

### 4.2 `src/discogs_sdk/_exceptions.py`

- Add `CacheMissError(DiscogsError)`:
  ```python
  class CacheMissError(DiscogsError):
      """Raised inside `cache_only()` when a request cannot be served from cache."""

      def __init__(self, method: str, url: str) -> None:
          super().__init__(f"Not cached: {method} {url}")
          self.method = method
          self.url = url
  ```
- `RateLimitError.__init__` gains `ratelimit: RateLimit | None = None`, stored
  as `self.ratelimit`. Import `RateLimit` from `._events`.

### 4.3 `src/discogs_sdk/_base_client.py`

- `BaseClient.__init__`: new keyword `on_request: Callable[[RequestEvent], None] | None = None`;
  store `self._on_request = on_request` and `self._ratelimit: RateLimit | None = None`.
- Add:
  ```python
  @property
  def ratelimit(self) -> RateLimit | None:
      """Rate limit reported by the most recent live response, or None."""
      return self._ratelimit


  def _observe(self, event: RequestEvent) -> None:
      if event.ratelimit is not None:
          self._ratelimit = event.ratelimit
      if self._on_request is not None:
          self._on_request(event)
  ```
- `_raise_for_response()`: also pass
  `ratelimit=RateLimit.from_headers(response.headers)` to `_maybe_raise()`.
- `_maybe_raise()`: new keyword `ratelimit: RateLimit | None = None`, forwarded
  to `RateLimitError(...)` in the 429 branch. Other branches ignore it.

### 4.4 `src/discogs_sdk/_async/_client.py` (then regenerate sync)

- Constructor: add `on_request` keyword, pass it to `super().__init__`.
  Docstring entry, next to `cache`:
  > `on_request`: Called once per request with a `RequestEvent` - after a
  > cache hit or after the final network response, before any error is
  > raised. Invoked synchronously; keep it cheap.
- Module level, beside `_CACHE_BYPASS`:
  ```python
  _CACHE_ONLY: ContextVar[frozenset[int]] = ContextVar(
      "discogs_sdk_cache_only", default=frozenset()
  )
  ```
- In `_send()`, after `use_cache` is computed:
  ```python
  cache_only = id(self) in _CACHE_ONLY.get()
  ```
  - Build `req` (and therefore the resolved URL) when `use_cache or cache_only`;
    keep computing `cache_key` only when `use_cache`.
  - Cache lookup: time it with `time.monotonic()`. On a hit, replace the bare
    `return httpx2.Response(...)` with: build the response, call
    `self._observe(RequestEvent(method=method.upper(), url=str(req.url),
    status_code=status, source="cache", elapsed_ms=lookup_ms, attempts=0,
    stored=False, ratelimit=None))`, then return it. Keep the existing
    `logger.debug("Cache hit: ...")`.
  - After the lookup (miss) or when `use_cache` is false: if `cache_only`,
    `raise CacheMissError(method.upper(), str(req.url))`. This sits **before**
    the `for attempt in range(...)` loop, so no network I/O can happen.
- In the final-response branch (`if not may_retry_status(...) or attempt == self.max_retries:`):
  set `stored = False`, set it `True` inside the existing cache-write block,
  then immediately before `self._raise_for_response(response)`:
  ```python
  self._observe(
      RequestEvent(
          method=method.upper(),
          url=str(response.request.url),
          status_code=response.status_code,
          source="network",
          elapsed_ms=elapsed_ms,
          attempts=attempt + 1,
          stored=stored,
          ratelimit=RateLimit.from_headers(response.headers),
      )
  )
  ```
  `response.request.url` is the resolved URL for both the injected-client and
  owned-client paths, so it does not depend on `req` having been built.
- Add `cache_only()` directly after `no_cache()`, same `if True:  # ASYNC`
  directive, same body with `_CACHE_ONLY` instead of `_CACHE_BYPASS`. Docstring:
  > Serve only from the response cache for the current execution context. A
  > request the cache cannot serve - a miss, an expired entry, a non-GET, a
  > disabled cache or an enclosing `no_cache()` - raises `CacheMissError`
  > before any network I/O. Scopes nest and restore exactly like `no_cache()`.
- Run `just generate-sync`; confirm `just sync-check` passes and
  `src/discogs_sdk/_sync/_client.py` now has the sync `cache_only()` and the
  event emission.

### 4.5 `src/discogs_sdk/__init__.py`

Import and export `RateLimit`, `RequestEvent` (from `._events`) and
`CacheMissError` (from `._exceptions`). Add them to `__all__` under
"Client config"/"Exceptions" respectively, keeping the existing grouping.

## 5. Tests

Add `tests/async/test_events.py` and `tests/sync/test_events.py` (twins). Use
the `respx_mock` fixture with `using="httpcore2"` and `respx.MockResponse`.
Helper: a list-appending `on_request` callback.

Behaviours to cover (each is one test; names are suggestions):

1. `test_network_event_fields` - single 200 with ratelimit headers
   `60/14/46`: one event, `source == "network"`, `attempts == 1`,
   `stored is False` (cache disabled), `ratelimit == RateLimit(60, 14, 46)`,
   `url` is the fully resolved URL including query, `elapsed_ms >= 0`; and
   `client.ratelimit == RateLimit(60, 14, 46)`.
2. `test_cache_hit_event` - `cache=True`, two identical GETs: events are
   `[network(stored=True), cache(attempts=0, ratelimit=None)]`, only one HTTP
   call recorded by respx, and `client.ratelimit` still holds the network value.
3. `test_ratelimit_keeps_last_known_when_headers_absent` - first response with
   headers, second without: `client.ratelimit` unchanged after the second;
   the second event's `ratelimit is None`.
4. `test_ratelimit_error_carries_headers` - 429 with headers and `Retry-After`:
   `RateLimitError.ratelimit == RateLimit(...)`, `retry_after` still set, and the
   event was emitted (with `status_code == 429`) even though the call raised.
   Use `max_retries=0` so the 429 is final.
5. `test_retries_produce_one_event_with_attempts` - 503 then 200 with
   `max_retries=1`: exactly one event, `attempts == 2`.
6. `test_connection_error_emits_no_event` - `side_effect=httpx2.ConnectError(...)`,
   `max_retries=0`: `DiscogsConnectionError` raised, no events.
7. `test_callback_exception_propagates` - callback raises `RuntimeError`; the
   SDK call raises that same `RuntimeError`.
8. `test_cache_only_miss_raises_before_network` - `cache=True`, inside
   `cache_only()` a never-fetched URL raises `CacheMissError` with `method` and
   resolved `url`; respx recorded zero calls; no event emitted.
9. `test_cache_only_hit_serves_from_cache` - fetch once outside the scope, then
   inside `cache_only()` the same call succeeds with zero additional HTTP calls.
10. `test_cache_only_with_cache_disabled_raises` - `cache=False`,
    `cache_only()`: `CacheMissError`.
11. `test_cache_only_inside_no_cache_raises` - nested scopes: `CacheMissError`.
12. `test_cache_only_scope_restores` - after the `with` block (including when
    the body raised), the same request goes to the network normally.
13. `test_cache_only_non_get_raises` - a POST inside `cache_only()` raises
    `CacheMissError` and respx recorded nothing.

Extend `tests/sync/test_exceptions.py` (and async twin if it exists) with:
`RateLimit.from_headers` returns `None` on missing or non-integer values and
accepts mixed-case header names; `CacheMissError` message and attributes.

Add `RateLimit`, `RequestEvent`, `CacheMissError` to whatever test enumerates
public exports (check `tests/test_public_api*.py` or the export-coverage
script; `scripts/check_endpoint_coverage.py` is about endpoints, not exports).

## 6. Documentation

- `README.md`
  - Features list: extend "Rate Limit Aware" to mention the exposed budget
    (`client.ratelimit`) and per-request events.
  - "Error handling": show `exc.ratelimit` on `RateLimitError`; add
    `CacheMissError` to the hierarchy diagram as a direct child of
    `DiscogsError` (sibling of `DiscogsConnectionError` and `DiscogsAPIError`).
  - "Configuration" table: add `on_request` (`None`, "Callback receiving a
    `RequestEvent` per request").
  - "Caching": after the `no_cache()` example, document `cache_only()` with an
    example that catches `CacheMissError`. Use release 352665.
  - New subsection "Observability" (after "Caching"): `client.ratelimit`
    semantics (live responses only, last known good), `RequestEvent` fields,
    the emission rules from section 2.1, and a short pacing example:
    ```python
    client = Discogs(
        token="...", on_request=lambda e: print(e.source, e.status_code, e.elapsed_ms)
    )
    release = client.releases.get(352665)
    if client.ratelimit and client.ratelimit.remaining < 10:
        time.sleep(60)
    ```
- `examples/advanced.py`: add a short section using `on_request` and
  `cache_only()` with NIN data, in the style of the existing sections.
- `AGENTS.md`: error hierarchy line gains `CacheMissError`; "Rate Limits" quirk
  gains one sentence: "`client.ratelimit` exposes the headers; `on_request`
  emits a `RequestEvent` per request."
- Docstrings on every new public symbol (the `verify-types`/public API audit
  runs on them).

## 7. QA and release

1. `just generate-sync`, then `just qa` (dead-code, deps-unused, format-check,
   lint, sync-check, type-check) and `just test` all green.
2. Optionally `just test-unauthenticated` to see real headers flow through
   (`client.ratelimit` should be `RateLimit(25, ...)` after one anonymous call).
   Do not run it within a minute of `just test-integration`.
3. Bump `version = "0.5.0"` in `pyproject.toml` (new public API, minor bump),
   commit (`feat: expose rate-limit budget, request events and cache-only mode`),
   `just release`.

## 8. Acceptance criteria

- The section 2 contract is importable from `discogs_sdk` exactly as written,
  in both `Discogs` and `AsyncDiscogs`.
- `just qa` and `just test` pass; `_sync/` is generated, not hand-edited.
- Every behaviour in section 5 has a passing test in both `tests/async` and
  `tests/sync`.
- A cache hit never updates `client.ratelimit` and never reports a `RateLimit`
  in its event.
- Inside `cache_only()`, a miss performs no HTTP call (respx call count is the
  proof).
- `RateLimitError.ratelimit` is populated from the 429 response.
- README documents `on_request`, `ratelimit`, `cache_only()`, `CacheMissError`.
- `0.5.0` is published to PyPI.

## 9. Out of scope (do not do here)

- Emitting events for transport failures (`status_code` would have to become
  optional for every consumer).
- Local throttling or sleeping based on the budget. The SDK reports; callers
  decide.
- A public single-page fetch API (agent-discogs currently calls `_send` and
  `_build_url` directly for that). Worth a separate change; not this one.
- Cache statistics (`entries`, `bytes`). agent-discogs reads the SQLite file
  size from disk instead.
