"""Terminal color: for a person at a terminal, never for an agent.

Agents read the CLI's output through their tool calls, where an escape code
is pure noise, so color is off unless the stream is clearly a person's: under
`--color auto` (the default) a stream is colored only when it is a terminal,
`NO_COLOR` is unset or empty (no-color.org) and `TERM` is not `dumb`.
`--color always` and `--color never` override all three. stdout and stderr
are decided separately. A `--json` command is for a program, so it is never
colored, on either stream.

Only the CLI's own framing is painted, never a Discogs value: a view marks
framing with a `Span` and paints it after `sanitize.line()`, so data can
neither carry an escape code nor be painted like framing.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Literal, NamedTuple

import click

if TYPE_CHECKING:
    from typing import TextIO

Mode = Literal["auto", "always", "never"]
MODES: tuple[Mode, ...] = ("auto", "always", "never")

Style = Literal["ref", "label", "error", "warning", "dim"]

# The palette, in one place: the SGR parameters of each framing style.
_SGR: dict[Style, str] = {
    "ref": "36",  # cyan: @r…, @a…, @m…, @l…
    "label": "1",  # bold: Tracklist:, Credits:, Members:, ...
    "error": "31",  # red: the ✗ of an error
    "warning": "33",  # yellow: the ⚠ budget warning
    "dim": "2",  # faint: counts, timings, Next page: / Continue scan:
}
_RESET = "\x1b[0m"

# ctx.meta is shared by a command's whole context chain, and is fresh for each
# invocation, so the setting cannot outlive the command that chose it.
_META_KEY = "agent_discogs.color"


class Span(NamedTuple):
    """CLI framing for a view to paint: never a Discogs value."""

    text: str
    style: Style


def configure(ctx: click.Context, mode: Mode) -> None:
    """Record the `--color` mode for the rest of the invocation."""
    ctx.meta[_META_KEY] = mode


def off_for_json(ctx: click.Context, _param: click.Parameter, json: bool) -> bool:
    """Callback of every `--json` flag: the whole command stays uncolored,
    stderr included, whatever `--color` says."""
    if json:
        configure(ctx, "never")
    return json


def enabled(stream: TextIO) -> bool:
    """Whether text written to `stream` gets color. Off outside a command."""
    ctx = click.get_current_context(silent=True)
    mode: Mode = "never" if ctx is None else ctx.meta.get(_META_KEY, "never")
    if mode != "auto":
        return mode == "always"
    if os.environ.get("NO_COLOR") or os.environ.get("TERM") == "dumb":
        return False
    return stream.isatty()


def paint(text: str, style: Style, *, color: bool) -> str:
    """`text` in `style` when `color` (`enabled()` for the stream it goes
    to), else `text` unchanged. `text` must already be neutralized
    (`sanitize.line()`)."""
    return f"\x1b[{_SGR[style]}m{text}{_RESET}" if color else text
