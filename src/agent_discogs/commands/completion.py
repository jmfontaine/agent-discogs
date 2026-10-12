"""Completion command: print click's shell completion script."""

from __future__ import annotations

import click
from click.shell_completion import (
    BashComplete,
    FishComplete,
    ShellComplete,
    ZshComplete,
)

_SHELLS: dict[str, type[ShellComplete]] = {
    "bash": BashComplete,
    "zsh": ZshComplete,
    "fish": FishComplete,
}

# The installed console script, whatever name this process was started under
# (`python -m agent_discogs` included), and the variable click's main() reads
# for that name.
_PROG_NAME = "agent-discogs"
_COMPLETE_VAR = "_AGENT_DISCOGS_COMPLETE"


@click.command()
@click.argument("shell", type=click.Choice(list(_SHELLS)))
@click.pass_context
def completion(ctx: click.Context, shell: str) -> None:
    """Print the completion script for SHELL (bash, zsh, fish).

    For an interactive shell; load it from the shell's startup file, e.g.
    eval "$(agent-discogs completion zsh)" in ~/.zshrc.
    """
    complete = _SHELLS[shell](ctx.find_root().command, {}, _PROG_NAME, _COMPLETE_VAR)
    print(complete.source())
