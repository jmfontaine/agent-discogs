set quiet := true

_list:
    just --list

# Preview release notes for unreleased changes
changelog-preview:
    uvx git-cliff --unreleased

# Check for unused or undeclared deps
deps-unused:
    uv run deptry src

# Update deps to latest versions
deps-update:
    uv lock --upgrade
    uv sync --all-extras --all-groups

# Find dead code
dead-code:
    #!/usr/bin/env bash
    output=$(uv run deadcode src tests 2>&1)
    echo "$output"
    echo "$output" | grep -q "DC0" && exit 1 || exit 0

# Run formatters
format:
    uv run pyproject-fmt pyproject.toml
    uv run ruff format

# Check formatting without modifying files
format-check:
    uv run pyproject-fmt --check pyproject.toml
    uv run ruff format --check

# Run linter
lint:
    uv run ruff check

# Run linter and fix issues
lint-fix:
    uv run ruff check --fix

# Run pre-commit on all files
pre-commit:
    pre-commit run --all-files

# Install pre-commit hooks
pre-commit-install:
    pre-commit install

# Update pre-commit hooks to latest versions
pre-commit-update:
    pre-commit autoupdate --freeze

# Run all quality assurance checks
qa: dead-code deps-unused format-check lint type-check verify-types

# Tag, push, and monitor the publish workflow
release:
    #!/usr/bin/env bash
    set -euo pipefail
    version=$(uv run python -c "import tomllib, pathlib; print(tomllib.loads(pathlib.Path('pyproject.toml').read_text())['project']['version'])")
    tag="v${version}"
    if git rev-parse "$tag" >/dev/null 2>&1; then
        echo "Error: tag $tag already exists"
        exit 1
    fi
    if [ -n "$(git status --porcelain)" ]; then
        echo "Error: working tree is not clean"
        exit 1
    fi
    echo "Creating signed tag $tag..."
    git tag -s "$tag" -m "Release $tag"
    echo "Pushing main and $tag to origin..."
    git push origin main "$tag"
    echo "Waiting for publish workflow to start..."
    sleep 5
    gh run watch --exit-status "$(gh run list --workflow=publish.yml --branch="$tag" --limit=1 --json=databaseId --jq='.[0].databaseId')"

# Use local discogs-sdk for development testing
sdk-link:
    #!/usr/bin/env bash
    set -euo pipefail
    if grep -q 'sources.discogs-sdk' pyproject.toml; then
        echo "Already linked to local discogs-sdk"
        exit 0
    fi
    python3 -c "
    p = __import__('pathlib').Path('pyproject.toml')
    t = p.read_text()
    p.write_text(t.replace('[tool.deadcode]',
        '[tool.uv]\nsources.discogs-sdk = { path = \"../discogs-sdk\", editable = true }\n\n[tool.deadcode]'))
    "
    uv run pyproject-fmt pyproject.toml
    uv sync

# Revert to published discogs-sdk
sdk-unlink:
    #!/usr/bin/env bash
    set -euo pipefail
    if ! grep -q 'sources.discogs-sdk' pyproject.toml; then
        echo "Already using published discogs-sdk"
        exit 0
    fi
    python3 -c "
    import re, pathlib
    p = pathlib.Path('pyproject.toml')
    t = p.read_text()
    t = re.sub(r'\n*\[tool\.uv\]\n[^[]*', '\n\n', t)
    p.write_text(t)
    "
    uv run pyproject-fmt pyproject.toml
    uv sync

# Set local dev environment up
setup:
    uv sync --all-extras --all-groups  # Install dependencies
    pre-commit install  # Install pre-commit hooks
    echo "Run 'source .venv/bin/activate' to activate the Python virtual environment"

# Run tests
test *args:
    uv run pytest --cov --cov-report=term-missing {{ args }}

# Run type checker
type-check:
    uv run ty check

# Audit public API type annotation coverage
verify-types:
    uv run pyright --ignoreexternal --verifytypes agent_discogs
