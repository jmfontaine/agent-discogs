"""Tests for the bundled-skills command."""

from __future__ import annotations

import importlib.metadata
from pathlib import Path

from click.testing import CliRunner

from agent_discogs import cli


class TestSkillsCommand:
    def test_list_is_default_and_shows_description_and_version(self) -> None:
        result = CliRunner().invoke(cli, ["skills"])
        assert result.exit_code == 0
        assert result.output.startswith("core ")
        assert "usage guide" in result.output
        installed = importlib.metadata.version("agent-discogs")
        assert result.output.rstrip().endswith(f"(agent-discogs {installed})")
        assert CliRunner().invoke(cli, ["skills", "list"]).output == result.output

    def test_get_core_prints_the_guide_without_references(self) -> None:
        result = CliRunner().invoke(cli, ["skills", "get", "core"])
        assert result.exit_code == 0
        assert result.output.startswith("---\nname: core\n")
        assert "## Core Workflow" in result.output
        assert "<!-- references/" not in result.output

    def test_get_core_full_appends_every_reference(self) -> None:
        brief = CliRunner().invoke(cli, ["skills", "get", "core"]).output
        result = CliRunner().invoke(cli, ["skills", "get", "core", "--full"])
        assert result.exit_code == 0
        assert result.output.startswith(brief)
        for name in (
            "commands.md",
            "discogs-domain.md",
            "pressings-guide.md",
            "search-patterns.md",
        ):
            assert f"<!-- references/{name} -->" in result.output
        assert "# Command Reference" in result.output

    def test_path(self) -> None:
        root = CliRunner().invoke(cli, ["skills", "path"]).output.strip()
        core = CliRunner().invoke(cli, ["skills", "path", "core"]).output.strip()
        assert root.endswith("agent_discogs/skills")
        assert core == f"{root}/core"

    def test_unknown_skill(self) -> None:
        for argv in (["skills", "get", "nope"], ["skills", "path", "nope"]):
            result = CliRunner().invoke(cli, argv)
            assert result.exit_code == 1
            assert "Unknown skill 'nope'. Available: core" in result.output

    def test_help_mentions_skills(self) -> None:
        result = CliRunner().invoke(cli, ["--help"])
        assert "skills get core" in result.output

    def test_render_and_description_for_a_bare_skill(self, tmp_path: Path) -> None:
        from agent_discogs.commands.skills import _description, _render

        (tmp_path / "SKILL.md").write_text("# Solo\n", encoding="utf-8")
        assert _render(tmp_path, full=True) == "# Solo\n"
        assert _description("# Solo\n") == ""
