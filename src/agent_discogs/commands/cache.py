"""Cache command handler."""

from __future__ import annotations

import logging

import click
from discogs_sdk import CacheError

from agent_discogs.client import get_client


@click.command()
@click.argument("action", type=click.Choice(["clear"]))
def cache(action: str) -> None:
    """Manage cache."""
    if action == "clear":
        client = get_client()
        try:
            client.clear_cache()
        except CacheError as exc:
            # A cache that cannot be opened is a command error like any other.
            if exc.operation != "clear the cache":
                raise
            # discogs-sdk 0.8.0 raises when the purge fails; 0.7.0 logged this
            # same message as a warning and returned, which is what this keeps.
            logging.getLogger("discogs_sdk").warning("%s", exc)
        print("Cache cleared.")
