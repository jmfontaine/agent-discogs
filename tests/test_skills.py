"""Tests for the bundled-skills command."""

from __future__ import annotations

import importlib.metadata
import json
import re
from pathlib import Path

import pytest
from click.testing import CliRunner

from agent_discogs import cli
from agent_discogs.commands.skills import _description, _render
from tests.conftest import SKILLS, text_error

_SKILLS_DIR = (
    Path(__file__).resolve().parent.parent / "src" / "agent_discogs" / "skills"
)

# Bytes each level may print. `skills get core` is loaded in every session, so
# a later addition must not quietly regrow it: put detail in references/ or in
# the skill the task needs.
_BRIEF_LIMIT = {"core": 24_000}
_BRIEF_LIMIT_DEFAULT = 20_000
# The collection and marketplace skills hold the most Tasks entries, which
# --full prints too.
_FULL_LIMIT = {"core": 85_000, "collection": 50_000, "marketplace": 45_000}
_FULL_LIMIT_DEFAULT = 40_000


def _get(*argv: str) -> str:
    result = CliRunner().invoke(cli, ["skills", "get", *argv])
    assert result.exit_code == 0, result.output
    return result.stdout


def _declared_description(name: str) -> str:
    """The skill's front-matter description, read without the code under test."""
    text = (_SKILLS_DIR / name / "SKILL.md").read_text(encoding="utf-8")
    match = re.search(r"^description: (\S.*)$", text, re.MULTILINE)
    assert match
    return match[1]


class TestSkillsCommand:
    def test_list_is_default_and_aligns_every_skill(self) -> None:
        result = CliRunner().invoke(cli, ["skills"])
        assert result.exit_code == 0
        installed = importlib.metadata.version("agent-discogs")
        lines = result.stdout.splitlines()
        assert [line.split(" ", 1)[0] for line in lines] == list(SKILLS)
        width = max(map(len, SKILLS))
        for name, line in zip(SKILLS, lines, strict=True):
            desc = _declared_description(name)
            assert line == f"{name:<{width}} {desc} (agent-discogs {installed})"
        assert CliRunner().invoke(cli, ["skills", "list"]).stdout == result.stdout

    def test_list_json_has_one_object_per_skill_and_no_color(self) -> None:
        result = CliRunner().invoke(
            cli, ["--color", "always", "skills", "list", "--json"]
        )
        assert result.exit_code == 0
        installed = importlib.metadata.version("agent-discogs")
        assert json.loads(result.stdout) == [
            {
                "name": name,
                "description": _declared_description(name),
                "version": installed,
            }
            for name in SKILLS
        ]
        assert "\x1b" not in result.stdout
        assert "\x1b" not in result.stderr

    @pytest.mark.parametrize("name", SKILLS)
    def test_get_prints_the_named_skill(self, name: str) -> None:
        out = _get(name)
        assert out.startswith(f"---\nname: {name}\n")
        assert "<!-- references/" not in out

    def test_get_core_prints_the_guide_without_references(self) -> None:
        assert "## Core Workflow" in _get("core")

    def test_get_core_says_what_the_api_cannot_answer(self) -> None:
        # Loaded in every session, so an agent sees the limits before it
        # spends requests looking for a missing endpoint.
        assert "## What the Discogs API cannot answer" in _get("core")

    def test_get_core_full_appends_its_references(self) -> None:
        brief = _get("core")
        full = _get("core", "--full")
        assert full.startswith(brief)
        refs = sorted(p.name for p in (_SKILLS_DIR / "core" / "references").iterdir())
        assert refs
        for name in refs:
            assert f"<!-- references/{name} -->" in full
        assert "# Command Reference" in full

    @pytest.mark.parametrize("other", [s for s in SKILLS if s != "core"])
    def test_get_core_full_leaves_out_the_other_skills(self, other: str) -> None:
        full = _get("core", "--full")
        text = (_SKILLS_DIR / other / "SKILL.md").read_text(encoding="utf-8")
        # Every substantial line: a fact said in two skills drifts apart.
        shared = [line for line in text.splitlines() if len(line) > 60 and line in full]
        assert shared == []

    @pytest.mark.parametrize("name", SKILLS)
    def test_each_level_stays_within_its_size_budget(self, name: str) -> None:
        assert len(_get(name).encode()) <= _BRIEF_LIMIT.get(name, _BRIEF_LIMIT_DEFAULT)
        limit = _FULL_LIMIT.get(name, _FULL_LIMIT_DEFAULT)
        assert len(_get(name, "--full").encode()) <= limit

    def test_path(self) -> None:
        root = CliRunner().invoke(cli, ["skills", "path"]).stdout.strip()
        assert root.endswith("agent_discogs/skills")
        for name in SKILLS:
            argv = ["skills", "path", name]
            assert CliRunner().invoke(cli, argv).stdout.strip() == f"{root}/{name}"

    def test_unknown_skill(self) -> None:
        available = ", ".join(SKILLS)
        for argv in (["skills", "get", "nope"], ["skills", "path", "nope"]):
            result = CliRunner().invoke(cli, argv)
            expected = f"✗ Unknown skill 'nope'. Available: {available}\n"
            assert text_error(result) == expected

    def test_help_mentions_skills(self) -> None:
        result = CliRunner().invoke(cli, ["--help"])
        assert "skills get core" in result.stdout

    def test_render_and_description_for_a_bare_skill(self, tmp_path: Path) -> None:
        (tmp_path / "SKILL.md").write_text("# Solo\n", encoding="utf-8")
        assert _render(tmp_path, full=True) == "# Solo\n"
        assert _description("# Solo\n") == ""
