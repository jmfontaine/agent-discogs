# agent-discogs: budget footer, cache control, debug panel

Repository: `/Users/jmf/Projects/Personal/agent-discogs` - a token-efficient
Discogs CLI for AI agents, built on click and `discogs-sdk`. Read `AGENTS.md`
in this repo first; the conventions that constrain this work are restated in
section 3 so the plan stands alone.

Prerequisite: `discogs-sdk >= 0.5.0`, which adds the API restated in section 2
(its plan is `PLAN-SDK.md` beside this file). If `0.5.0` is not on PyPI yet,
stop and report; the only piece buildable against `0.4.0` is step 7.1 (SDK log
passthrough), and shipping it alone is not worth a release.

## 1. What and why

The Discogs API throttles by source IP: 60 requests/minute with a token, 25
without, moving 60-second window. The SDK retries 429 automatically, so an
agent that overspends does not see an error; it sees commands that stall for up
to a minute. Nothing today tells the agent what a command cost, what is left, or
whether a repeat would be free (the SDK caches GET responses for 1 hour in
`~/.cache/agent-discogs/cache.db`).

Three features, three questions:

| Layer | Question it answers | Always on? |
|---|---|---|
| Budget footer | What did this cost, what is left this minute | Yes, after any request |
| `--cached` / `--fresh` | Spend, or do not | Opt-in flags |
| `--debug` | Why did it cost that | Opt-in flag / env var |

Invariants that hold across all three:

- **stdout is a pure data channel.** Footer, warnings and the debug panel go to
  **stderr**, in text and `--json` mode alike. `--json` documents are never
  wrapped or extended. (Precedent: `fail()` in `errors.py` already prints text
  errors to stderr and the JSON error envelope to stdout.)
- Exit codes are unchanged by the footer and panel. A `--cached` miss is an
  error (exit 1) like any other failed ref.
- The CLI reports; it never sleeps or throttles on the agent's behalf.

## 2. SDK contract this plan depends on (discogs-sdk 0.5.0)

```python
from discogs_sdk import CacheMissError, Discogs, RateLimit, RateLimitError, RequestEvent

@dataclass(frozen=True, slots=True)
class RateLimit:
    limit: int; used: int; remaining: int

@dataclass(frozen=True, slots=True)
class RequestEvent:
    method: str                          # upper-case
    url: str                             # fully resolved, query included
    status_code: int
    source: Literal["network", "cache"]
    elapsed_ms: float                    # network: final attempt; cache: lookup time
    attempts: int                        # network >= 1; cache 0
    stored: bool                         # written to cache by this call
    ratelimit: RateLimit | None          # None for cache hits

Discogs(..., on_request: Callable[[RequestEvent], None] | None = None)
Discogs.ratelimit -> RateLimit | None    # last live value; cache hits never update it
Discogs.cache_only() -> ContextManager   # miss => CacheMissError before any network I/O
Discogs.no_cache() -> ContextManager     # existed in 0.4.0; bypasses the cache
RateLimitError.ratelimit -> RateLimit | None
CacheMissError(DiscogsError): .method, .url
```

Emission rules: exactly one event per SDK `_send()` call - on a cache hit, or on
the final network response *before* the SDK raises for a 4xx/5xx. No event for
retried intermediate attempts (the final event carries `attempts`) or for
connection failures. The callback runs synchronously inside the request.

The CLI's own single-page fetch (`pagination.fetch_page`) calls `client._send`
directly, so it is covered by the hook like every SDK resource call.

## 3. Repository conventions that constrain the work

- Task runner `just`: `just test` (pytest, excludes live tests), `just qa`
  (dead-code, deps-unused, format-check, lint, type-check, verify-types),
  `just lint-fix`, `just format`. Single test:
  `uv run pytest tests/test_cli.py -k name`.
- Tests use `click.testing.CliRunner` in-process; SDK calls are stubbed by
  monkeypatching `agent_discogs.commands.<module>.get_client` with
  `SimpleNamespace` fakes (`_fake_client(...)` in `tests/test_cli.py`). No
  mocking framework beyond `unittest.mock`/`monkeypatch`. click 8.3:
  `result.stdout` and `result.stderr` are separate; `result.output` is the
  interleaved terminal view.
- `tests/test_doc_examples.py` checks every `@ref` in docs against
  `KNOWN_REFS`. **Use only refs already present in the docs** in any new prose:
  `@a3857`, `@r847868`, `@m3719`, `@l647`.
- Any user-facing change (flag, output shape, error) must update all of:
  `_HELP_TEXT` in `src/agent_discogs/__init__.py`, `README.md`, and the bundled
  skill `src/agent_discogs/skills/core/` (`SKILL.md` for workflow,
  `references/commands.md` for flag detail). Agents load the skill from the
  installed package, so stale skill text is a live bug.
- Python 3.10+, fully typed public API, ruff line length per `pyproject.toml`.
  `errors.py` imports `discogs_sdk` lazily inside functions (see the `PLC0415`
  note in `pyproject.toml`); keep that pattern there.
- Run `git` directly, never `git -C`.

### 3.1 Code map (files touched)

- `src/agent_discogs/__init__.py` - `AliasGroup` and the `cli` group callback
  (`@click.group(cls=AliasGroup, invoke_without_command=True)`, currently takes
  only `ctx`). `AliasGroup.invoke` is the last-resort exception boundary
  (`sys.exit(1)` after printing `format_error`). `_HELP_TEXT` is the hand-written
  `--help`.
- `src/agent_discogs/client.py` - `get_client()` module singleton:
  `Discogs(token=..., cache=True, cache_dir=CACHE_DIR)`; `CACHE_DIR`;
  `has_token()`.
- `src/agent_discogs/errors.py` - `ErrorInfo` (frozen dataclass; `asdict` of it
  minus `None`s is the JSON error body), `classify()`, `format_error()`,
  `error_document()`, `format_error_json()`, `fail()`.
- `src/agent_discogs/pagination.py` - `fetch_page()` (one `_send`),
  `fetch_filtered_page()` (loop of up to `MAX_API_CALLS = 5` fetches,
  over-fetching `limit * 3` rows per API page, `capped` when the loop exhausts).
- `src/agent_discogs/commands/get.py` - `_dispatch()` runs one handler per ref,
  catches `Exception` per ref: single ref -> `fail()`; several refs -> a `✗`
  text block or `{"ref", "error"}` JSON item per failure, exit 1 if any failed.
- `src/agent_discogs/commands/search.py` - same `fail()` pattern.
- `src/agent_discogs/commands/status.py` - no API call; not part of the footer.

### 3.2 Why `call_on_close` is the emission point

click's `Context.__exit__` calls `close()` (running `call_on_close` callbacks)
on **any** exit of the context, including `SystemExit` raised by `fail()` or
`AliasGroup.invoke`. `BaseCommand.main` runs `with self.make_context(...) as ctx:`.
So a callback registered in the group callback runs after every subcommand,
success or failure, after the command's own output. That is what makes the
footer show up on the 429 that matters most. `--version` is an eager option
that exits before the group callback, so nothing is registered; `--help` on a
subcommand runs the group callback but produces no events, so the footer rule
"no events, no footer" (section 4.2) keeps it silent.

## 4. New module: `src/agent_discogs/trace.py`

One process-wide collector shared by the footer and the panel. Module-level
state is fine (one invocation per process); `begin()` resets it so `CliRunner`
tests are isolated.

### 4.1 State and API

```python
"""Per-invocation request trace: the budget footer and the --debug panel."""

LOW_BUDGET = 10          # remaining requests at which the footer warns.
                         # One client-side scan costs up to MAX_API_CALLS (5)
                         # requests; two back-to-back scans can spend 10.
CACHE_TTL = 3600.0       # passed to Discogs(cache_ttl=...) and shown by --debug

@dataclass
class ScanNote:          # one per fetch_filtered_page call
    api_calls: int; rows: int; kept: int; capped: bool

class _Trace:
    events: list[RequestEvent]
    scans: list[ScanNote]
    started: float           # time.perf_counter() at begin()
    debug: bool
    stdout: _CountingWriter | None

def begin(ctx: click.Context, *, debug: bool) -> None
    # reset state; record start; if debug: wrap sys.stdout in _CountingWriter and
    # attach a logging.StreamHandler(sys.stderr) at DEBUG to logging.getLogger("discogs_sdk")
    # with format "[sdk] %(message)s"; then ctx.call_on_close(finish)

def record(event: RequestEvent) -> None        # passed as on_request=; always appends
def note_scan(note: ScanNote) -> None          # called by fetch_filtered_page

def render_footer(events: Sequence[RequestEvent], *, authenticated: bool) -> str | None
def render_panel(trace: _Trace, *, cache_dir: Path, elapsed_ms: float) -> str

def finish() -> None
    # 1. restore sys.stdout if wrapped (so tests do not leak the wrapper)
    # 2. footer = render_footer(events, authenticated=has_token()); if footer: print to stderr
    # 3. if debug: print render_panel(...) to stderr, after the footer
    # 4. remove the logging handler if attached
```

Import `RequestEvent` under `TYPE_CHECKING` only; `record()` needs no SDK
symbol at runtime.

`_CountingWriter`: a thin proxy around the current `sys.stdout` that counts
`chars` and `lines` (newlines) in `write()`, forwards `flush()` and everything
else via `__getattr__`. Installed only under `--debug`.

### 4.2 Footer rules (normative)

Input: the recorded events, and whether `DISCOGS_TOKEN` is set.

1. No events -> `None` (nothing printed). Covers `status`, `skills`, `cache clear`,
   `--help`, argument errors before any request.
2. `network = [e for e in events if e.source == "network"]`, `n = len(network)`.
3. `n == 0` (only cache hits) -> `api: 0 requests · cached`
4. Otherwise `base = f"api: {n} request" + ("" if n == 1 else "s")`.
5. `rl` = the `ratelimit` of the **last** network event that has one
   (iterate reversed; skip `None`). No such event -> return `base`.
6. `line = base + f" · {rl.remaining}/{rl.limit} left this minute"`.
7. If `rl.remaining <= LOW_BUDGET`, append:
   - authenticated: ` ⚠ pause ~60s before uncached calls`
   - not authenticated: ` ⚠ set DISCOGS_TOKEN for 60/min, or pause ~60s`
8. Return `line`.

Examples, exactly:

```
api: 3 requests · 43/60 left this minute
api: 1 request · 59/60 left this minute
api: 0 requests · cached
api: 4 requests · 6/60 left this minute ⚠ pause ~60s before uncached calls
api: 1 request · 3/25 left this minute ⚠ set DISCOGS_TOKEN for 60/min, or pause ~60s
api: 2 requests
```

`·` is U+00B7, `⚠` is U+26A0 (the codebase already prints `✗` and `→`; no
new encoding concern).

### 4.3 Panel format (`--debug`)

Printed after the footer, stderr:

```
── debug ──────────────────────────────────────────────
total     412ms
GET /database/search?q=Nine+Inch+Nails&type=artist&per_page=15&page=1   200   190ms
GET /database/search?q=Nine+Inch+Nails&type=artist&per_page=15&page=2   200   190ms  ×2 attempts
GET /masters/3719                                                      200   cache
scan      2 API calls, 30 rows fetched, 5 kept, not capped
cache     ~/.cache/agent-discogs  3.1 MB  ttl 1h
output    842 chars, 12 lines, ≈210 tokens
```

- One row per event in order. URL column: `urllib.parse.urlsplit(e.url)` ->
  `path` plus `?query` when present (base URL stripped). Third column: `cache`
  for cache hits, else `f"{e.elapsed_ms:.0f}ms"`; append `  ×{attempts} attempts`
  when `attempts > 1`. Left-align the URL column to the longest URL in this
  invocation; do not truncate (debug is opt-in).
- `scan` row per `ScanNote`; `capped` renders `capped at 5 API calls` /
  `not capped`. Omit the row when there were no scans.
- `cache` row: `CACHE_DIR` with `Path.home()` replaced by `~`; size is
  `sum(p.stat().st_size for p in CACHE_DIR.glob("cache.db*"))` (SQLite may
  leave `-wal`/`-shm` files) formatted with one decimal in KB/MB; `ttl` from
  `CACHE_TTL` rendered as `1h` (`f"{CACHE_TTL/3600:g}h"`).
- `output` row from the `_CountingWriter`; `≈tokens` is `chars // 4`, labelled
  with `≈`. No tokenizer dependency.
- `total` is `perf_counter()` since `begin()`, in ms with no decimals.

## 5. Phase 1 - budget footer

### 5.1 Dependency bump

`pyproject.toml`: `"discogs-sdk>=0.5.0"`. Run `uv lock` (keep the 3.15
pydantic branch; see AGENTS.md - do not add `[tool.uv] environments`).

### 5.2 `client.py`

```python
from agent_discogs import trace

_client = Discogs(
    token=token,
    cache=True,
    cache_dir=CACHE_DIR,
    cache_ttl=trace.CACHE_TTL,
    on_request=trace.record,
)
```

`trace.record` is always attached; it is an append to a list of at most a
handful of events, so there is no reason to gate it on a flag.

### 5.3 `__init__.py` group callback

```python
@click.group(cls=AliasGroup, invoke_without_command=True)
@click.version_option(package_name="agent-discogs", prog_name="agent-discogs")
@click.option(
    "--debug",
    is_flag=True,
    envvar="AGENT_DISCOGS_DEBUG",
    help="Print request trace and timings to stderr.",
)
@click.option("--cached", is_flag=True, help="Serve only from cache; fail on a miss.")
@click.option("--fresh", is_flag=True, help="Bypass the cache for this command.")
@click.pass_context
def cli(ctx, debug, cached, fresh):
    if cached and fresh:
        raise click.UsageError("--cached and --fresh are mutually exclusive.")
    trace.begin(ctx, debug=debug)
    if cached:
        ctx.with_resource(get_client().cache_only())
    elif fresh:
        ctx.with_resource(get_client().no_cache())
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())
```

`--debug`, `--cached`, `--fresh` are **group** options: they precede the
subcommand (`agent-discogs --cached price @r847868`). Phase 1 only needs
`--debug` wired to `trace.begin`; add the two cache flags in Phase 2 but keep the
callback shape above so the file is edited once. `ctx.with_resource()` enters the
context manager and exits it when the context closes, i.e. around the whole
subcommand.

### 5.4 `errors.py`: richer 429

In `classify()`'s `RateLimitError` branch, read `exc.ratelimit`:

- message: `Rate limited: {remaining}/{limit} requests left this minute.` when
  `ratelimit` is present, else the current `Rate limited.` wording.
- hint unchanged in shape: `Retry in {n}s.` / `Wait a moment and retry.` +
  ` 60 req/min with DISCOGS_TOKEN, 25 without.`
- `ErrorInfo` gains `limit: int | None = None` and `remaining: int | None = None`,
  populated here only. They flow into the JSON envelope automatically via
  `asdict` (Nones are dropped), so `error.remaining` sits next to
  `error.retry_after`.

### 5.5 Tests

New `tests/test_trace.py`:
- `render_footer`: each rule in 4.2 - no events; cache-only; singular/plural;
  budget from the *last* network event with headers (build events by hand with
  `RequestEvent(...)`); threshold at exactly `LOW_BUDGET` (warns) and
  `LOW_BUDGET + 1` (does not); authenticated vs not wording; no headers at all
  -> bare `api: N requests`.
- `_CountingWriter` counts chars and lines across multiple writes and forwards
  `flush`.

`tests/test_cli.py` (mocked `get_client`, so events must be recorded by the fake):
- In a fake handler, call `trace.record(RequestEvent(...))` as a side effect,
  then assert the footer is in `result.stderr` and **not** in `result.stdout`,
  for text and `--json`.
- Footer present on the failure path: fake raises after recording an event;
  `exit_code == 1`, JSON envelope on stdout parses, footer on stderr.
- `status` / `skills` / `--help` produce no footer (`result.stderr == ""`).
  Existing tests already assert `result.stderr == ""` in places; they keep
  passing because no events are recorded.

`tests/test_errors.py`: `RateLimitError(..., ratelimit=RateLimit(60, 60, 0))`
classifies to a message containing `0/60` and a JSON body with
`limit`/`remaining`; without `ratelimit` the current wording holds.

### 5.6 Docs

- `_HELP_TEXT`: `--debug` under Options; `AGENT_DISCOGS_DEBUG` under
  Environment; a short "Budget" paragraph after the Environment block:
  > After any command that talks to the API, stderr ends with a budget line,
  > e.g. `api: 3 requests · 43/60 left this minute`. `⚠` means fewer than
  > ~10 requests remain in the moving 60s window: pause ~60s or stick to
  > cached refs. Cache hits cost nothing (`api: 0 requests · cached`).
- `README.md`: new section `## Budget` between "Caching" and "Error handling"
  with the same content plus the cost table (`get <noun>` 1 request; `tracks`,
  `price` 1; client-side scans - `search --release-type`, `get releases --role`,
  `get releases @l --year` - up to 5; any repeat within 1h 0). Note it is on
  stderr and never inside `--json` output.
- Skill `SKILL.md`: new `## Budget` section after "Token Efficiency": what the
  line means, the cost table, what to do at `⚠` (pause ~60s, or use `--cached`
  once Phase 2 ships), and that unauthenticated runs get 25/min. Add a bullet to
  "Error Recovery": `rate_limited` now reports `remaining/limit`.
- `references/commands.md` "Global Flags" table: `--debug`.

## 6. Phase 2 - `--cached` and `--fresh`

### 6.1 Behaviour

- `--cached`: whole command runs inside `client.cache_only()`. Any request the
  cache cannot serve raises `CacheMissError` **before network I/O**. Through the
  existing per-ref error handling in `get._dispatch` this means:
  - single ref: `fail()` -> text `✗ Not cached: GET /marketplace/price_suggestions/847868`
    with hint `Rerun without --cached to fetch it from the API.`, or JSON
    `{"error":{"code":"cache_miss","message":...,"hint":...}}`; exit 1.
  - several refs: hits print normally, each miss becomes its `✗` block or
    `{"ref","error"}` item; exit 1 if any missed. Zero requests spent either way.
  - a client-side scan whose Nth API page is not cached fails at that page,
    which is exactly where the money would have gone.
- `--fresh`: whole command inside `client.no_cache()`; every request goes to
  the network. SDK semantics (verified in 0.4.0 `_send()`: the bypass clears
  `use_cache`, and both the read and the write are gated on it): a bypassed
  response is **not** stored, so a later call without `--fresh` can still serve
  the older cached entry until its 1h TTL expires. Document this in README and
  the skill; do not change the SDK for it.
- Both flags together: click `UsageError` (exit 2), message
  `--cached and --fresh are mutually exclusive.`
- The footer still prints: `api: 0 requests · cached` for a fully served
  `--cached` run; a `--cached` run that failed on a miss has no network events
  and prints the same line (nothing was spent).

### 6.2 `errors.py`

Add a `CacheMissError` branch to `classify()` **before** the generic
`DiscogsError`/`Exception` fallbacks:

```python
if isinstance(exc, CacheMissError):
    split = urlsplit(exc.url)
    where = split.path + (f"?{split.query}" if split.query else "")
    return ErrorInfo(
        "cache_miss",
        f"Not cached: {exc.method} {where}",
        "Rerun without --cached to fetch it from the API.",
    )
```

### 6.3 Tests

`tests/test_cli.py`:
- `--cached` and `--fresh` together -> exit 2 and the message.
- `--cached` enters `cache_only()`: the fake client exposes a
  `cache_only()` context manager that records entry/exit; assert it was entered
  before the handler ran and exited after. Same for `--fresh` / `no_cache()`.
  (The group callback calls `get_client()` from `agent_discogs.client`; patch
  `agent_discogs.get_client` or, cleaner, have the group import
  `agent_discogs.client` and patch `agent_discogs.client.get_client`.)
- `--cached price @r10 @r1 @r20` where the fake raises `CacheMissError` for
  `@r1`: two normal blocks, one `✗ Not cached:` block, exit 1; JSON variant has
  `error.code == "cache_miss"` on that item.

`tests/test_errors.py`: `CacheMissError("GET", "https://api.discogs.com/releases/847868?curr_abbr=USD")`
-> message `Not cached: GET /releases/847868?curr_abbr=USD`, code `cache_miss`.

### 6.4 Docs

- `_HELP_TEXT`: `--cached` / `--fresh` under Options with one-line meanings.
- `README.md` "Caching" section: describe both flags, the multi-ref behaviour,
  and that `--cached` is exact (it uses the real cache key at the real moment;
  there is no separate "is it cached" query because the answer depends on the
  full request sequence a command issues).
- Skill `SKILL.md` "Budget" section: the pattern *try everything with
  `--cached`, then spend only on the misses*; when it matters (parallel agents
  sharing one cache dir and one IP budget; after a `⚠`). Add an anti-pattern
  bullet: do not use `--fresh` by default; 1h-old catalogue data is fine, use it
  for `price` when you need current market numbers.
- `references/commands.md` "Global Flags": `--cached`, `--fresh`; note the
  `cache_miss` error code.

## 7. Phase 3 - `--debug` panel

### 7.1 SDK log passthrough

Already specified in `trace.begin()`: when `debug`, attach a
`logging.StreamHandler(sys.stderr)` with formatter `[sdk] %(message)s` at
`DEBUG` to `logging.getLogger("discogs_sdk")` and set that logger's level to
`DEBUG`; remove the handler in `finish()`. This streams the SDK's own
`Cache hit` / `HTTP request` / `HTTP response ... (Nms)` / `Retrying ...
waiting Ns` lines in real time, which is the only way to see a 60s retry sleep
while it is happening.

### 7.2 Scan instrumentation in `pagination.fetch_filtered_page`

Count inside the existing loop: `api_calls += 1` per `fetch_page` call,
`rows += len(result.items)`. Before the final `return PageResult(...)`, call
`trace.note_scan(ScanNote(api_calls, rows, len(collected), capped))`. No
behaviour change.

### 7.3 Panel and counter

Implement `render_panel` and `_CountingWriter` per section 4.3/4.1.

### 7.4 Tests

`tests/test_trace.py`:
- `render_panel`: URL column strips the base URL and keeps the query; cache
  hit shows `cache`; `attempts > 1` shows `×N attempts`; scan row wording for
  capped/not capped; output row arithmetic (`chars // 4`); cache row uses `~`
  and handles a missing cache directory (0 B).
- `render_panel` never touches the network or the client: it takes plain values.

`tests/test_cli.py`:
- `--debug` puts `── debug ──` on stderr, nothing on stdout; stdout content is
  byte-identical to the same command without `--debug`.
- `AGENT_DISCOGS_DEBUG=1` via `CliRunner(env=...)` behaves like the flag.
- With `--debug`, a `discogs_sdk` logger `debug()` call during the command
  appears on stderr prefixed `[sdk] `; without the flag it does not.

`tests/test_pagination.py`: a filtered scan records one `ScanNote` with the
expected `api_calls`/`rows`/`kept`/`capped` (reuse the existing fake-fetch
setup there).

### 7.5 Docs

- `_HELP_TEXT` and `references/commands.md`: `--debug` already added in Phase 1;
  extend the description to "request rows, timings, scan stats, cache size,
  output size".
- `README.md`: `## Debugging` section after "Budget": the flag, the env var, a
  sample panel, and that everything goes to stderr.
- Skill: one line only, under "Error Recovery": "Unexpected cost or a stall?
  Re-run with `--debug` to see each request, retries and cache hits." Agents
  should not leave it on.

## 8. Verification

1. `just qa` and `just test` green after each phase.
2. Smoke test against the real API with `DISCOGS_TOKEN` set (each line: the
   command, then what stderr must end with):
   - `agent-discogs cache clear` -> no footer.
   - `agent-discogs get release @r847868` -> `api: 1 request · N/60 left this minute`.
   - same command again -> `api: 0 requests · cached`.
   - `agent-discogs --cached price @r847868` (never fetched) -> `✗ Not cached: GET /marketplace/price_suggestions/847868`, exit 1, footer `api: 0 requests · cached`.
   - `agent-discogs --fresh get release @r847868` -> `api: 1 request · ...`.
   - `agent-discogs --debug get releases @a3857 --role Main --limit 5` -> footer, then a panel with request rows and a `scan` row.
   - `agent-discogs get release --json @r847868 2>/dev/null | jq .ref` -> `"@r847868"` (stdout untouched).
   - `agent-discogs --cached --fresh status` -> exit 2.
   - Unset `DISCOGS_TOKEN`, run `get release @r847868` once -> footer shows `/25`.
   Do not try to reach `⚠` live; the unit tests cover the threshold.
3. `agent-discogs skills get core --full | grep -n "Budget"` shows the new
   section from the installed package (`uv run agent-discogs ...`).

## 9. Acceptance criteria

- After any command that performed at least one request, stderr ends with the
  footer per 4.2; commands with no requests print no footer; stdout is
  unchanged in every mode.
- The footer appears on failure paths (exit 1) as well as success.
- `rate_limited` errors report `remaining/limit` when the SDK provides it, in
  text and in the JSON envelope.
- `--cached` performs zero network requests; a miss is a `cache_miss` error
  handled per ref exactly like other per-ref failures; `--fresh` bypasses the
  cache; both together is a usage error.
- `--debug` / `AGENT_DISCOGS_DEBUG=1` prints the panel per 4.3 on stderr after
  the footer, plus the SDK's own debug log lines, and leaves stdout
  byte-identical.
- `_HELP_TEXT`, `README.md`, `SKILL.md`, `references/commands.md` all describe
  the footer, the two cache flags and `--debug`; `tests/test_doc_examples.py`
  still passes (no new refs).
- `just qa` and `just test` pass; version bumped in `pyproject.toml` (new
  flags and output: minor bump) and released per AGENTS.md.

## 10. Non-goals

- Any form of local throttling or automatic waiting.
- A "would this be cached?" query command (`cache has ...`): it would have to
  duplicate every handler's URL construction and would still be wrong for
  partial scans. `--cached` is the exact oracle.
- `cache list` / dumping cache keys: verbose and immediately stale.
- Putting the footer or panel on stdout in any mode, or a `warnings` key in
  `--json` documents.
- A tokenizer dependency for the `≈tokens` estimate.
