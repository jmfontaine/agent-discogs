# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

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
just dead-code          # deadcode src tests
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

### Core Modules

- `client.py` — Singleton `Discogs` client. Reads `DISCOGS_TOKEN` from env. Cache dir: `~/.cache/agent-discogs`.
- `refs.py` — Typed ref system (`@a3857`, `@r367113`, `@m3719`, `@l647`). `make_ref()` creates refs, `parse_ref()` parses them. Raw numeric IDs return type `"unknown"`.
- `pagination.py` — Bypasses SDK's `SyncPage` auto-paging to fetch exactly one page with full metadata (`total_items`, `total_pages`). Uses SDK internals (`_build_url`, `_send`, `_maybe_raise`).
- `formatting.py` — All output formatting. Returns plain strings, callers `print()` them.
- `errors.py` — Maps SDK exceptions to recovery-oriented error messages.
- `json_output.py` — JSON serialization helpers for `--json` flag. `dump_entity()` for single objects, `dump_page()` for paginated results.

### Client-Side Filtering

`fetch_filtered_page()` in `pagination.py` handles filters the Discogs API doesn't support server-side (e.g., release type in search, credit role in artist releases). It over-fetches from the API, applies a `keep` predicate client-side, and pulls additional API pages as needed to fill the user's requested page size. Capped at 5 API calls per user request.

### Ref System

Search results replace all refs. Single-entity lookups (`get`) are additive. Smart resolution: `get versions @r123` auto-resolves a release ref to its `master_id`.

## Testing

- Tests use `click.testing.CliRunner` for in-process CLI testing (no subprocess).
- Test files: `tests/test_cli.py`, `tests/test_client.py`, `tests/test_errors.py`, `tests/test_formatting.py`, `tests/test_pagination.py`, `tests/test_refs.py`.
- No special fixtures or mocking framework beyond `unittest.mock`.

## Dependencies

- Runtime: `click`, `discogs-sdk`, `pydantic`
- Dev: pytest, pytest-cov, ruff, ty, pyright, deadcode, deptry, pyproject-fmt

## Discogs API Documentation

`docs/discogs_api/` contains the Discogs API documentation as Markdown files (gitignored for copyright reasons). Read these when working on API integration, pagination, or new endpoints.

## Key Conventions

- Python 3.10+ required
- `py.typed` marker present (PEP 561)
- Run `git` commands directly, never with `git -C`

## Releasing

Publishing is fully automated via CI. The `publish.yml` workflow triggers on `v*` tag push.

1. Update `version` in `pyproject.toml`
2. Commit the version bump
3. Run `just release` — creates a signed tag, pushes, and monitors the workflow
