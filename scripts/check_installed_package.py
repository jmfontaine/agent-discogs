#!/usr/bin/env python
"""Smoke-check an installed agent-discogs distribution.

Run with the interpreter of a clean environment where the wheel or sdist was
installed from its published metadata (not from ``uv.lock``). It checks that
the package really comes from that environment, that the dependency floors the
metadata is supposed to enforce held, and that the console script works
without touching the network: ``--version`` and the bundled skill, which the
CLI serves from package data and which a checkout-based test run cannot prove
made it into the wheel.

The pydantic check mirrors AGENTS.md: on Python 3.15 the discogs-sdk marker
must resolve pydantic 2.14 or later (the first series with cp315 pydantic-core
wheels), with no upper bound; everywhere else this project's own ``>=2.12.5``
floor applies.
"""

from __future__ import annotations

import subprocess
import sys
import sysconfig
from importlib.metadata import version
from pathlib import Path

import pydantic

import agent_discogs

MINIMUM_PYDANTIC = (2, 12)
# Mirrors discogs-sdk's Python 3.15 clause (`pydantic>=2.14;
# python_full_version == '3.15.*'`).
MINIMUM_PYDANTIC_315 = (2, 14)

SKILLS = ("collection", "core", "marketplace", "pressings")
# The references `skills get core --full` appends.
REFERENCES = (
    "commands.md",
    "discogs-domain.md",
    "errors.md",
    "json-output.md",
    "search-patterns.md",
)


def fail(message: str) -> None:
    print(f"check_installed_package: {message}", file=sys.stderr)
    raise SystemExit(1)


def check_import_location() -> None:
    package = Path(agent_discogs.__file__).resolve()
    site_packages = Path(sysconfig.get_paths()["purelib"]).resolve()
    if not package.is_relative_to(site_packages):
        fail(f"agent_discogs was imported from {package}, not from {site_packages}")
    print(f"import path ok: {package}")


def check_pydantic() -> None:
    major, minor = pydantic.VERSION.split(".")[:2]
    installed = int(major), int(minor)
    python = sys.version.split()[0]
    floor = (
        MINIMUM_PYDANTIC_315 if sys.version_info[:2] == (3, 15) else MINIMUM_PYDANTIC
    )
    if installed < floor:
        fail(
            f"pydantic {pydantic.VERSION} is below the "
            f"{'.'.join(map(str, floor))} floor required on Python {python}"
        )
    print(f"pydantic ok: {pydantic.VERSION} on Python {python}")


def run_cli(*args: str) -> str:
    # The console script next to this interpreter: proves the entry point was
    # installed, not just that the package imports.
    script = Path(sys.executable).with_name("agent-discogs")
    if not script.exists():
        fail(f"console script not installed at {script}")
    result = subprocess.run(  # noqa: S603  # argv is fixed; nothing user-supplied
        [str(script), *args], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        command = f"agent-discogs {' '.join(args)}"
        fail(f"`{command}` exited {result.returncode}:\n{result.stderr}")
    return result.stdout


def check_cli() -> None:
    installed = version("agent-discogs")
    out = run_cli("--version")
    if installed not in out:
        fail(f"--version printed {out.strip()!r}, expected {installed}")

    out = run_cli("skills", "list")
    listed = [line.split(" ", 1)[0] for line in out.splitlines()]
    if listed != list(SKILLS):
        fail(f"skills list names {listed}, expected {list(SKILLS)}")

    out = run_cli("skills", "get", "core", "--full")
    if not out.startswith("---"):
        fail("skills get core --full did not print SKILL.md front-matter")
    missing = [ref for ref in REFERENCES if f"<!-- references/{ref} -->" not in out]
    if missing:
        fail(f"skills get core --full is missing {', '.join(missing)}")
    print(f"cli ok: agent-discogs {installed}, skill core with {len(REFERENCES)} refs")


def main() -> None:
    check_import_location()
    check_pydantic()
    check_cli()


if __name__ == "__main__":
    main()
