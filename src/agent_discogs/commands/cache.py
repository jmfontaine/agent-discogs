"""Cache command handler."""

from __future__ import annotations

import click

from agent_discogs.client import get_client
from agent_discogs.errors import fail
from agent_discogs.json_output import dump


@click.command()
# `clear` is the only action, so the handler needs no branch on it.
@click.argument("action", type=click.Choice(["clear"]), expose_value=False)
@click.option("--json", "json_output", is_flag=True, help="Output as JSON.")
def cache(json_output: bool) -> None:
    """Manage cache."""
    try:
        get_client().clear_cache()
    # classify() maps every exception to a coded error: a purge or an open that
    # fails raises CacheError (cache_error), and the database is left as it was.
    except Exception as e:  # noqa: BLE001
        fail(e, json_output=json_output)
    if json_output:
        dump({"cleared": True})
    else:
        print("Cache cleared.")
