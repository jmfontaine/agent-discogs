"""Tests for the color decision: who gets color, per stream."""

from __future__ import annotations

import io

import click
import pytest

from agent_discogs import style


class _Terminal(io.StringIO):
    """A stream that reports being a terminal."""

    def isatty(self) -> bool:
        return True


@pytest.fixture(autouse=True)
def _color_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """A color-capable terminal environment, whatever the developer's shell has."""
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")


def _enabled(mode: style.Mode, stream: io.StringIO) -> bool:
    with click.Context(click.Command("cli")) as ctx:
        style.configure(ctx, mode)
        return style.enabled(stream)


class TestEnabled:
    def test_auto_colors_a_terminal_only(self) -> None:
        assert _enabled("auto", _Terminal())
        assert not _enabled("auto", io.StringIO())

    @pytest.mark.parametrize(
        ("name", "value"), [("NO_COLOR", "1"), ("NO_COLOR", "false"), ("TERM", "dumb")]
    )
    def test_auto_respects_the_environment(
        self, monkeypatch: pytest.MonkeyPatch, name: str, value: str
    ) -> None:
        monkeypatch.setenv(name, value)
        assert not _enabled("auto", _Terminal())

    def test_empty_no_color_is_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NO_COLOR", "")
        assert _enabled("auto", _Terminal())

    def test_always_and_never_override_everything(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assert not _enabled("never", _Terminal())
        monkeypatch.setenv("NO_COLOR", "1")
        monkeypatch.setenv("TERM", "dumb")
        assert _enabled("always", io.StringIO())

    def test_off_outside_a_command(self) -> None:
        assert not style.enabled(_Terminal())

    def test_subcommand_sees_the_group_setting(self) -> None:
        with click.Context(click.Command("cli")) as parent:
            style.configure(parent, "always")
            with click.Context(click.Command("get"), parent=parent):
                assert style.enabled(io.StringIO())
