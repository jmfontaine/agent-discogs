# Contributing to agent-discogs

Contributions are welcome — bug fixes, new features, documentation, test coverage.
This guide walks you through the development setup and PR process.

For larger changes, please open an issue first so we can discuss the approach
before you invest significant time.

## Reporting bugs and requesting features

Before opening an issue, search [existing issues](https://github.com/jmfontaine/agent-discogs/issues) to avoid duplicates.

**Bug reports** should include:
- Python version and agent-discogs version (`python --version`, `agent-discogs --version`)
- The exact command you ran and its output
- Full traceback or error message

**Feature requests** — describe the problem you want to solve, not just the solution.
For new Discogs API coverage, note which endpoints are involved.

## Setup

```bash
git clone https://github.com/jmfontaine/agent-discogs.git
cd agent-discogs
just setup
```

This installs dependencies with [uv](https://docs.astral.sh/uv/), including [pre-commit](https://pre-commit.com/)
from the dev group, and sets up the pre-commit hooks. No global pre-commit install is needed.

> [!IMPORTANT]
> If you work in [git worktrees](https://git-scm.com/docs/git-worktree), run `just setup` in the main checkout only.
> Every linked worktree shares the main checkout's `.git/hooks`, so installing hooks from one replaces the hook all of
> them use and points it at that worktree's `.venv`, which disappears when the worktree is removed. In a linked
> worktree, install the dependencies with `uv sync --all-extras --all-groups` and do not run `just pre-commit-install`,
> `pre-commit install`, or any other hook installer. `just setup` skips the hook in a linked worktree, and
> `just pre-commit-install` refuses to run there.

> [!TIP]
> The task runner is [just](https://github.com/casey/just). If you don't have it installed, you can run the underlying
> commands directly. Check [`justfile`](justfile) to see what each recipe runs — most are one-line `uv run` commands.

## Authentication setup

Price lookups need a Discogs personal access token (or OAuth credentials), and the live test suite needs a token.

1. Create a Discogs account (or use an existing one) at https://www.discogs.com
2. Generate a personal access token at https://www.discogs.com/settings/developers
3. Add it to your `.env` file:

```
DISCOGS_TOKEN=your_token_here
```

If you use [direnv](https://direnv.net/), the `.envrc` already loads `.env` automatically.

> [!NOTE]
> `just test` never hits the network, so you do not need a token to run the test suite. The opt-in live suite
> (`just test-live`, tests marked `live`) resolves the documentation's example refs against the real Discogs API and
> needs `DISCOGS_TOKEN`.

## Development workflow

Key commands:

```bash
just format             # Auto-format code (ruff + pyproject-fmt)
just lint-fix           # Auto-fix lint issues
just qa                 # All checks (see PR checklist below)
just test               # Unit tests with coverage (fast, no network)
just test-live          # Live-API tests, opt-in (needs DISCOGS_TOKEN)
just type-check         # Run type checker (ty)
just verify-types       # Audit public API type annotation coverage (pyright)
```

Run `just --list` to see all available commands. Maintainers release with `just release-prepare`, `just release` and
`just release-next`, which [AGENTS.md](AGENTS.md#releasing) describes.

## Testing

```bash
just test                                                             # All unit tests, 100% coverage enforced
just test tests/test_cli_basics.py --cov-fail-under=0                 # Single file
just test tests/test_cli_basics.py -k test_version --cov-fail-under=0 # Single test
```

Tests use `click.testing.CliRunner` for in-process CLI testing. They patch with pytest's `monkeypatch` and fake SDK responses with `SimpleNamespace` objects; tests that need the real SDK client run it over an `httpx2.MockTransport`. `just test` deselects the tests marked `live`, so it never hits the network; `just test-live` runs only those, against the real API.

100% test coverage of `src/agent_discogs` is required and enforced: `[tool.coverage]` in `pyproject.toml` measures only the package and sets `fail_under = 100`, so `just test` and CI fail below it. `just test` reports uncovered lines with `--cov-report=term-missing`. A partial run (a single file or `-k`) cannot reach 100%, so pass `--cov-fail-under=0` to keep the report without the threshold.

## Code style

- Python 3.11+
- Formatting and linting handled by ruff (`just format`, `just lint-fix`)
- Type checking with ty (`just type-check`)
- Type completeness auditing with pyright (`just verify-types`)
- Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/): `<type>: <description>`
  (e.g., `feat: add list items resource`, `fix: report the wait time on a rate limit`).
  See [`cliff.toml`](cliff.toml) for the configured commit types.

## Submitting a pull request

1. Fork the repo and create a branch from `main` (e.g., `fix/rate-limit-wait-time`, `feat/add-list-items`)
2. Make your changes
3. Add or update tests
4. Run `just qa` to catch issues before CI does
5. Push and open a PR against `main`, referencing any related issues (e.g., "Fixes #42")

### PR checklist

Before opening a PR, make sure the following are done:

- [ ] Tests added or updated and passing (`just test`)
- [ ] 100% test coverage maintained
- [ ] `just qa` passes (dead-code, deps-unused, format-check, lint, type-check, verify-types)

### What to expect

A maintainer will review your PR, usually within a few days. Change requests are normal and collaborative.
Push additional commits to the same branch; no need to squash. Every commit on the branch must be signed and show as
verified on GitHub: `main` requires signed commits, and an unsigned one blocks the merge until it is re-signed and
force-pushed. Pull requests are squash-merged, so the pull request title becomes the commit subject on `main`, and
git-cliff builds the changelog from it: write it as a Conventional Commit. The `CI passed` check summarizes every CI job
and must be green before a pull request merges.

## License

By contributing, you agree that your contributions will be licensed under the [Apache License 2.0](LICENSE.txt).
