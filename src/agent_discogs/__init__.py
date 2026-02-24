"""agent-discogs: Token-efficient Discogs CLI for AI agents."""

from __future__ import annotations

import sys
from typing import Any

import click

from agent_discogs.commands.cache import cache
from agent_discogs.commands.get import get, price, tracks
from agent_discogs.commands.search import search
from agent_discogs.commands.status import status

ALIASES: dict[str, str] = {
    "find": "search",
    "query": "search",
    "fetch": "get",
    "show": "get",
}


class AliasGroup(click.Group):
    """Click group that supports command aliases."""

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        cmd = super().get_command(ctx, cmd_name)
        if cmd is not None:
            return cmd
        canonical = ALIASES.get(cmd_name)
        if canonical is not None:
            return super().get_command(ctx, canonical)
        return None

    def resolve_command(
        self, ctx: click.Context, args: list[str]
    ) -> tuple[str | None, click.Command | None, list[str]]:
        # Ensure the canonical name is used so help text is consistent
        _, cmd, remaining = super().resolve_command(ctx, args)
        if cmd is not None:
            return cmd.name, cmd, remaining
        return None, None, args

    def format_usage(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        super().format_usage(ctx, formatter)

    def invoke(self, ctx: click.Context) -> Any:
        try:
            return super().invoke(ctx)
        except KeyboardInterrupt:
            sys.exit(130)
        except click.exceptions.Exit:
            raise
        except click.exceptions.Abort:
            raise
        except click.exceptions.UsageError:
            raise
        except Exception as e:
            from agent_discogs.errors import format_error

            print(format_error(e), file=sys.stderr)
            sys.exit(1)


@click.group(cls=AliasGroup, invoke_without_command=True)
@click.version_option(package_name="agent-discogs", prog_name="agent-discogs")
@click.pass_context
def cli(ctx: click.Context) -> None:
    """Token-efficient Discogs CLI for AI agents."""
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


cli.add_command(cache)
cli.add_command(get)
cli.add_command(price)
cli.add_command(search)
cli.add_command(status)
cli.add_command(tracks)


def main() -> None:
    """Entry point for the CLI."""
    cli()
