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
must resolve a 2.14 series pydantic (the first with cp315 pydantic-core
wheels); everywhere else this project's own ``>=2.12.5`` floor applies.
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
# KLUDGE: mirrors discogs-sdk's temporary Python 3.15 clause
# (`pydantic>=2.14.0b2,<2.15; python_version=='3.15'`). When the SDK relaxes it
# to `pydantic>=2.14`, drop only the upper bound here: the 3.15 branch must keep
# asserting the 2.14 minimum.
PYDANTIC_RANGE_315 = ((2, 14), (2, 15))

REFERENCES = (
    "commands.md",
    "discogs-domain.md",
    "pressings-guide.md",
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
    if sys.version_info[:2] == (3, 15):
        low, high = PYDANTIC_RANGE_315
        if not low <= installed < high:
            fail(
                f"pydantic {pydantic.VERSION} is outside the "
                f"{'.'.join(map(str, low))}-{'.'.join(map(str, high))} range "
                f"required on Python {python}"
            )
    elif installed < MINIMUM_PYDANTIC:
        fail(
            f"pydantic {pydantic.VERSION} is below the "
            f"{'.'.join(map(str, MINIMUM_PYDANTIC))} floor required on Python {python}"
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
    if not out.startswith("core"):
        fail(f"skills list does not start with the core skill: {out.strip()!r}")

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
