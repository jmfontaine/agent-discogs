"""Tests for shared configuration."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from click.testing import CliRunner

from agent_discogs import cli, config
from agent_discogs.config import cache_dir


def _no_home() -> Path:
    msg = "Could not determine home directory."
    raise RuntimeError(msg)


class TestCacheDir:
    @pytest.mark.parametrize("value", ["", "relative/cache", "./cache", "~/cache"])
    def test_empty_or_relative_xdg_falls_back_to_home(
        self, value: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("XDG_CACHE_HOME", value)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert cache_dir() == tmp_path / ".cache" / "agent-discogs"

    def test_unset_xdg_falls_back_to_home(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert cache_dir() == tmp_path / ".cache" / "agent-discogs"

    def test_absolute_xdg_is_used_without_looking_up_home(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        monkeypatch.setattr(Path, "home", _no_home)
        assert cache_dir() == tmp_path / "agent-discogs"


@pytest.mark.parametrize("args", [["--help"], ["skills"]])
def test_cache_free_commands_work_without_a_home_directory(
    args: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.setattr(Path, "home", _no_home)
    # Re-run the module body, as a fresh `import` would: it must not need home.
    importlib.reload(config)
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 0, result.output
    assert "agent-discogs" in result.stdout
