"""Status command handler."""

from __future__ import annotations

from importlib.metadata import version

import click

from agent_discogs.client import auth_mode
from agent_discogs.config import cache_dir
from agent_discogs.formatting import format_status


@click.command()
def status() -> None:
    """Show version, auth mode, and cache location."""
    print(
        format_status(
            auth_mode=auth_mode(),
            cache_dir=cache_dir(),
            version=version("agent-discogs"),
        )
    )
