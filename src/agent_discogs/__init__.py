"""agent-discogs: Token-efficient Discogs CLI for AI agents."""

from __future__ import annotations

import sys
from typing import Any

import click

from agent_discogs.commands.cache import cache
from agent_discogs.commands.get import get, price, tracks
from agent_discogs.commands.search import search
from agent_discogs.commands.skills import skills
from agent_discogs.commands.status import status

ALIASES: dict[str, str] = {
    "find": "search",
    "query": "search",
    "fetch": "get",
    "show": "get",
}


_HELP_TEXT = """\
agent-discogs - token-efficient Discogs CLI for AI agents

Usage: agent-discogs <command> [args] [options]

Commands:
  search [type] <query>      Search database (aliases: find, query)
  get <noun> <ref>            Get entity details (aliases: fetch, show)
  tracks <ref>                Shortcut: get tracklist <ref>
  price <ref>                 Shortcut: get price <ref>
  cache clear                 Clear HTTP cache
  skills [get <name>]         Print the bundled agent guide (start: skills get core)
  status                      Show session and auth info

Search Types:  artist, label, master, release
Get Nouns:     artist, credits, identifiers (ids), label, master, price, release,
               releases, tracklist, versions

Refs:
  Search results return typed refs: @a3857 (artist), @r847868 (release),
  @m3719 (master), @l647 (label). Use refs with get commands.

Options:
  --json           Output raw JSON (search, get)
  --limit N        Results per page (search, get)
  --page N         Page number for server-side pages (search, get)
  --after CURSOR   Continue a client-side filtered list; copy from Next page /
                   Continue scan (search with --release-type official|unofficial,
                   get releases --role)
  -v, --verbose    get release: also print notes, credits, identifiers
  --version        Show version and exit
  --help           Show this message and exit

Environment:
  DISCOGS_TOKEN    Personal access token (higher rate limit, required for price data)

Price data also needs seller settings filled out on the Discogs account the
token belongs to (discogs.com/settings/seller). Without them the API answers
404 and only 'price' is affected.

Examples:
  agent-discogs search "The Downward Spiral"
  agent-discogs search artist "Nine Inch Nails"
  agent-discogs get release @r847868
  agent-discogs get versions @m3719 --country US --format Vinyl
  agent-discogs tracks @r847868
  agent-discogs price @r847868
  agent-discogs get releases @a3857 --role Main --limit 5
"""


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

    def format_help(
        self,
        ctx: click.Context,  # noqa: ARG002  # part of click's Command API
        formatter: click.HelpFormatter,
    ) -> None:
        formatter.write(_HELP_TEXT)

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
        # Last-resort boundary: any escaped exception becomes agent-readable text
        # instead of a traceback.
        except Exception as e:  # noqa: BLE001
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
cli.add_command(skills)
cli.add_command(status)
cli.add_command(tracks)


def main() -> None:
    """Entry point for the CLI."""
    cli()
