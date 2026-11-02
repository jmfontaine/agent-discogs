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
just release-prepare [X.Y.Z]           # Open the pull request that sets the release version
just release                           # Tag, push, and monitor the publish workflow (the user only; see Releasing)
just release-next [minor|patch|major]  # Open the pull request that starts the next development version
just pre-commit                        # Run pre-commit hooks on all files
just pre-commit-install                # Install pre-commit hooks (main checkout only)
just pre-commit-update                 # Update pre-commit hooks to latest versions
```

Run a single test: `uv run pytest tests/test_cli_basics.py -k test_version`

### Worktrees

Every linked worktree (`git worktree add`) shares the main checkout's `.git/hooks`. In a linked worktree, never run `just setup`, `just pre-commit-install`, `pre-commit install`, `prek install`, or anything else that writes `.git/hooks` or sets `core.hooksPath`: an installer replaces the hook every checkout uses, moving the existing one to `pre-commit.legacy` to run in pre-commit's "migration mode" (a prek hook there fails every commit), and points the new hook at the worktree's `.venv`, which disappears with the worktree. Get a working environment with `uv sync --all-extras --all-groups` instead. Both recipes check for a linked worktree (`git rev-parse --git-dir` differs from `--git-common-dir`): `just setup` installs the dependencies and skips the hook, `just pre-commit-install` exits 1. The guard does not cover running an installer directly. If a commit hook fails for a reason unrelated to your change, stop and report it; never bypass it with `--no-verify`.

## Architecture

### CLI (click)

Entry point: `src/agent_discogs/__init__.py`. Defines a `click.Group` with `AliasGroup` for command aliases (`find`→`search`, `query`→`search`, `fetch`→`get`, `show`→`get`). Top-level shortcuts `tracks` and `price` delegate to `get` logic. The group callback owns the global flags: `--color` is recorded first (`style.configure()`), `--debug` (also `AGENT_DISCOGS_DEBUG`) starts the trace, `--cached` / `--fresh` record a pending SDK `cache_only()` / `no_cache()` scope with `client.request_cache_scope()`. The callback runs before the subcommand validates its flags, so it never builds the client itself: the first `get_client()` call enters the scope on the root context (`ctx.with_resource()`), and it lasts until the command ends, exiting before `trace.finish()` and the client's closer run. A rejected flag therefore builds no client. A global flag typed after the subcommand is click's `NoSuchOption`; `AliasGroup.invoke` rewrites it, for the names in `_GLOBAL_OPTIONS` (kept next to the group's options: add a new global flag there too), into one saying to put it before the command, without click's did-you-mean.

`ctx.meta` is shared by the whole context chain and fresh for each invocation; it holds the client, the pending cache scope, the username, the folder list, the custom fields, the collection's ownership read and the color mode, so none outlives its command. A new per-invocation value goes there, never in a module global; the one exception is the request trace (`trace._trace`), which `trace.begin()` resets for each invocation.

### Command Modules

- `commands/search.py` — `search` (positional `[type] query`). `--added-since` wraps the SDK's experimental `releases_added_since()` (`_search_added_since()`). `--owned` and `--wanted` mark release rows after the page is read, through `collection.ownership_of()` and `inventory.wants_of()`, and are also accepted with `--max-requests`, which then caps that read. Every conflict is rejected before any request (`_added_since_error()`, `_typed_filter_error()` and the `--owned`/`--wanted`/`--max-requests` checks inline in `search()`).
- `commands/get.py` — `get NOUN REF...` (at most `MAX_REFS`) and the `tracks` and `price` shortcuts, which dispatch `GetOptions()`. `_dispatch()` prints one block per ref in order and exits 1 if any failed; one ref keeps the single-document shape and the `fail()` path. The options travel as one frozen `GetOptions`, built once in `get` from `**options` (its `explicit` field is `_explicit_options()`), to `_flag_error(noun, refs, options, mode)`, `_dispatch(noun, refs, options, mode)` and `_run_one(client, noun, ref, options, mode)`; the handlers and validators keep narrow keyword signatures. A new flag is its click declaration, a `GetOptions` field (`tests/test_cli_get.py` checks the two match), a `NOUN_OPTIONS` entry, and the validator and handler calls that use it. Validators (`_flag_error()`, the per-noun `_collection_error()`, `_wantlist_error()` and `_inventory_error()`, and `_price_error()` for `--max-price`/`--currency`) run before any request.
  Noun kinds: `REFLESS_NOUNS` (`collection`, `lists`, `wantlist`) take no ref, `ID_NOUNS` (`list`) a bare id, `USERNAME_NOUNS` (`inventory`) a seller's username and no user, and `NOUN_ALSO_ACCEPTS` lets `owned` take master refs (`_get_owned_master()`). A noun or flag that reads the user's own data (`get collection`, `get lists`, `get owned`, `get wantlist`, `--owned`, `--wanted`, `--in-wantlist`) calls `account.require_user()` before any request. On `releases` and `versions`, `--owned` and `--wanted` read each row's own stats (`collection.stat_copies()`, `collection.stat_wanted()`) and send nothing more.
  `get collection`: `_get_collection()` is the listing; `_get_collection_counts()` (`--count-by`), `_get_collection_duplicates()` (`--duplicates`), `_get_collection_random()` (`--random`) and `_get_collection_value()` (`--value`) are the views. The four handlers that read records start with `_prepare_collection()`, the one owner of the steps before a read, which returns a `_CollectionStart`; the three views read through `_read_collection()` into a `CollectionRead`, and only `collection._read_ownership()` builds an `Ownership`. `_collection_error()` holds what each `get collection` mode rejects, besides the checks `_flag_error()` shares with every listing (`--after` without a filter, `--page` with one); `--random`'s negative count is `_negative_counts()`.
  `get wantlist --for-sale` is a `fetch_filtered_page(..., keep_sends=True)` scan with `wantlist.ForSale` (see Client-Side Filtering); `_get_wantlist()` and `_get_versions()` return `(output, missed)` (`_run_one()` passes the pair through for the ref nouns, `_dispatch()` calls `_get_wantlist()` itself), so a `--cached` miss on a want or a versions row makes the command exit 1 without failing the ref. The API lists no copies for sale, only stats, so do not add condition, sleeve, location or shipping flags (`wantlist` module docstring).
  `get inventory` (`_get_inventory()`): `inventory.Filter` is the `keep` of a scan, `--in-wantlist` first reads the whole wantlist (`inventory.read_wantlist()`) and a partial read scans nothing (`pagination.unscanned()`); `--max-price` is matched client-side, never sent to Discogs. `--owned` looks rows up by `inventory.release_id()`, never the listing's own id. `get stats` (`_get_stats()`) and `get versions --market` (`_get_versions()`, `_mark_markets()`, `wantlist.read_markets()`) share `project_market()` with the `--for-sale` rows.
- `commands/status.py`, `commands/cache.py`, `commands/completion.py` — `status` and `completion` never call `get_client()`, so neither opens or creates the cache; `cache clear` runs `clear_cache()` inside the error boundary and prints success only when it returns. The CLI deletes no cache database itself.
- `commands/skills.py` — Serves the bundled skills from the installed package (module docstring). `skills/agent-discogs/SKILL.md` at the repo root is a thin discovery stub that points agents at `agent-discogs skills get core`; do not put feature content there.

### Core Modules

- `config.py` — Leaf module: it imports nothing from the package, and the package has no import cycles (`tests/test_imports.py`). Put new shared constants here (`cache_dir()`, `CACHE_TTL`, `CACHE_TTL_LABEL`) rather than in a module that needs them.
- `client.py` — The invocation's `Discogs` client: `get_client()` builds it on its first call (`_build()`, the seam tests patch), keeps it in `ctx.meta`, and enters the cache scope the global flags requested. It passes no credentials (the SDK resolves `DISCOGS_*`; `auth_mode()` reads the mode, so never check a variable yourself) and builds with `max_retries=0`, so the CLI never retries or sleeps and each request that reaches the network is sent once. `close_client()` runs after `trace.finish()` (see its docstring for why).
- `refs.py` — Typed refs (`@a3857`, `@r847868`, `@m3719`, `@l647`): `make_ref()` creates, `parse_ref()` parses; a raw numeric id is type `"unknown"`.
- `pagination.py` — Reads one page with full metadata through the SDK's public `SyncPage.fetch()`, from a `Listing` (a paginated SDK method with its filters bound); the SDK owns paths, models and envelope checks, so never reach into SDK internals. `fetch_filtered_page()` owns every client-side scan (Client-Side Filtering). `ScanStop.document()` is the one JSON form of a stop: `page_document()` and the `document()` of `CollectionRead`, `Ownership`, `Wants` and `MarketRead` merge it, and any later document that reports a stop should too. `next_page_cmd()` builds the `Next page:` / `Continue scan:` command, or a note when `sanitize.line()` would change a token.
- `account.py` — The user behind the credentials; every command that reads the user's own data goes through it. `require_user()` raises before any request unless the credentials name a user; `username()` is looked up once per invocation (`ctx.meta`); `currency()` reads the profile's `curr_abbr` and `amount()` parses a formatted amount; both raise `UnknownFormatError`, which is no `ValueError`, so `errors.classify()` maps it to `bad_response`. `get` and `search` run the `require_user()` preflight inside their error boundary, because `auth_mode()` builds a client that can fail.
- `collection.py` — The user's collection. `records()` is the one listing of a folder, so `get collection` without `--folder` or `--sort` and the `--owned` read send the same requests and share cache entries; `matcher()` is the one matching rule of every `get collection` filter (no `fnmatch`, no backtracking `re`); `read_whole()` is the one keep-everything read into a `CollectionRead`; `_read_ownership()` is the only place that turns a read into an `Ownership`, kept in `ctx.meta` by `ownership_of()` (`search`, `get inventory`) and `master_copies()` (`get owned @m...`), so the collection is read once per invocation. The folder list and custom fields are also read once per invocation. `count_records()`, `find_duplicates()`, `draw()` (tests patch `random.sample`), `sample_records()` and `valuation()` serve the views.
- `wantlist.py` — `ForSale`, the `keep` predicate of the `--for-sale` scan; `Market` and `market_of()`, a release's Marketplace numbers wherever shown; `read_markets()` for `get versions --market`.
- `inventory.py` — `GRADES`, the one grade ranking (ours, not the order of discogs-sdk's `Condition` literal); `Filter`, the `get inventory` `keep` predicate, which sends nothing; `read_wantlist()` and `wants_of()` (`search --wanted`) read the whole wantlist, so a row the part read lacks stays undecided.
- `rows.py` — The pure helpers that read fields off Discogs rows (`field()`, `count()`, `is_release_row()`, ...), shared by the views, the projections, `collection` and `inventory`. None sends a request, touches click or styles text. It imports nothing from the package but `refs`, and `projections` never imports `formatting` (`tests/test_imports.py` checks both).
- `formatting.py` — All text views, reading rows through `rows`; callers `print()` the plain strings. Every view returns `_text(lines)`, which runs each line through `sanitize.line()`, so a Discogs field can never add a line or print a raw control character: build new views the same way. Never put a Discogs value in a `style.Span`, and never build a `Styled` outside `markup`.
- `markup.py` — Converts Discogs markup in profiles and notes (`parse()`, `profile()`), for the text views and the `--json` projections alike (`--full` keeps it raw). It writes no escape code: conversion runs before `sanitize.line()`, painting after.
- `sanitize.py` — Neutralizes untrusted text for the text views (`line()`, `summary()`). Discogs data values in `--json` output never go through it.
- `style.py` — Terminal color for a person, never an agent: `--color` is recorded in `ctx.meta` by `configure()`, and every `--json` flag has the `off_for_json` callback, which turns color off for the whole command. `paint()` takes already-neutralized text; `--help` and `skills` never paint.
- `errors.py` — `classify()` maps SDK exceptions to an `ErrorInfo` (stable `code`, message, hint); `format_error()` renders text, `format_error_json()` the `{"error": {...}}` envelope, `fail()` prints one and exits 1. An API error's body and a connection error's cause enter a message only through `sanitize.summary()`; an `invalid_argument` from a `ValueError` may quote Discogs text (a folder name, a release title), which `format_error()` (`sanitize.line()`) and `json_output.dumps()` neutralize; `src/` never imports `httpx2`. The 401 and 429 advice follows `client.auth_mode()`.
- `trace.py` — Per-invocation request trace. `record()` is the client's `on_request` hook; `sends()`, what `--max-requests` compares against, is a running count that `record()` adds each network event's `attempts` to (and `begin()` resets), so a capped scan asking before every row never walks the events. `begin()` (group callback) registers `finish()` via `ctx.call_on_close`, which prints the budget footer and, under `--debug`, the panel, both to stderr, never stdout, before the client is closed.
- `json_output.py` — `--json` emission. `Mode(json, full)` tells a handler how to emit; `dumps()` is the one serializer, shared with the error envelope: never call `json.dumps` for output directly. `full_dump()` is every `--full` path's serializer for an SDK model; never print `model.raw` or a Python-mode `model_dump()`. Build documents inside a command's error boundary and print outside it.
- `projections.py` — One projector per text view (`project_release`, `project_search_result`, ...) returning the text view's data, plus refs and `url` (`_url()`: the entity's or row's `uri`, or a list's own `url`, a site-relative one completed to `https://www.discogs.com`; JSON-only, never in text). Every projector of something Discogs may give a page carries it: release, master, artist, label, search result, list, `get lists` row, and the `versions`/`releases` rows; the release facets (tracklist, price, credits, identifiers) and list items do not. Empty values are dropped. The module docstring lists where JSON differs from the text; keep it true.

### Client-Side Filtering

`pagination.fetch_filtered_page()` handles filters the Discogs API lacks (release type in search, credit role in artist releases, every `get collection` filter): it reads 100-row pages, applies a `keep` predicate, and has no default cap. `--max-requests N` caps the HTTP sends of the whole command, counted by `trace.sends()`, so header lookups count (text-mode headers such as `artist.name` run before the scan) and cache hits are free. `get`'s `_flag_error()` validates it before any request (a scanning listing, one ref, ≥ 1 through `pagination.max_requests_error()`). An early stop (the cap, or a 429 after the first page) sets `PageResult.stop`, a `ScanStop`; `PageResult` and `fetch_filtered_page()` state the rest, including the `None` totals of a scan that read no page.

A continuation cursor is `<display_page>:<row>`, an absolute row independent of the scan's page size (`pagination.parse_cursor()`). Besides the cursor, a continuation repeats the caller's own arguments (`pagination.next_page_cmd()`). `formatting._page_counts()` explains the `header_resent` note under `--fresh`.

`keep_sends=True` serves `get wantlist --for-sale`, whose `keep` sends a request per want; its cap and 429 rules are in the `fetch_filtered_page()` docstring. `--max-requests` below 3 is rejected with `--for-sale`; `_wantlist_error()` says why.

### Ref System

Refs are stateless: `@r847868` is just the Discogs ID with a type prefix, so nothing is stored between invocations and refs never expire. Smart resolution: `get versions @r123` auto-resolves a release ref to its `master_id`.

## Testing

- Tests use `click.testing.CliRunner` for in-process CLI testing (no subprocess), and no mocking framework: they patch with pytest's `monkeypatch` and fake the SDK with `SimpleNamespace` objects (`fake`), preferring a real model when its fields allow the case. Assert on `result.stdout` and `result.stderr`, not `result.output`, which mixes the two.
- `tests/conftest.py` holds the helpers more than one test module uses (SDK stand-ins, the `_FakeAPI` and `real_sdk` harnesses over a mock transport, the shared collection and ownership harnesses, the result checks, the autouse `_isolate_env`, `DOC_FILES`); its module docstring says what each is for. Import them with `from tests.conftest import ...`; test modules never import from each other, and a helper only one module uses stays in that module.
- `tests/test_cli_*.py` hold the CLI tests, one module per area: `test_cli_basics.py` (version, help, `status`, aliases, `main()`, exception handling), `test_cli_cache.py` (`cache` and the cache flags), `test_cli_collection.py` (`get collection` rows, pages, arguments, `--value`, `--fields`), `test_cli_collection_filters.py` (its filters, patterns, `--folder`), `test_cli_collection_views.py` (`--count-by`, `--duplicates`), `test_cli_collection_random.py` (`--random`, the draw fixed by patching `random.sample`), `test_cli_completion.py` (the `completion` command and what Tab offers), `test_cli_search.py` (`search`), `test_cli_get.py` (`get` and the shortcuts), `test_cli_get_details.py` (several refs, release details), `test_cli_get_owned.py` (`get owned` for release and master refs), `test_cli_inventory.py` (`get inventory`, its filters, `--in-wantlist`, `--owned`), `test_cli_json.py` (`--json` documents), `test_cli_lists.py` (`get lists`, `get list`), `test_cli_market.py` (`get stats`, `get versions --market`), `test_cli_output.py` (unknown search types, stdout encodings, control characters, `--color`, Discogs markup), `test_cli_owned.py` (`--owned` and `--wanted` on list rows, and the user they need), `test_cli_paging.py` (paging bounds, continuation, filtered scans), `test_cli_trace.py` (budget footer, `--debug`, request cost), `test_cli_wantlist.py` (`get wantlist`, `--for-sale`) and `test_cli_real_sdk.py` (assertions that depend on the real client `get_client()` builds). Put a new CLI test in the module for its area, and keep each module to about 1,000 lines.
- The others, one line each: `test_account.py` (`account.amount()`, `account.currency()`), `test_client.py` (`get_client()`: one client per invocation, what it sends, reports and caches), `test_collection.py` (`collection.matcher()`, including patterns a backtracking regex never finishes, and `collection.user_stat()`), `test_config.py`, `test_env_isolation.py` (the `_isolate_env` fixture, through `pytester`), `test_errors.py`, `test_formatting.py`, `test_markup.py` (the markup converter), `test_pagination.py`, `test_refs.py`, `test_rows.py` (the row-reading helpers), `test_scenarios.py` (the skills' Tasks entries and each `@recipe` run against a small Discogs world), `test_skills.py`, `test_style.py` (the color decision) and `test_trace.py`.
- `tests/test_imports.py` builds the package's import graph from the source and fails on any import cycle (function-level imports included) or on `config.py` importing from the package.
- `DOC_FILES` in `tests/conftest.py` is the one list of docs the doc tests read: `README.md`, `docs/*.md`, the `skills/agent-discogs/SKILL.md` stub and the bundled skills' `**/*.md`.
- `tests/test_doc_commands.py` parses every `agent-discogs ...` command in `DOC_FILES` and in `_HELP_TEXT` with click's own parser, without running it, so a renamed command or a flag on the wrong command fails with the file and line. An example that shows an error on purpose must end in `# error`, or the test fails on it.
- `tests/test_doc_examples.py` checks every `@ref` in `DOC_FILES`, AGENTS.md and `_HELP_TEXT` against `KNOWN_REFS`, and that every `KNOWN_REFS` entry is cited somewhere. Its `live`-marked half resolves each ref against the real API through its own uncached client, one request per ref; `just test` excludes it, `just test-live` runs it (needs `DISCOGS_TOKEN`). Adding a new example ID to any doc means adding it to `KNOWN_REFS`; dropping the last citation of one means removing it.
- `tests/test_doc_links.py` checks that every relative link and `#anchor` in `DOC_FILES` and AGENTS.md resolves (GitHub heading slugs), so a renamed heading or moved page fails the test.

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

- Python 3.11+ required
- `py.typed` marker present (PEP 561)
- Imports live at module scope; ruff's `PLC0415` enforces it. Only `TYPE_CHECKING` blocks may import inside a function or block.
- Run `git` commands directly, never with `git -C`
- Never commit on `main`. Work starts on a branch named `<type>/issue-<N>-<slug>` for an issue, otherwise `<type>/<slug>`, where `<type>` is the Conventional Commits type of the change (`feat`, `fix`, `docs`, `test`, `ci`, ...). The one exception is a version change, which goes on `release/v<version>`, created by the [release recipes](#releasing). Either way it lands through a pull request.
- Pull requests land by squash merge (the only method the repository allows): `gh pr merge <n> --squash`, or, for a stack, `gh stack merge --yes --squash` run with no number from a worktree that has the stack checked out (a bare number is read as a stack number before a pull request number). GitHub signs the squash commit itself, but `main`'s signed-commit ruleset also checks every commit on the pull request branch, so each one must be signed and verified: an unsigned commit blocks the merge until it is rebased, signed, and force-pushed. The pull request title becomes the commit subject, and git-cliff builds the changelog from it, so write the title as a Conventional Commit (`fix: ...`, `feat(cache): ...`).
- `CI passed`, the `ci-passed` job in `.github/workflows/ci.yml`, is the only required status check. It needs the
  `checks` job, which calls `checks.yml`, so a job added to `checks.yml` is covered without a ruleset change. A job
  added to `ci.yml` itself must be added to the `needs` of `ci-passed`
- When adding or changing user-facing behavior (commands, flags, output shape, errors), update all of: `_HELP_TEXT` in `src/agent_discogs/__init__.py`, the matching `docs/` page (`commands.md`, `json-output.md`, `caching-and-budget.md`, `errors.md`, `debugging.md`), and the bundled skill the behavior belongs to under `src/agent_discogs/skills/` (`SKILL.md` for workflow/overview, `references/*.md` for detail): `collection` for `--owned`, `get owned`, `get collection`, `get lists` and `get list`; `marketplace` for `price`, `get wantlist` and `get inventory`; `pressings` for telling releases apart (versions, identifiers, unofficial releases); `core` for everything else, including the global flags, `search`, the database nouns of `get`, errors and budget, and any fact two skills need (the other skill names the `skills get core` section that holds it). `skills get core` is loaded in every session, so `tests/test_skills.py` caps the size of each skill (`SKILL.md` 24 KB for `core`, 20 KB for the others; `--full` 85 KB for `core`, 50 KB for `collection`, 45 KB for `marketplace`, 40 KB for `pressings`): put detail in `references/`. Update `README.md` only when the overview changes (installation, authentication, the per-command examples, the refs table, or the list of `docs/` pages); it is the PyPI page, so its links to `docs/` are absolute `https://github.com/jmfontaine/agent-discogs/blob/main/...` URLs, while links between `docs/` pages are relative. Agents load the bundled skills from the installed binary, so stale content there is a live bug.
- The same change updates the Tasks entries it affects: each skill's `## Tasks` (`core`'s in `references/tasks.md`, its `SKILL.md` holding the index of every task ID) gives each recurring request a stable ID (never reused), its status per part (`works`, `works, costly`, `not possible`), its commands written in full, their cost as a formula and what would make it cheaper or possible. A change that makes a recipe cheaper, breaks it or makes a task possible updates the entry's status, commands and cost, and its test in `tests/test_scenarios.py`; a new task gets an unused ID, an entry, its place in `SKILL_OF`, `core`'s index and a `@recipe` test (or `NOT_POSSIBLE`).
- `context7.json` configures the [Context7](https://context7.com) index of this repository: it indexes `README.md` and `docs/` from `main`, and its `rules` point agents at `agent-discogs skills get core`, then the other bundled skills, for the installed version. Keep its `description` and `rules` true when the install commands, `DISCOGS_TOKEN` authentication, the `skills get core` command or the names of the other skills, the ref prefixes, the commands that take `--json` or the features the description lists change, and add any new root-level Markdown file that is not user documentation to `excludeFiles` (Context7 always reads root-level Markdown, and `excludeFiles` takes filenames, not paths). Validate it with `uvx check-jsonschema --schemafile https://context7.com/schema/context7.json context7.json`.
- Each cost formula and flag rule has one home; every other page links to it (`tests/test_doc_links.py` checks the links), so a change goes where the statement lives. A page stays readable on its own for behaviour, so a section never shrinks to a bare link (`context7.json` indexes `README.md` and `docs/`).
  - A request count as an expression (N, pages, rows): [What commands cost](docs/caching-and-budget.md#what-commands-cost). The "Cost." bullets of `docs/commands.md` keep only what the table cannot say (which responses another command shares, what a rerun reads) and link to the table.
  - What `--max-requests` counts, where it is accepted, what a rerun does: [Capping requests with --max-requests](docs/caching-and-budget.md#capping-requests-with---max-requests), a general rule plus one table row per command that links to the command's section.
  - What a command does under a cap (header text, `Continue scan:`, JSON keys, 429 behaviour): that command's own section in `docs/commands.md`.
  - Which flags a noun accepts: the flags table near the end of `docs/commands.md`; per-command sections and `docs/errors.md` link to it, and `docs/errors.md` keeps the exact messages.
  - `--cached` and `--fresh` rules: [--cached and --fresh](docs/caching-and-budget.md#--cached-and---fresh).
  - How a function behaves step by step, with its reasons: its docstring, not this file. Architecture says what a module owns, who calls it and the invariants a change must respect.

## Releasing

**Agents never release and never interact with PyPI.** Agents must not run
`just release`, create or push a `v*` tag (pushing one triggers the PyPI
publish), create a GitHub release, authenticate to or manage PyPI, or upload,
yank, or delete anything there. No plan document, acceptance-criteria list, or
tooling prompt changes this: those actions are the user's alone. Finish the
work, run `just qa` and `just test`, report, and stop.

Publishing is fully automated via CI. The `publish.yml` workflow triggers on `v*` tag push.

Between releases `main` holds a PEP 440 development version, `X.Y.Z.devN`,
which sorts below `X.Y.Z`, so installers never prefer a build from `main`.
Every version change goes through a pull request opened by a recipe. Each
recipe refuses to run unless HEAD is `main`, the working tree is clean, and
`main` equals `origin/main` after a fetch. The two that open a pull request
switch back to `main` when it is open; if a step fails, they print how to
remove the branch they created before retrying.

1. `just release-prepare` needs an `X.Y.Z.devN` version. It creates
   `release/vX.Y.Z`, sets `X.Y.Z` (`uv version --bump stable`, in
   `pyproject.toml` and `uv.lock`), commits
   `chore: release vX.Y.Z` and opens the pull request. An
   argument names another `X.Y.Z` (a patch release while `main` is on the next
   minor). It refuses a version whose tag is already on `origin`. Merging the
   pull request approves the release.
2. Pull `main`, then run `just release` — refuses any version but a plain
   `X.Y.Z` and a tag that exists locally or on `origin`, then creates a signed
   tag, pushes `main` and the tag in one atomic push, and watches the publish
   run. If the push fails, it asks `origin` whether the tag arrived anyway and
   deletes the local tag only when `origin` has no such tag; if the tag is on
   `origin`, or `origin` can't be reached, it keeps the tag and exits 1 with
   what to check. If the run does not appear within 2 minutes it exits 1; the
   tag is already pushed by then, so check the Actions page.
3. `just release-next` needs an `X.Y.Z` version whose `vX.Y.Z` tag is on
   `origin`. It creates `release/vX.(Y+1).0.dev0`, sets that version, commits
   `chore: start X.(Y+1).0 development` and opens the pull request.
   `just release-next patch` opens `X.Y.(Z+1).dev0` instead, and
   `just release-next major` `(X+1).0.0.dev0`. Merge it to reopen development.

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
release the next patch version with `just release-prepare X.Y.(Z+1)` (run
`just release-next` first if `main` still holds `X.Y.Z`).

## Shared workflow

This repository follows the shared workflow: the `github-workflow` skill's repository
setup describes its GitHub settings, rulesets, labels and templates. Those settings are
managed as code outside this repository; change them there, never with `gh api` or the
GitHub web UI.

### Deviations from the shared workflow

None.
