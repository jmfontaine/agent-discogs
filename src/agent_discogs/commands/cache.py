"""Cache command handler."""

from __future__ import annotations

import click

from agent_discogs.client import get_client


@click.command()
@click.argument("action", type=click.Choice(["clear"]))
def cache(action: str) -> None:
    """Manage cache."""
    if action == "clear":
        get_client().clear_cache()
        print("Cache cleared.")
