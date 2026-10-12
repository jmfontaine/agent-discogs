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
    # KLUDGE: deadcode runs on Python 3.13 via uvx, not the project's 3.14, and
    # is deliberately not a dev dependency. deadcode 2.4.1 calls ast.Str, which
    # Python 3.14 removed, so it crashes outright on 3.14. Upstream is dormant:
    # no release since 2024-08, and the crash is open as
    # https://github.com/albertas/deadcode/issues/37
    # Pinning the interpreter is safe because deadcode only parses source that
    # already has to support Python 3.10. It would break if this project ever
    # adopts 3.14-only syntax.
    # Remove the pin and restore the dev dependency once deadcode supports 3.14.
    output=$(uvx --python 3.13 deadcode@2.4.1 src tests 2>&1)
    status=$?
    echo "$output"
    # KLUDGE: deadcode exits 0 even when it *does* report findings, so grepping
    # its DC0* codes is the only way to fail on them. Test the exit status too:
    # checking output alone treats a crash as success, because a traceback
    # contains no "DC0". That is exactly how the 3.14 breakage above stayed
    # invisible here while CI (which runs with `bash -e`) failed on it.
    if [ "$status" -ne 0 ] || grep -q "DC0" <<<"$output"; then
        exit 1
    fi

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
    uv run pre-commit run --all-files

# Install pre-commit hooks
pre-commit-install:
    uv run pre-commit install

# Update pre-commit hooks to latest versions
pre-commit-update:
    uv run pre-commit autoupdate --freeze

# Run all quality assurance checks
qa: dead-code deps-unused format-check lint type-check verify-types

# Tag, push, and monitor the publish workflow
release:
    #!/usr/bin/env bash
    set -euo pipefail
    fail() { echo "Error: $*" >&2; exit 1; }
    version=$(uv run python -c "import tomllib, pathlib; print(tomllib.loads(pathlib.Path('pyproject.toml').read_text())['project']['version'])")
    tag="v${version}"
    branch=$(git symbolic-ref --quiet --short HEAD) || fail "HEAD is detached; check out main"
    [ "$branch" = main ] || fail "on branch $branch; releases are cut from main"
    [ -z "$(git status --porcelain)" ] || fail "working tree is not clean"
    git fetch --quiet origin main || fail "could not fetch main from origin"
    head=$(git rev-parse HEAD)
    [ "$head" = "$(git rev-parse refs/remotes/origin/main)" ] || fail "main is not in sync with origin/main; pull or push first"
    if git rev-parse --quiet --verify "refs/tags/$tag" >/dev/null; then
        fail "tag $tag already exists locally"
    fi
    # ls-remote --exit-code: 0 = tag found, 2 = not found, anything else = the query failed.
    status=0
    git ls-remote --exit-code --tags origin "refs/tags/$tag" >/dev/null || status=$?
    case "$status" in
        0) fail "tag $tag already exists on origin" ;;
        2) ;;
        *) fail "could not check origin for tag $tag" ;;
    esac
    echo "Creating signed tag $tag..."
    git tag -s "$tag" -m "Release $tag"
    echo "Pushing main and $tag to origin in one atomic push..."
    if ! git push --atomic origin main "refs/tags/$tag"; then
        # A failed push is not proof of rejection: the connection can drop after
        # origin accepted it. Ask origin before touching the local tag.
        status=0
        git ls-remote --exit-code --tags origin "refs/tags/$tag" >/dev/null || status=$?
        case "$status" in
            2)
                cleanup="the local tag was deleted"
                git tag -d "$tag" >/dev/null || cleanup="deleting the local tag failed; run: git tag -d $tag"
                fail "push failed; origin has no $tag, so nothing was pushed or published; $cleanup" ;;
            0)
                fail "git push reported an error, but $tag is on origin, so publishing has started; kept the local tag; check https://github.com/jmfontaine/agent-discogs/actions" ;;
            *)
                fail "git push failed and origin could not be checked, so $tag may have been pushed; kept the local tag; check with 'git ls-remote --tags origin refs/tags/$tag' and https://github.com/jmfontaine/agent-discogs/actions" ;;
        esac
    fi
    echo "Waiting for the publish workflow run for $tag..."
    run_id=""
    for _ in $(seq 24); do
        # A failing gh call (network blip, rate limit) costs one attempt, not the run.
        run_id=$(gh run list --workflow=publish.yml --event=push --branch="$tag" --commit="$head" --limit=1 --json=databaseId --jq='.[0].databaseId // empty') || run_id=""
        [ -n "$run_id" ] && break
        sleep 5
    done
    [ -n "$run_id" ] || fail "no publish run for $tag appeared within 2 minutes; $tag is pushed, so check https://github.com/jmfontaine/agent-discogs/actions"
    gh run watch --exit-status "$run_id"

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
    uv run pre-commit install  # Install pre-commit hooks
    echo "Run 'source .venv/bin/activate' to activate the Python virtual environment"

# Run tests
test *args:
    uv run pytest --cov --cov-report=term-missing {{ args }}

# Run live-API tests (doc examples resolve against Discogs; needs DISCOGS_TOKEN)
test-live *args:
    uv run pytest -m live --no-cov {{ args }}

# Run type checker
type-check:
    uv run ty check

# Audit public API type annotation coverage
verify-types:
    uv run pyright --ignoreexternal --verifytypes agent_discogs
