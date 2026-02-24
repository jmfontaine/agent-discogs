"""Status command handler."""

from __future__ import annotations

from importlib.metadata import version

import click

from agent_discogs.client import CACHE_DIR, has_token
from agent_discogs.formatting import format_status


@click.command()
def status() -> None:
    """Show session and auth info."""
    print(
        format_status(
            authenticated=has_token(),
            cache_dir=CACHE_DIR,
            version=version("agent-discogs"),
        )
    )
