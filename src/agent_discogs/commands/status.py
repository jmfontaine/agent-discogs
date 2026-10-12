"""Status command handler."""

from __future__ import annotations

from importlib.metadata import version

import click

from agent_discogs.config import cache_dir, has_token
from agent_discogs.formatting import format_status


@click.command()
def status() -> None:
    """Show session and auth info."""
    print(
        format_status(
            authenticated=has_token(),
            cache_dir=cache_dir(),
            version=version("agent-discogs"),
        )
    )
