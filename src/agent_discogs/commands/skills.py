"""Skills command — serve the bundled agent guide from the installed package."""

from __future__ import annotations

import sys
from importlib.metadata import version
from importlib.resources import files
from typing import TYPE_CHECKING

import click

from agent_discogs.json_output import dump
from agent_discogs.style import off_for_json

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


def _complete_name(
    _ctx: click.Context, _param: click.Parameter, incomplete: str
) -> list[str]:
    return [name for name in _skill_dirs() if name.startswith(incomplete)]


@click.group(invoke_without_command=True)
@click.pass_context
def skills(ctx: click.Context) -> None:
    """List or print bundled agent skills (usage guides matching this version)."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(list_skills)


@skills.command("list")
@click.option(
    "--json", "json_output", is_flag=True, callback=off_for_json, help="Output as JSON."
)
def list_skills(json_output: bool) -> None:
    """List available skills, tagged with the installed version they describe."""
    installed = version("agent-discogs")
    dirs = _skill_dirs()
    descriptions = {
        name: _description((skill_dir / "SKILL.md").read_text(encoding="utf-8"))
        for name, skill_dir in dirs.items()
    }
    if json_output:
        dump(
            [
                {"name": name, "description": desc, "version": installed}
                for name, desc in descriptions.items()
            ]
        )
        return
    width = max(map(len, dirs), default=0)
    for name, desc in descriptions.items():
        print(f"{name:<{width}} {desc} (agent-discogs {installed})")


@skills.command("get")
@click.argument("name", shell_complete=_complete_name)
@click.option(
    "--full", is_flag=True, default=False, help="Append the skill's references/*.md"
)
def get_skill(name: str, full: bool) -> None:
    """Print a skill's content."""
    print(_render(_lookup(name), full=full), end="")


@skills.command("path")
@click.argument("name", required=False, shell_complete=_complete_name)
def skill_path(name: str | None) -> None:
    """Print the directory holding bundled skills, or one skill."""
    print(_SKILLS_ROOT if name is None else _lookup(name))
