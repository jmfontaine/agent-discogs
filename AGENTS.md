# AGENTS.md

This file provides guidance to agents when working with code in this repository.

## Project Overview

A token-efficient Discogs CLI designed for AI agents. Built on click and the `discogs-sdk` Python SDK.

## Commands

Task runner is `just`. All commands use `uv run` under the hood.

```bash
just setup                             # Install deps + pre-commit hooks (main checkout only; see below)
just test                              # Run pytest; fails below 100% coverage of src/agent_discogs
just test <path> --cov-fail-under=0    # Run specific test file or directory (a partial run can't reach 100%)
just test-live                         # Run only the `live` tests against the real Discogs API (needs DISCOGS_TOKEN; skipped without it)
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
just pre-commit-install                # Install pre-commit hooks (main checkout only)
just pre-commit-update                 # Update pre-commit hooks to latest versions
```

Run a single test: `uv run pytest tests/test_cli_basics.py -k test_version`

### Worktrees

Every linked worktree (`git worktree add`) shares the main checkout's `.git/hooks`. In a linked worktree, never run `just setup`, `just pre-commit-install`, `pre-commit install`, `prek install`, or anything else that writes `.git/hooks` or sets `core.hooksPath`: an installer replaces the hook every checkout uses, moving the existing one to `pre-commit.legacy` to run in pre-commit's "migration mode" (a prek hook there fails every commit), and points the new hook at the worktree's `.venv`, which disappears with the worktree. Get a working environment with `uv sync --all-extras --all-groups` instead. Both recipes check for a linked worktree (`git rev-parse --git-dir` differs from `--git-common-dir`): `just setup` installs the dependencies and skips the hook, `just pre-commit-install` exits 1. The guard does not cover running an installer directly. If a commit hook fails for a reason unrelated to your change, stop and report it; never bypass it with `--no-verify`.

## Architecture

### CLI (click)

Entry point: `src/agent_discogs/__init__.py`. Defines a `click.Group` with `AliasGroup` for command aliases (`find`→`search`, `query`→`search`, `fetch`→`get`, `show`→`get`). Top-level shortcuts `tracks` and `price` delegate to `get` logic. The group callback owns the global flags: `--color` is recorded first (`style.configure()`), `--debug` (also `AGENT_DISCOGS_DEBUG`) starts the trace, `--cached` / `--fresh` record a pending SDK `cache_only()` / `no_cache()` scope with `client.request_cache_scope()`. The callback runs before the subcommand validates its flags, so it never builds the client itself: the first `get_client()` call enters the scope on the root context (`ctx.with_resource()`), and it lasts until the command ends. A rejected flag therefore builds no client.

### Command Modules

- `commands/search.py` — Search Discogs database. Positional args with optional type prefix (`release`, `master`, `artist`, `label`). `--added-since` (`search release` only) is a thin wrapper around the SDK's experimental `discogs_sdk.experimental.releases_added_since()` (with `per_page=100` and the command's `--max-requests`): it returns the whole window of looked-up `Release` models, filters `--release-type` on their formats (unofficial goes to Discogs as `format=Unofficial Release` unless `--format` is given), and applies `--limit` only when the user passed it (click's parameter source). Its conflicts (`--sort`, `--desc`, `--page`, `--after`, a non-release search) are rejected before any request.
- `commands/get.py` — Get entity details by noun (`artist`, `credits`, `identifiers`, `label`, `master`, `price`, `release`, `releases`, `tracklist`, `versions`) for one or more refs (`MAX_REFS`). Handlers return text or a JSON document; `_dispatch` collects one block per ref in order and prints them once (text: blank-separated on stdout, a failing ref becomes its `✗` block; JSON: a list with `{"ref", "error"}` items), exiting 1 if any failed. A single ref keeps the single-document shape and the `fail()` error path. Also defines `tracks` and `price` shortcut commands.
- `commands/status.py` — Show version, the SDK's `auth_mode` (from `client.auth_mode()`), and the cache location. Never calls `get_client()`, so it never opens or creates the cache.
- `commands/cache.py` — Cache management (`clear`, the only action, so the handler takes no `action` value). `clear_cache()` is inside the command's error boundary: success prints `Cache cleared.` (`{"cleared": true}` under `--json`) only when it returns; a purge or open failure (`CacheError`) exits 1 with `cache_error`, in text or the JSON envelope. The CLI deletes no database and attaches no logging handler.
- `commands/skills.py` — Serves the bundled agent guide (`skills`, `skills get core [--full]`, `skills path`). Content lives in `src/agent_discogs/skills/core/` (`SKILL.md` + `references/*.md`) and ships in the wheel. `skills/agent-discogs/SKILL.md` at the repo root is a thin discovery stub that points agents at `agent-discogs skills get core`; do not put feature content there.

### Core Modules

- `config.py` — Leaf module (imports nothing from the package) holding shared configuration: `cache_dir()` and `CACHE_TTL` (and `CACHE_TTL_LABEL`, the `1h` that `status` and the `--debug` panel print). `cache_dir()` is `$XDG_CACHE_HOME/agent-discogs` when that is non-empty and absolute, else `~/.cache/agent-discogs`; it resolves on each call and looks up home only on the fallback, so nothing touches home at import. Put new shared constants here rather than in a module that needs them, so the package stays free of import cycles.
- `client.py` — Singleton `Discogs` client. Passes no credentials: the SDK resolves them from the `DISCOGS_*` environment variables (token, then OAuth, then consumer key/secret), so never check one variable yourself; `auth_mode()` reads the selected mode from a throwaway client built without a cache. Cache settings come from `config.py`. Built with `max_retries=0`: the CLI never retries or sleeps on the caller's behalf, so each request that reaches the network is sent once. `get_client()` also enters the cache scope the global flags requested. Neither step does I/O: the SDK opens its SQLite cache at the first request that needs it, so an unusable cache raises `CacheError` inside the command's error boundary (a `cache_error` envelope under `--json`), and a `no_cache()` (`--fresh`) command never opens it. Nothing in the CLI deletes a cache database.
- `refs.py` — Typed ref system (`@a3857`, `@r847868`, `@m3719`, `@l647`). `make_ref()` creates refs, `parse_ref()` parses them. Raw numeric IDs return type `"unknown"`.
- `pagination.py` — Reads exactly one page with full metadata (`total_items`, `total_pages`) through the SDK's public `SyncPage.fetch()`, where iterating a `SyncPage` would fetch the next page as soon as the current one runs out. Callers pass a `Listing`: the SDK's paginated resource method with its filters bound (`functools.partial(client.search, **params)`, `label.releases.list`), which `fetch_page` calls with `page`/`per_page`. The SDK owns endpoint paths, item keys, models, and the envelope checks; a malformed page raises `DiscogsError`, and a page that fails to parse is never cached. Never reach into SDK internals here.
- `formatting.py` — All output formatting. Returns plain strings, callers `print()` them. Every view returns `_text(lines)`, which runs each line through `sanitize.line()`; build new views the same way so a Discogs field can never add a line or print a raw control character. A line is a string, or a list of parts: text, which may hold Discogs values, and `style.Span`s for the CLI's own framing (`_ref()`, `_label()`, dimmed counts and footer prefixes). With color off the parts are joined and neutralized as one string, so the output is exactly the plain text; with color on each text run is neutralized and each span painted around its neutralized text. Never put a Discogs value in a `Span`.
- `sanitize.py` — Neutralizes untrusted text for the text views: `line()` turns line breaks and tabs into spaces and other control/bidi characters into visible escapes; `summary()` also collapses whitespace and bounds the length (API and connection error details, in text and JSON alike). Discogs data values in `--json` output never go through it.
- `style.py` — Terminal color, for a person at a terminal and never an agent. `enabled(stream)` answers whether that stream gets color: `--color always`/`never` decide, and `auto` (the default) colors only a terminal (`isatty()`), with `NO_COLOR` unset or empty and `TERM` not `dumb`; outside a command it is off. The mode lives in `ctx.meta` (`configure()`), so it is fresh per invocation; every `--json` flag has the `off_for_json` callback, which turns color off for the whole command, stderr included. `paint(text, style, color=...)` wraps already-neutralized text in the SGR code of a framing style when `color` is true; `_SGR` is the one palette. Callers decide per stream: text views on stdout (`_text`), `format_error(color=...)` on stderr (or stdout for a multi-ref block), and the footer and `--debug` panel on stderr. `--help` and `skills` never call it.
- `errors.py` — `classify()` maps SDK exceptions to an `ErrorInfo` (stable `code`, message, hint, `retry_after`, `status`, `limit`/`remaining` on 429, and `path` on `cache_error`); `format_error()` renders text (neutralized), `format_error_json()` the `{"error": {...}}` envelope; `fail()` prints one or the other and exits 1. The 401 (`auth_required`) and 429 (`rate_limited`) advice follows `client.auth_mode()`, the mode `status` reports: a 401 names the credentials that were sent (`_REJECTED`) and points at `agent-discogs status`; a 429 tells only an unauthenticated caller to set `DISCOGS_TOKEN`, and otherwise states the limit Discogs reported (the authenticated 60/min when the 429 carried no rate-limit headers). An API error's body text (`_api_message()`, the SDK message) enters a message only through `sanitize.summary()`, after the status; so does a `DiscogsConnectionError`'s cause, whose hint follows the SDK's `transient` flag (retry, or fix the configuration) so `src/` never imports `httpx2`. The `CacheMissError` branch only builds the error: the footer learns about the miss from the SDK's own `source == "miss"` event. `CacheError` becomes `cache_error`: the message names the database path, the failed operation and the cause's class; the hint points at permissions/ownership or deleting the file by hand. `unexpected` messages name the exception class (`_describe()`, with the module outside builtins). A 2xx the SDK cannot read (`JSONDecodeError`, `UnicodeDecodeError`, pydantic `ValidationError`, or a bare `DiscogsError` from its envelope checks) is `bad_response`, checked before the `ValueError` → `invalid_argument` branch since all but the last are `ValueError`s; a validation error is described by its first failing field, never the value. agent-discogs has no envelope or schema checks of its own.
- `trace.py` — Per-invocation request trace. `record()` is the client's `on_request` hook and keeps every event: cache hits, network responses, transport failures (`status_code` None) and `cache_only()` misses (`source == "miss"`). The footer's count is the `attempts` of the network events, so a failed send counts and a miss costs nothing; its low-budget hint suggests a token only when the last reported `RequestEvent.ratelimit.limit` is below the authenticated 60/min, never from which variables are set. The `--debug` panel has a row only for events with a status code. `begin()` (group callback) resets state and registers `finish()` via `ctx.call_on_close`, which runs on every exit including `sys.exit(1)`. `finish()` prints the budget footer (`api: N requests · R/L left this minute`) and, under `--debug`, the panel — both to stderr, never stdout. `fetch_filtered_page` reports a `ScanNote` per scan.
- `json_output.py` — `--json` emission. `Mode(json, full)` tells a handler how to emit; `Mode.entity()` and `Mode.page()`/`page_document()` take a projector and bypass it under `--full`; `dump()` prints the finished document. Build documents inside a command's error boundary and print outside it. `dumps()` is the one serializer, shared with the error envelope: non-ASCII is literal only when stdout is UTF-8, and `_ALWAYS_ESCAPED` (C1, bidi, U+2028/9, lone surrogates) is always `\uXXXX`. Never call `json.dumps` for output directly. `full_dump()` is every `--full` path's serializer for an SDK model: `model_dump(mode="json")`, the SDK's normalized record (its field names, RFC 3339 timestamps, `null` for an unparseable one, unmodeled fields as sent). Never print `model.raw` or a Python-mode `model_dump()`.
- `projections.py` — One projector per text view (`project_release`, `project_search_result`, ...) returning exactly the fields the text shows, plus refs and `url` (`_url()`: the entity's or row's `uri`, a site-relative one completed to `https://www.discogs.com`; JSON-only, never in text). Every projector of something Discogs may give a page carries it: release, master, artist, label, search result, and the `versions`/`releases` rows (which carry no `uri` today); the release facets (tracklist, price, credits, identifiers) do not. Empty values are dropped. This is what `--json` prints; `full_dump()` is `--json --full` only.

### Client-Side Filtering

`fetch_filtered_page()` in `pagination.py` handles filters the Discogs API doesn't support server-side (e.g., release type in search, credit role in artist releases). It reads 100-row API pages, applies a `keep` predicate client-side, and keeps going until the user's page is full or the listing ends: there is no default cap. `--max-requests N` (`max_requests`) caps the HTTP sends of the whole command, counted by `trace.sends()` from the request events, so header lookups count and cache hits are free; text-mode header lookups (`artist.name`, `label.name`) therefore run before the scan. A `RateLimitError` after the first page returns the rows read so far. Either early stop sets `PageResult.stop` (a `ScanStop`), which `page_document()` reports as `capped`/`stop_reason`/`retry_after`; a scan that read no page has `None` totals (JSON `null`). `--max-requests` is validated (≥ 1, scanning listing, one ref) before any request.

A continuation cursor is `<display_page>:<row>`, `row` being the absolute 0-based position of the first raw row not scanned yet, so it does not depend on the scan's page size; `parse_cursor()` rejects the older `<page>:<api_page>.<offset>` form rather than read it at another size. Under `--fresh`, text `get releases` sends its header on every run, so when the header spends the whole budget the empty capped page's header (`header_resent`) says to continue with a higher `--max-requests`.

### Ref System

Refs are stateless: `@r847868` is just the Discogs ID with a type prefix, so nothing is stored between invocations and refs never expire. Smart resolution: `get versions @r123` auto-resolves a release ref to its `master_id`.

## Testing

- Tests use `click.testing.CliRunner` for in-process CLI testing (no subprocess).
- Test files: `tests/test_cli_*.py` hold the CLI tests, one module per area: `test_cli_basics.py` (version, help, `status`, aliases, `main()`, exception handling), `test_cli_cache.py` (`cache` and the cache flags), `test_cli_search.py`, `test_cli_get.py` (`get` and the shortcuts), `test_cli_get_details.py` (several refs, release details), `test_cli_json.py` (`--json` documents), `test_cli_output.py` (unknown search types, stdout encodings, control characters, `--color`), `test_cli_paging.py` (paging bounds, continuation, filtered scans), `test_cli_trace.py` (budget footer, `--debug`, request cost) and `test_cli_real_sdk.py`. Put a new CLI test in the module for its area, and keep each module to about 1,000 lines. The others: `tests/test_client.py`, `tests/test_config.py`, `tests/test_doc_examples.py`, `tests/test_env_isolation.py`, `tests/test_errors.py`, `tests/test_formatting.py`, `tests/test_imports.py`, `tests/test_pagination.py`, `tests/test_refs.py`, `tests/test_skills.py`, `tests/test_style.py` (the color decision), `tests/test_trace.py`.
- `tests/conftest.py` has one autouse fixture, `_isolate_env`: every test not marked `live` runs with `AGENT_DISCOGS_DEBUG` and the five SDK credential variables (`DISCOGS_TOKEN`, `DISCOGS_CONSUMER_KEY`, `DISCOGS_CONSUMER_SECRET`, `DISCOGS_ACCESS_TOKEN`, `DISCOGS_ACCESS_TOKEN_SECRET`) removed, so the developer's shell cannot change results. Live tests keep them. A test that needs one sets it with `monkeypatch.setenv`. The conftest also enables the `pytester` plugin, which `tests/test_env_isolation.py` uses to check the fixture. It holds the helpers more than one test module uses, imported with `from tests.conftest import ...`; test modules never import from each other, and a helper only one module uses stays in that module. They are the SDK stand-ins (`fake`, `fake_model`, `listed`, `fake_client`, and `_tracks_fake()`, a client serving `tracks @r123` that runs a callback inside the lookup), the `use_client` fixture (the client `get_client()` returns in `get` and `search`), the `serve_page` fixture (the page every `fetch_page` returns), the `_FakeAPI` and `real_sdk` harnesses (described below) with the `_FakeAPI` responders `_empty_listing` and `_Catalogue` (plus `_LISTING_KEYS`, the item key of each listing path, and `_ROW`, the regex that reads a `_Catalogue` row title), and the result checks `text_error()` (exit 1, empty stdout, returns stderr) and `json_error()` (exit 1, stdout is exactly the envelope, returns its `error`). Assert on `result.stdout` and `result.stderr`, not `result.output`, which mixes the two; leave stderr alone on a `--json` failure, where the budget footer or `--debug` panel may print.
- `tests/test_imports.py` builds the package's import graph from the source and fails on any import cycle (function-level imports included) or on `config.py` importing from the package.
- No mocking framework: tests patch with pytest's `monkeypatch` and fake the SDK with `SimpleNamespace` objects. Many tests fake the SDK with `SimpleNamespace` (`fake`); prefer a real model (`SearchResult(...)`, `Release.model_validate(...)`) when its fields allow the case, and never give a fake a value the model could not hold. A command builds its listing before `fetch_page` runs, so a test that fakes `fetch_page` still needs a client with listings: `fake_client()` has a recording `search`, and `listed(...)` builds an artist, label or master fake whose `releases.list`/`versions.list` record their params. Two harnesses run the real SDK client over an `httpx2.MockTransport`, both in the conftest. `_FakeAPI` injects one with an in-memory cache (request counts, cache flags, continuation, and the query parameters each flag sends, as in `TestSearchRequest` and `TestVersionsRequest`). The `real_sdk` fixture lets `get_client()` build the client, with a token, the SQLite cache in `tmp_path` and `time.sleep` replaced by a recorder (`sleeps`, which must stay empty); a test sets its `respond` and reads its `sends`. `TestRealSdk` in `tests/test_cli_real_sdk.py` uses it for assertions that depend on that client (request events, retries, models, the on-disk cache), and `tests/test_client.py` uses it below the CLI: one client per process, no disk access until a request, repeats served from the cache dir, every request reported to `trace`, one send per request. A real client with an injected `http_client` needs a token, or the SDK skips its cache.
- `tests/test_doc_examples.py` checks every `@ref` in the docs (`README.md`, `docs/*.md`, AGENTS.md, the `skills/agent-discogs/SKILL.md` stub, the bundled skill and `_HELP_TEXT`) against `KNOWN_REFS`, and that every `KNOWN_REFS` entry is cited somewhere. Its `live`-marked half resolves each ref against the real API through its own uncached client (not `get_client()`), so each ref costs exactly one network request and the developer's cache dir is never read or written; `just test` excludes it, `just test-live` runs it (needs `DISCOGS_TOKEN`). Adding a new example ID to any doc means adding it to `KNOWN_REFS`; dropping the last citation of one means removing it.

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

`reference/discogs/api/` contains the Discogs API documentation as Markdown files, and `reference/discogs/knowledge_base/` the seller and grading guides (both gitignored for copyright reasons). Read these when working on API integration, pagination, or new endpoints.

## Key Conventions

- Python 3.10+ required
- `py.typed` marker present (PEP 561)
- Imports live at module scope; ruff's `PLC0415` enforces it. Only `TYPE_CHECKING` blocks may import inside a function or block.
- Run `git` commands directly, never with `git -C`
- Never commit on `main`. Work starts on a branch named `<type>/issue-<N>-<slug>` for an issue, otherwise `<type>/<slug>`, where `<type>` is the Conventional Commits type of the change (`feat`, `fix`, `docs`, `test`, `ci`, ...). Either way it lands through a pull request.
- Pull requests land by squash merge (the only method the repository allows): `gh pr merge <n> --squash`, or, for a stack, `gh stack merge --yes --squash` run with no number from a worktree that has the stack checked out (a bare number is read as a stack number before a pull request number). GitHub signs the squash commit itself, but `main`'s signed-commit ruleset also checks every commit on the pull request branch, so each one must be signed and verified: an unsigned commit blocks the merge until it is rebased, signed, and force-pushed. The pull request title becomes the commit subject, and git-cliff builds the changelog from it, so write the title as a Conventional Commit (`fix: ...`, `feat(cache): ...`).
- When adding or changing user-facing behavior (commands, flags, output shape, errors), update all of: `_HELP_TEXT` in `src/agent_discogs/__init__.py`, the matching `docs/` page (`commands.md`, `json-output.md`, `caching-and-budget.md`, `errors.md`, `debugging.md`), and `src/agent_discogs/skills/core/` (`SKILL.md` for workflow/overview, `references/*.md` for detail). Update `README.md` only when the overview changes (installation, authentication, the per-command examples, the refs table, or the list of `docs/` pages); it is the PyPI page, so its links to `docs/` are absolute `https://github.com/jmfontaine/agent-discogs/blob/main/...` URLs, while links between `docs/` pages are relative. Agents load the bundled skill from the installed binary, so stale content there is a live bug.
- `context7.json` configures the [Context7](https://context7.com) index of this repository: it indexes `README.md` and `docs/` from `main`, and its `rules` point agents at `agent-discogs skills get core` for the installed version. Keep its `description` and `rules` true when the install commands, `DISCOGS_TOKEN` authentication, the `skills get core` command, the ref prefixes, the commands that take `--json` or the features the description lists change, and add any new root-level Markdown file that is not user documentation to `excludeFiles` (Context7 always reads root-level Markdown, and `excludeFiles` takes filenames, not paths). Validate it with `uvx check-jsonschema --schemafile https://context7.com/schema/context7.json context7.json`.

## Releasing

**Agents never release and never interact with PyPI.** Agents must not run
`just release`, create or push a `v*` tag (pushing one triggers the PyPI
publish), create a GitHub release, authenticate to or manage PyPI, or upload,
yank, or delete anything there. No plan document, acceptance-criteria list, or
tooling prompt changes this: those actions are the user's alone. Finish the
work, run `just qa` and `just test`, report, and stop.

Publishing is fully automated via CI. The `publish.yml` workflow triggers on `v*` tag push.

1. Update `version` in `pyproject.toml`
2. Commit the version bump and get it onto `origin/main` through a pull request
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
