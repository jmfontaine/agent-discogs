# AGENTS.md

This file provides guidance to agents when working with code in this repository.

## Project Overview

A token-efficient Discogs CLI designed for AI agents. Built on click and the `discogs-sdk` Python SDK.

## Commands

Task runner is `just`. All commands use `uv run` under the hood.

```bash
just setup              # Install deps + pre-commit hooks
just test               # Run pytest with coverage
just test <path>        # Run specific test file or directory
just qa                 # All checks: dead-code, deps-unused, format-check, lint, type-check, verify-types
just lint               # ruff check
just lint-fix           # ruff check --fix
just format             # ruff format + pyproject-fmt
just type-check         # ty check
just dead-code          # deadcode src tests (via uvx, Python 3.13)
just deps-unused        # deptry src
just deps-update        # Update deps to latest versions
just verify-types       # Audit public API type annotation coverage
just release            # Tag, push, and monitor the publish workflow
just pre-commit         # Run pre-commit hooks on all files
just pre-commit-install # Install pre-commit hooks
just pre-commit-update  # Update pre-commit hooks to latest versions
```

Run a single test: `uv run pytest tests/test_cli.py -k test_version`

## Architecture

### CLI (click)

Entry point: `src/agent_discogs/__init__.py`. Defines a `click.Group` with `AliasGroup` for command aliases (`find`→`search`, `query`→`search`, `fetch`→`get`, `show`→`get`). Top-level shortcuts `tracks` and `price` delegate to `get` logic.

### Command Modules

- `commands/search.py` — Search Discogs database. Positional args with optional type prefix (`release`, `master`, `artist`, `label`).
- `commands/get.py` — Get entity details by noun (`artist`, `label`, `master`, `price`, `release`, `releases`, `tracklist`, `versions`). Also defines `tracks` and `price` shortcut commands.
- `commands/status.py` — Show session/auth info.
- `commands/cache.py` — Cache management (`clear`).
- `commands/skills.py` — Serves the bundled agent guide (`skills`, `skills get core [--full]`, `skills path`). Content lives in `src/agent_discogs/skills/core/` (`SKILL.md` + `references/*.md`) and ships in the wheel. `skills/agent-discogs/SKILL.md` at the repo root is a thin discovery stub that points agents at `agent-discogs skills get core`; do not put feature content there.

### Core Modules

- `client.py` — Singleton `Discogs` client. Reads `DISCOGS_TOKEN` from env. Cache dir: `~/.cache/agent-discogs`.
- `refs.py` — Typed ref system (`@a3857`, `@r367113`, `@m3719`, `@l647`). `make_ref()` creates refs, `parse_ref()` parses them. Raw numeric IDs return type `"unknown"`.
- `pagination.py` — Bypasses SDK's `SyncPage` auto-paging to fetch exactly one page with full metadata (`total_items`, `total_pages`). Uses SDK internals (`_build_url`, `_send`). `_send()` is the SDK's HTTP-error boundary — it raises the mapped `DiscogsAPIError` subclass before returning, so callers never re-check the status.
- `formatting.py` — All output formatting. Returns plain strings, callers `print()` them.
- `errors.py` — Maps SDK exceptions to recovery-oriented error messages.
- `json_output.py` — JSON serialization helpers for `--json` flag. `dump_entity()` for single objects, `dump_page()` for paginated results.

### Client-Side Filtering

`fetch_filtered_page()` in `pagination.py` handles filters the Discogs API doesn't support server-side (e.g., release type in search, credit role in artist releases). It over-fetches from the API, applies a `keep` predicate client-side, and pulls additional API pages as needed to fill the user's requested page size. Capped at 5 API calls per user request.

### Ref System

Search results replace all refs. Single-entity lookups (`get`) are additive. Smart resolution: `get versions @r123` auto-resolves a release ref to its `master_id`.

## Testing

- Tests use `click.testing.CliRunner` for in-process CLI testing (no subprocess).
- Test files: `tests/test_cli.py`, `tests/test_client.py`, `tests/test_errors.py`, `tests/test_formatting.py`, `tests/test_pagination.py`, `tests/test_refs.py`, `tests/test_skills.py`.
- No special fixtures or mocking framework beyond `unittest.mock`.

## Dependencies

- Runtime: `click`, `discogs-sdk`, `pydantic`
- Dev: deptry, pyproject-fmt, pyright, pytest, pytest-cov, ruff, ty
- KLUDGE: `deadcode` is deliberately *not* a dev dependency. It crashes on
  Python 3.14 (it calls `ast.Str`, which 3.14 removed) and upstream is
  dormant, so `just dead-code` runs it through `uvx` pinned to Python 3.13.
  The justfile recipe, the CI step, and the pre-commit hook each carry
  `KLUDGE` comments with the details — grep for `KLUDGE` before changing the
  dead-code check, and do not "simplify" its exit-code handling.
- Python 3.15 is supported, and it is the one interpreter that resolves a
  **pre-release** pydantic. That split is deliberate, inherited from
  discogs-sdk 0.4.0: stable Pythons get stable pydantic, 3.15 gets the beta.
  The SDK declares `pydantic>=2.14.0b2,<2.15; python_version=='3.15'` because
  pydantic 2.13 pins a pydantic-core with no cp315 wheels, and 2.14's
  pydantic-core (2.49.0) is the first with them. So the committed lockfile
  carries a 3.15-only pydantic branch, and every install path — `uv sync`,
  `uv pip install`, plain `pip install` — picks 2.14.0b2 there with no
  pre-release flag, because a specifier naming `2.14.0b2` enables pre-release
  selection for that package on its own. Nothing builds from sdist.
  Consequences to keep in mind:
  - Do not prune the 3.15 lock branch with `[tool.uv] environments`. It is
    marker-gated on `python_full_version == '3.15.*'`, so it is inert on
    3.10–3.14, and pruning it makes the `3.15` CI leg unresolvable.
  - Our own `pydantic>=2.12.5` floor stays as is. It neither needs nor grants
    pre-release permission; the SDK's marker-scoped clause does that.
  - A pydantic 2.14 beta regression surfaces as a red `3.15` test leg here.
    Check `pydantic.VERSION` in that job before suspecting this project.
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
  When pydantic 2.14 goes final the split disappears on its own: the `>=2.12.5`
  floor resolves a 3.15-capable stable pydantic everywhere, and no metadata
  here changes.

## Discogs API Documentation

`docs/discogs/api/` contains the Discogs API documentation as Markdown files, and `docs/discogs/knowledge_base/` the seller and grading guides (both gitignored for copyright reasons). Read these when working on API integration, pagination, or new endpoints.

## Key Conventions

- Python 3.10+ required
- `py.typed` marker present (PEP 561)
- Run `git` commands directly, never with `git -C`
- When adding or changing user-facing behaviour (commands, flags, output shape, errors), update all of: `_HELP_TEXT` in `src/agent_discogs/__init__.py`, `README.md`, and `src/agent_discogs/skills/core/` (`SKILL.md` for workflow/overview, `references/*.md` for detail). Agents load the bundled skill from the installed binary, so stale content there is a live bug.

## Releasing

Publishing is fully automated via CI. The `publish.yml` workflow triggers on `v*` tag push.

1. Update `version` in `pyproject.toml`
2. Commit the version bump
3. Run `just release` — creates a signed tag, pushes, and monitors the workflow
