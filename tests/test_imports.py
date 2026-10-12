"""The package's import graph: shared configuration sits in a leaf module and
no module imports another that imports it back, even from inside a function."""

from __future__ import annotations

import ast
from graphlib import TopologicalSorter
from pathlib import Path

import agent_discogs

PACKAGE = "agent_discogs"
ROOT = Path(agent_discogs.__file__).parent


def _module_name(path: Path) -> str:
    parts = path.relative_to(ROOT).with_suffix("").parts
    return ".".join((PACKAGE, *parts)).removesuffix(".__init__")


def _first_party_imports(path: Path, modules: set[str]) -> set[str]:
    """Every package module `path` imports, at any depth in the file.

    `TYPE_CHECKING` blocks count too: a type-only back-reference is still a
    cycle in the design, even though it never runs."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        # Relative imports would slip past the name matching below.
        assert not (isinstance(node, ast.ImportFrom) and node.level), (
            f"{path.name}:{node.lineno} uses a relative import"
        )
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names if a.name in modules)
        elif isinstance(node, ast.ImportFrom) and node.module in modules:
            # `from agent_discogs import trace` imports the submodule `trace`.
            for alias in node.names:
                sub = f"{node.module}.{alias.name}"
                found.add(sub if sub in modules else node.module)
    return found


def _import_graph() -> dict[str, set[str]]:
    paths = {_module_name(p): p for p in ROOT.rglob("*.py")}
    modules = set(paths)
    return {
        name: _first_party_imports(path, modules) - {name}
        for name, path in paths.items()
    }


def test_config_imports_nothing_from_the_package() -> None:
    assert _import_graph()[f"{PACKAGE}.config"] == set()


def test_package_has_no_import_cycle() -> None:
    # Raises graphlib.CycleError naming the cycle.
    TopologicalSorter(_import_graph()).prepare()
