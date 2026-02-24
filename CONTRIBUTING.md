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

This installs dependencies with [uv](https://docs.astral.sh/uv/) and sets up pre-commit hooks.

> [!TIP]
> The task runner is [just](https://github.com/casey/just). If you don't have it installed, you can run the underlying
> commands directly. Check [`justfile`](justfile) to see what each recipe runs — most are one-line `uv run` commands.

## Authentication setup

Some commands require a Discogs personal access token.

1. Create a Discogs account (or use an existing one) at https://www.discogs.com
2. Generate a personal access token at https://www.discogs.com/settings/developers
3. Add it to your `.env` file:

```
DISCOGS_TOKEN=your_token_here
```

If you use [direnv](https://direnv.net/), the `.envrc` already loads `.env` automatically.

> [!NOTE]
> Tests never hit the network. You do not need a token to run the test suite.

## Development workflow

Key commands:

```bash
just format             # Auto-format code (ruff + pyproject-fmt)
just lint-fix           # Auto-fix lint issues
just qa                 # All checks (see PR checklist below)
just test               # Unit tests with coverage (fast, no network)
just type-check         # Run type checker (ty)
just verify-types       # Audit public API type annotation coverage (pyright)
```

Run `just --list` to see all available commands.

## Testing

```bash
just test                                        # All unit tests
just test tests/test_cli.py                      # Single file
just test tests/test_cli.py -k test_version      # Single test
```

Tests use `click.testing.CliRunner` for in-process CLI testing with `unittest.mock` and `SimpleNamespace` for mocking SDK responses. They never hit the network.

100% test coverage is required. `just test` runs pytest with `--cov` and `--cov-report=term-missing` so you can see any uncovered lines.

## Code style

- Python 3.10+
- Formatting and linting handled by ruff (`just format`, `just lint-fix`)
- Type checking with ty (`just type-check`)
- Type completeness auditing with pyright (`just verify-types`)
- Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/): `<type>: <description>`
  (e.g., `feat: add list items resource`, `fix: handle rate limit retry`).
  See [`cliff.toml`](cliff.toml) for the configured commit types.

## Submitting a pull request

1. Fork the repo and create a branch from `main` (e.g., `fix/rate-limit-retry`, `feat/add-list-items`)
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
Push additional commits to the same branch — no need to force-push or squash until asked.

## License

By contributing, you agree that your contributions will be licensed under the [Apache License 2.0](LICENSE.txt).
