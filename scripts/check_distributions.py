#!/usr/bin/env python
"""Validate the contents of built distributions before they are published.

Usage: ``python scripts/check_distributions.py dist``

The wheel and the sdist are held to different contracts. The wheel is what gets
installed, so it must carry the runtime package and nothing else; the sdist is
what a wheel gets rebuilt from, so it must carry the sources and packaging
metadata, and test files legitimately belong in it.

These are checks the test suite cannot make: a wheel missing ``py.typed``, the
license, or the bundled skill an agent loads at runtime behaves identically
under ``uv run pytest``, which imports from the checkout.
"""

from __future__ import annotations

import sys
import tarfile
import zipfile
from pathlib import Path

WHEEL_REQUIRED = (
    "agent_discogs/__init__.py",
    "agent_discogs/__main__.py",
    "agent_discogs/py.typed",
    # `agent-discogs skills get core` serves these from the installed package.
    "agent_discogs/skills/core/SKILL.md",
    "agent_discogs/skills/core/references/commands.md",
    "agent_discogs/skills/core/references/discogs-domain.md",
    "agent_discogs/skills/core/references/pressings-guide.md",
    "agent_discogs/skills/core/references/search-patterns.md",
)
# Project-only trees: useful in the repository and in the sdist, wrong in the
# importable distribution. `skills` is the repo-root discovery stub, not the
# package's `agent_discogs/skills`.
WHEEL_FORBIDDEN_ROOTS = ("tests", "scripts", "docs", "skills")

SDIST_REQUIRED = (
    "pyproject.toml",
    "PKG-INFO",
    "LICENSE.txt",
    "README.md",
    "src/agent_discogs/__init__.py",
    "src/agent_discogs/py.typed",
    "src/agent_discogs/skills/core/SKILL.md",
)


def fail(message: str) -> None:
    print(f"check_distributions: {message}", file=sys.stderr)
    raise SystemExit(1)


def check_wheel(path: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()

    missing = [name for name in WHEEL_REQUIRED if name not in names]
    if missing:
        fail(f"{path.name} is missing {', '.join(missing)}")

    dist_info = {
        name.rsplit("/", 1)[1]
        for name in names
        if "dist-info/" in name and not name.endswith("/")
    }
    required_metadata = {"METADATA", "RECORD", "entry_points.txt", "LICENSE.txt"}
    missing_metadata = sorted(required_metadata - dist_info)
    if missing_metadata:
        fail(f"{path.name} dist-info is missing {', '.join(missing_metadata)}")

    leaked = sorted(
        {name for name in names if name.split("/", 1)[0] in WHEEL_FORBIDDEN_ROOTS}
    )
    if leaked:
        fail(f"{path.name} ships project-only paths: {', '.join(leaked)}")

    print(f"ok: {path.name} ({len(names)} entries)")


def check_sdist(path: Path) -> None:
    with tarfile.open(path) as archive:
        # Every member is under a single `<name>-<version>/` prefix.
        names = {name.split("/", 1)[1] for name in archive.getnames() if "/" in name}

    missing = [name for name in SDIST_REQUIRED if name not in names]
    if missing:
        fail(f"{path.name} is missing {', '.join(missing)}")

    print(f"ok: {path.name} ({len(names)} entries)")


def main() -> None:
    args = sys.argv[1:]
    if len(args) != 1:
        fail("usage: check_distributions.py <dist-directory>")

    dist = Path(args[0])
    wheels = sorted(dist.glob("*.whl"))
    sdists = sorted(dist.glob("*.tar.gz"))

    if len(wheels) != 1 or len(sdists) != 1:
        fail(
            f"expected exactly one wheel and one sdist in {dist}, "
            f"found {len(wheels)} and {len(sdists)}"
        )

    check_wheel(wheels[0])
    check_sdist(sdists[0])


if __name__ == "__main__":
    main()
