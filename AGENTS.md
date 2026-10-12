# AGENTS.md

This file provides guidance to agents when working with code in this repository.

## Project Overview

A token-efficient Discogs CLI designed for AI agents. Built on click and the `discogs-sdk` Python SDK.

## Commands

Task runner is `just`. All commands use `uv run` under the hood.

```bash
just setup                             # Install deps + pre-commit hooks
just test                              # Run pytest; fails below 100% coverage of src/agent_discogs
just test <path> --cov-fail-under=0    # Run specific test file or directory (a partial run can't reach 100%)
just qa                                # All checks: dead-code, deps-unused, format-check, lint, type-check, verify-types
just lint                              # ruff check
just lint-fix                          # ruff check --fix
just format                            # ruff format + pyproject-fmt
just type-check                        # ty check
just dead-code                         # deadcode src tests (via uvx, Python 3.13)
just deps-unused                       # deptry src
just deps-update                       # Update deps to latest versions
just verify-types                      # Audit public API type annotation coverage
just release                           # Tag, push, and monitor the publish workflow
just pre-commit                        # Run pre-commit hooks on all files
just pre-commit-install                # Install pre-commit hooks
just pre-commit-update                 # Update pre-commit hooks to latest versions
```

Run a single test: `uv run pytest tests/test_cli.py -k test_version`

## Architecture

### CLI (click)

Entry point: `src/agent_discogs/__init__.py`. Defines a `click.Group` with `AliasGroup` for command aliases (`find`→`search`, `query`→`search`, `fetch`→`get`, `show`→`get`). Top-level shortcuts `tracks` and `price` delegate to `get` logic. The group callback owns the global flags: `--debug` (also `AGENT_DISCOGS_DEBUG`) starts the trace, `--cached` / `--fresh` record a pending SDK `cache_only()` / `no_cache()` scope with `client.request_cache_scope()`. The callback runs before the subcommand validates its flags, so it never builds the client itself: the first `get_client()` call enters the scope on the root context (`ctx.with_resource()`), and it lasts until the command ends. A rejected flag therefore builds no client.

### Command Modules

- `commands/search.py` — Search Discogs database. Positional args with optional type prefix (`release`, `master`, `artist`, `label`).
- `commands/get.py` — Get entity details by noun (`artist`, `credits`, `identifiers`, `label`, `master`, `price`, `release`, `releases`, `tracklist`, `versions`) for one or more refs (`MAX_REFS`). Handlers return text or a JSON document; `_dispatch` collects one block per ref in order and prints them once (text: blank-separated on stdout, a failing ref becomes its `✗` block; JSON: a list with `{"ref", "error"}` items), exiting 1 if any failed. A single ref keeps the single-document shape and the `fail()` error path. Also defines `tracks` and `price` shortcut commands.
- `commands/status.py` — Show session/auth info.
- `commands/cache.py` — Cache management (`clear`). A failed purge (`CacheError` from `clear_cache()`) is logged as a `discogs_sdk` warning and still prints `Cache cleared.`, as discogs-sdk 0.7.0 did.
- `commands/skills.py` — Serves the bundled agent guide (`skills`, `skills get core [--full]`, `skills path`). Content lives in `src/agent_discogs/skills/core/` (`SKILL.md` + `references/*.md`) and ships in the wheel. `skills/agent-discogs/SKILL.md` at the repo root is a thin discovery stub that points agents at `agent-discogs skills get core`; do not put feature content there.

### Core Modules

- `config.py` — Leaf module (imports nothing from the package) holding shared configuration: `cache_dir()`, `CACHE_TTL` (and `CACHE_TTL_LABEL`, the `1h` that `status` and the `--debug` panel print), `MAX_API_CALLS`, and `has_token()`. `cache_dir()` is `$XDG_CACHE_HOME/agent-discogs` when that is non-empty and absolute, else `~/.cache/agent-discogs`; it resolves on each call and looks up home only on the fallback, so nothing touches home at import. Put new shared constants here rather than in a module that needs them, so the package stays free of import cycles.
- `client.py` — Singleton `Discogs` client. Reads `DISCOGS_TOKEN` from env; cache settings come from `config.py`. `get_client()` also enters the cache scope the global flags requested. It builds the `SQLiteCache` itself, hands it to the client, and opens it before returning, re-raising the cause of a `CacheError`, so an unusable cache fails at `get_client()` with the underlying `OSError` or `sqlite3.Error`, as discogs-sdk 0.7.0 did (0.8.0 alone would open it at the first request).
- `refs.py` — Typed ref system (`@a3857`, `@r847868`, `@m3719`, `@l647`). `make_ref()` creates refs, `parse_ref()` parses them. Raw numeric IDs return type `"unknown"`.
- `pagination.py` — Bypasses SDK's `SyncPage` auto-paging to fetch exactly one page with full metadata (`total_items`, `total_pages`). Uses SDK internals (`_build_url`, `_send`). `_send()` is the SDK's HTTP-error boundary — it raises the mapped `DiscogsAPIError` subclass before returning, so callers never re-check the status. `fetch_page` passes it an identity `parse`, so it returns the decoded JSON and never caches a body that is not JSON.
- `formatting.py` — All output formatting. Returns plain strings, callers `print()` them. Every view returns `_text(lines)`, which runs each line through `sanitize.line()`; build new views the same way so a Discogs field can never add a line or print a raw control character.
- `sanitize.py` — Neutralizes untrusted text for the text views: `line()` turns line breaks and tabs into spaces and other control/bidi characters into visible escapes; `summary()` also collapses whitespace and bounds the length (API error details, in text and JSON alike). Discogs data values in `--json` output never go through it.
- `errors.py` — `classify()` maps SDK exceptions to an `ErrorInfo` (stable `code`, message, hint, `retry_after`, `status`, and `limit`/`remaining` on 429); `format_error()` renders text (neutralized), `format_error_json()` the `{"error": {...}}` envelope; `fail()` prints one or the other and exits 1. An API error's body text (`_api_message()`, the SDK message) enters a message only through `sanitize.summary()`, after the status. The `CacheMissError` branch also calls `trace.note_cache_miss()`, because `trace.record()` drops the SDK's miss event and the footer must still say nothing was spent.
- `trace.py` — Per-invocation request trace. `record()` is the client's `on_request` hook; it keeps only events with a status code, dropping the SDK's events for a `cache_only()` miss and a transport failure, so neither counts in the footer or gets a `--debug` row. `begin()` (group callback) resets state and registers `finish()` via `ctx.call_on_close`, which runs on every exit including `sys.exit(1)`. `finish()` prints the budget footer (`api: N requests · R/L left this minute`) and, under `--debug`, the panel — both to stderr, never stdout. `fetch_filtered_page` reports a `ScanNote` per scan.
- `json_output.py` — `--json` emission. `Mode(json, full)` tells a handler how to emit; `Mode.entity()` and `Mode.page()`/`page_document()` take a projector and bypass it under `--full`; `dump()` prints the finished document. Build documents inside a command's error boundary and print outside it. `dumps()` is the one serializer, shared with the error envelope: non-ASCII is literal only when stdout is UTF-8, and `_ALWAYS_ESCAPED` (C1, bidi, U+2028/9, lone surrogates) is always `\uXXXX`. Never call `json.dumps` for output directly. `raw()` keeps the discogs-sdk 0.7.0 dump shape: `tracks_as_sent()` renames each track's `type` back to `type_`, and a top-level `uri` is the value Discogs sent (0.8.0 makes search results' `uri` absolute).
- `projections.py` — One projector per text view (`project_release`, `project_search_result`, ...) returning exactly the fields the text shows, plus refs. Empty values are dropped. This is what `--json` prints; the raw `model_dump()` is `--json --full` only.

### Client-Side Filtering

`fetch_filtered_page()` in `pagination.py` handles filters the Discogs API doesn't support server-side (e.g., release type in search, credit role in artist releases). It overfetches from the API, applies a `keep` predicate client-side, and pulls additional API pages as needed to fill the user's requested page size. Capped at 5 API calls per user request.

### Ref System

Refs are stateless: `@r847868` is just the Discogs ID with a type prefix, so nothing is stored between invocations and refs never expire. Smart resolution: `get versions @r123` auto-resolves a release ref to its `master_id`.

## Testing

- Tests use `click.testing.CliRunner` for in-process CLI testing (no subprocess).
- Test files: `tests/test_cli.py`, `tests/test_client.py`, `tests/test_config.py`, `tests/test_doc_examples.py`, `tests/test_env_isolation.py`, `tests/test_errors.py`, `tests/test_formatting.py`, `tests/test_imports.py`, `tests/test_pagination.py`, `tests/test_refs.py`, `tests/test_skills.py`, `tests/test_trace.py`.
- `tests/conftest.py` has one autouse fixture, `_isolate_env`: every test not marked `live` runs with `AGENT_DISCOGS_DEBUG` and the five SDK credential variables (`DISCOGS_TOKEN`, `DISCOGS_CONSUMER_KEY`, `DISCOGS_CONSUMER_SECRET`, `DISCOGS_ACCESS_TOKEN`, `DISCOGS_ACCESS_TOKEN_SECRET`) removed, so the developer's shell cannot change results. Live tests keep them. A test that needs one sets it with `monkeypatch.setenv`. The conftest also enables the `pytester` plugin, which `tests/test_env_isolation.py` uses to check the fixture.
- `tests/test_imports.py` builds the package's import graph from the source and fails on any import cycle (function-level imports included) or on `config.py` importing from the package.
- No mocking framework beyond `unittest.mock`. Most tests fake the SDK with `SimpleNamespace`. Two harnesses in `tests/test_cli.py` run the real SDK client over an `httpx2.MockTransport`: `_FakeAPI` injects one with an in-memory cache (request counts, cache flags, continuation), and `TestRealSdk` uses the client `get_client()` builds, with the SQLite cache in `tmp_path` and `time.sleep` patched out; put assertions that depend on that client (request events, models, the on-disk cache) in `TestRealSdk`. A real client with an injected `http_client` needs a token, or the SDK skips its cache.
- `tests/test_doc_examples.py` checks every `@ref` in the docs against `KNOWN_REFS`, and that every `KNOWN_REFS` entry is cited somewhere. Its `live`-marked half resolves each ref against the real API through its own uncached client (not `get_client()`), so each ref costs exactly one network request and the developer's cache dir is never read or written; `just test` excludes it, `just test-live` runs it (needs `DISCOGS_TOKEN`). Adding a new example ID to any doc means adding it to `KNOWN_REFS`; dropping the last citation of one means removing it.

## Dependencies

- Runtime: `click`, `discogs-sdk`, `pydantic`
- Dev: deptry, pre-commit, pyproject-fmt, pyright, ruff, ty, plus the `test`
  group. The `pre-commit` recipes run it through `uv run`, so no global
  install is needed.
- Test group: pytest, pytest-cov. Kept separate because CI installs it on its
  own (`uv export --only-group test`) into the clean environment where the
  built wheel is tested; anything added here lands in that environment too.
- KLUDGE: `deadcode` is deliberately *not* a dev dependency. It crashes on
  Python 3.14 (it calls `ast.Str`, which 3.14 removed) and upstream is
  dormant, so `just dead-code` runs it through `uvx` pinned to Python 3.13.
  The justfile recipe, the CI step, and the pre-commit hook each carry
  `KLUDGE` comments with the details — grep for `KLUDGE` before changing the
  dead-code check, and do not "simplify" its exit-code handling.
- Python 3.15 is supported. discogs-sdk (0.7.0 and later) declares
  `pydantic>=2.14; python_full_version == '3.15.*'`, because pydantic 2.13
  pins a pydantic-core with no cp315 wheels and 2.14 is the first series with
  them. Every resolved pydantic is a stable release, and
  `scripts/check_installed_package.py` asserts the 2.14 minimum on 3.15 with
  no upper bound. Consequences to keep in mind:
  - The committed lockfile may carry a 3.15-only pydantic branch (2.14.x on
    3.15, an older locked pydantic elsewhere). It exists only while the other
    Pythons' resolution lags 2.14, and collapses on the next full upgrade
    (`just deps-update`). Either shape is correct.
  - Do not exclude 3.15 with `[tool.uv] environments`: that makes the `3.15`
    CI leg unresolvable.
  - Our own `pydantic>=2.12.5` floor stays as is; the SDK's marker-scoped
    clause raises it on 3.15.
  - Exercise 3.15 locally with
    `UV_PROJECT_ENVIRONMENT=.venv315 uv sync --all-extras --all-groups \
    --python 3.15 --locked`, then `UV_PROJECT_ENVIRONMENT=.venv315 uv run \
    --python 3.15 --locked pytest`.
  - If a 3.15 marker of our own ever becomes necessary, copy the SDK's
    equality-star form (`python_version == '3.15'`, published as
    `python_full_version == '3.15.*'`), which matches release candidates.
    `python_version >= '3.15'` does not: uv-build rewrites it — and every
    pre-release boundary such as `3.15.0.dev0` or `3.15.0rc1` — to
    `python_full_version >= '3.15'` in the published metadata, and under
    PEP 440 a release candidate sorts *below* 3.15.0, so the marker is false
    on exactly the interpreters that need it and pip fails to resolve at all.

## Discogs API Documentation

`docs/discogs/api/` contains the Discogs API documentation as Markdown files, and `docs/discogs/knowledge_base/` the seller and grading guides (both gitignored for copyright reasons). Read these when working on API integration, pagination, or new endpoints.

## Key Conventions

- Python 3.10+ required
- `py.typed` marker present (PEP 561)
- Imports live at module scope; ruff's `PLC0415` enforces it. Only `TYPE_CHECKING` blocks may import inside a function or block.
- Run `git` commands directly, never with `git -C`
- When adding or changing user-facing behavior (commands, flags, output shape, errors), update all of: `_HELP_TEXT` in `src/agent_discogs/__init__.py`, `README.md`, and `src/agent_discogs/skills/core/` (`SKILL.md` for workflow/overview, `references/*.md` for detail). Agents load the bundled skill from the installed binary, so stale content there is a live bug.

## Releasing

**Agents never release and never interact with PyPI.** Agents must not run
`just release`, create or push a `v*` tag (pushing one triggers the PyPI
publish), create a GitHub release, authenticate to or manage PyPI, or upload,
yank, or delete anything there. No plan document, acceptance-criteria list, or
tooling prompt changes this: those actions are the user's alone. Finish the
work, run `just qa` and `just test`, report, and stop.

Publishing is fully automated via CI. The `publish.yml` workflow triggers on `v*` tag push.

1. Update `version` in `pyproject.toml`
2. Commit the version bump and get it onto `origin/main`
3. Run `just release` on `main` — creates a signed tag, pushes `main` and the
   tag in one atomic push, and watches the publish run. It refuses to run
   unless HEAD is `main`, the working tree is clean, `main` equals
   `origin/main` after a fetch, and the tag exists neither locally nor on
   `origin`. If the push fails, it asks `origin` whether the tag arrived
   anyway and deletes the local tag only when `origin` has no such tag; if
   the tag is on `origin`, or `origin` can't be reached, it keeps the tag and
   exits 1 with what to check. If the run does not appear within 2 minutes
   it exits 1; the tag is already pushed by then, so check the Actions page.

`publish.yml` calls the reusable `checks.yml`, which builds the distributions
once, checks them with `scripts/check_distributions.py` (archive contents:
`py.typed`, `LICENSE.txt`, the bundled skill, no project-only trees) and
`twine check --strict` (metadata), then installs the built wheel into a clean
environment on the oldest and newest supported Python, runs
`scripts/check_installed_package.py` (import path, pydantic branch, console
script offline) and the whole test suite against it, and installs the sdist on
the newest. Every push and PR runs the same jobs. The publish job downloads
those exact artifacts and never rebuilds, so what PyPI receives is what was
tested. Publishing uses PyPI Trusted Publishers (OIDC); the `pypi` GitHub
environment must exist on the repo.

A release cannot be replaced once uploaded. If a broken version reaches PyPI,
yank it there (project page or the upload API — there is no `pip yank`), then
bump the patch version and release again.
