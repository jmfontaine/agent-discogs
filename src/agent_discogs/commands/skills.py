"""Skills command — serve the bundled agent guide from the installed package."""

from __future__ import annotations

import sys
from importlib.metadata import version
from importlib.resources import files
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from importlib.abc import Traversable

_SKILLS_ROOT = files("agent_discogs") / "skills"


def _skill_dirs() -> dict[str, Traversable]:
    return {
        entry.name: entry
        for entry in sorted(_SKILLS_ROOT.iterdir(), key=lambda e: e.name)
        if entry.is_dir() and (entry / "SKILL.md").is_file()
    }


def _description(skill_md: str) -> str:
    """The `description:` line of the skill's front-matter, or ''."""
    for line in skill_md.splitlines():
        if line.startswith("description:"):
            return line.removeprefix("description:").strip()
    return ""


def _render(skill_dir: Traversable, *, full: bool) -> str:
    text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    if not full:
        return text
    refs = skill_dir / "references"
    if not refs.is_dir():
        return text
    parts = [text]
    parts.extend(
        f"\n\n---\n\n<!-- references/{ref.name} -->\n\n"
        + ref.read_text(encoding="utf-8")
        for ref in sorted(refs.iterdir(), key=lambda e: e.name)
        if ref.name.endswith(".md")
    )
    return "".join(parts)


def _lookup(name: str) -> Traversable:
    dirs = _skill_dirs()
    if name not in dirs:
        print(
            f"✗ Unknown skill '{name}'. Available: {', '.join(dirs)}", file=sys.stderr
        )
        sys.exit(1)
    return dirs[name]


@click.group(invoke_without_command=True)
@click.pass_context
def skills(ctx: click.Context) -> None:
    """List or print bundled agent skills (usage guides matching this version)."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(list_skills)


@skills.command("list")
def list_skills() -> None:
    """List available skills, tagged with the installed version they describe."""
    tag = f"(agent-discogs {version('agent-discogs')})"
    for name, skill_dir in _skill_dirs().items():
        desc = _description((skill_dir / "SKILL.md").read_text(encoding="utf-8"))
        print(f"{name:<8} {desc} {tag}")


@skills.command("get")
@click.argument("name")
@click.option(
    "--full", is_flag=True, default=False, help="Append the skill's references/*.md"
)
def get_skill(name: str, full: bool) -> None:
    """Print a skill's content."""
    print(_render(_lookup(name), full=full), end="")


@skills.command("path")
@click.argument("name", required=False)
def skill_path(name: str | None) -> None:
    """Print the directory holding bundled skills, or one skill."""
    print(_SKILLS_ROOT if name is None else _lookup(name))
