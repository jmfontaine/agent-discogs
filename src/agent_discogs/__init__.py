"""agent-discogs: Token-efficient Discogs CLI for AI agents."""

from __future__ import annotations

import sys
from typing import Any

import click

from agent_discogs import client, trace
from agent_discogs.commands.cache import cache
from agent_discogs.commands.get import get, price, tracks
from agent_discogs.commands.search import search
from agent_discogs.commands.skills import skills
from agent_discogs.commands.status import status
from agent_discogs.errors import format_error

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
  get <noun> <ref>...         Get entity details, 1-10 refs (aliases: fetch, show)
  tracks <ref>...             Shortcut: get tracklist <ref>...
  price <ref>...              Shortcut: get price <ref>...
  cache clear                 Clear HTTP cache
  skills [get <name>]         Print the bundled agent guide (start: skills get core)
  status                      Show version, auth mode, and cache location

Search Types:  artist, label, master, release
Get Nouns:     artist, credits, identifiers (ids), label, master, price, release,
               releases, tracklist, versions
               (artist/label 'URLs:' lists site domains, empty entries omitted)
Get Credits:   release, track, and sub-track credits by role; each person once
               per role with the tracks covered (none = whole release);
               --json --full adds track_credits

get release's Format line (and --json formats) lists one entry per physical
format, ';'-separated: the quantity when above 1, the name, then descriptions
and free text in parentheses, e.g. 2\u00d7Vinyl (LP, Album, 180g, Gatefold); CD (Album).

Refs:
  Search results return typed refs: @a3857 (artist), @r847868 (release),
  @m3719 (master), @l647 (label). Use refs with get commands. A result of
  any other type (or none) shows 'id N [type]' and has no ref.

Unknown dates are left out: Discogs' year 0 is omitted, and a release date
drops a zero month or day (1994-05-00 prints as 1994-05, 1994-00-00 as 1994).

Text output:
  Discogs text never prints raw: line breaks and tabs in a field become spaces,
  other control and bidi characters print as escapes (\\x1b, \\u202e). Notes
  keep their line breaks, indented under 'Notes:'. --json keeps values exact.

Options:
  --cached         Serve only from the cache; a miss fails (cache_miss), spends
                   nothing. Precedes the command: agent-discogs --cached price @r...
  --fresh          Bypass the cache for this command (response is not stored)
  --debug          Request rows, timings, scan stats, cache and output size on
                   stderr, plus the SDK's own log lines. Never changes stdout
                   or the exit code; an unresolvable cache location shows as
                   'unknown (no home directory)'
  --json           JSON output: a compact projection of the text view (search, get);
                   non-ASCII is literal on a UTF-8 stdout, \\uXXXX escapes otherwise
  --full           With --json: the complete record instead of the projection,
                   normalized: consistent field names, RFC 3339 timestamps (null when
                   absent or unparseable), unmodeled fields as Discogs sends them
  --limit N        Results per page, 1-100 (search, get)
  --page N         Page number, from 1, for server-side pages (search, get)
  --after CURSOR   Continue a client-side filtered list; copy from Next page /
                   Continue scan (search with --release-type official, or
                   unofficial with --format; get releases --role, get releases
                   @l --year)
  -v, --verbose    get release: also print notes, credits, identifiers
  -c, --compact    get release: one-line "Tracks: N (mm:ss)" instead of the tracklist
                   (a suite counts as its parts, timed by their sum or else its
                   own duration; total omitted if any track lacks a duration)
  --sort KEY       Server-side order. get releases @a: year|title|format;
                   get versions: released|title|format|label|catno|country;
                   search release: date_added (undocumented by Discogs and only
                   seen on release searches; if Discogs drops it, results most
                   likely come back in relevance order with no error)
  --desc           With --sort: descending
  --year YEAR      get versions: filter by year (server-side); get releases @l:
                   filter by year (client-side scan, continues via --after)
  --version        Show version and exit
  --help           Show this message and exit

Get flags by noun (any other get flag fails with invalid_argument):
  release          -v/--verbose, -c/--compact
  releases @a      --limit, --page, --role, --sort, --desc, --after (with --role)
  releases @l      --limit, --page, --year, --after (with --year)
  versions         --limit, --page, --country, --format, --label, --year,
                   --sort, --desc
  any other noun   none beyond --json and --full
  --after continues one listing, so it takes exactly one ref.

Environment:
  DISCOGS_TOKEN        Personal access token (60 req/min, required for price data)
  DISCOGS_CONSUMER_KEY, DISCOGS_CONSUMER_SECRET
                       Used when DISCOGS_TOKEN is unset: an app key/secret (60
                       req/min, not a user, no price data), or OAuth 1.0a with
                       DISCOGS_ACCESS_TOKEN and DISCOGS_ACCESS_TOKEN_SECRET.
                       'status' shows the mode in use
  AGENT_DISCOGS_DEBUG  Same as --debug
  XDG_CACHE_HOME       Cache under $XDG_CACHE_HOME/agent-discogs when set to an
                       absolute path; otherwise ~/.cache/agent-discogs
  PYTHONIOENCODING     Non-UTF-8 stdout (e.g. ascii) prints characters it
                       cannot hold as backslash escapes (\\u5b87)

Paging:
  Lists end with a 'Next page:' or 'Continue scan:' command. Paste it unchanged:
  it repeats --cached or --fresh and puts a search's type and query after --,
  so a query such as -ambient survives.

Budget:
  After any command that talks to the API, stderr ends with a budget line, e.g.
  'api: 3 requests · 43/60 left this minute'. ⚠ means 10 or fewer requests
  remain in the moving 60s window: pause ~60s or stick to cached refs. Cache
  hits cost nothing ('api: 0 requests · cached'). Never inside --json output.

Price data also needs seller settings filled out on the Discogs account the
token belongs to (discogs.com/settings/seller). Without them the API answers
404 and only 'price' is affected.

get artist groups members as Members: and Former: (JSON members/former).
Only members Discogs marks inactive are Former; a member with no recorded
status stays under Members.

Prices carry the ISO code Discogs returns ('EUR 20.00'), never a symbol. The
'Market: 9 for sale from 9.36' line of get release/master is a bare amount in
the account's buyer currency (USD without a token); 'price' names the currency.

get release prints 'Added: YYYY-MM-DD', the day the release was added to
Discogs (--json: 'added', an RFC 3339 timestamp), at no extra request.

Piping into a reader that closes early (| head -1) exits 1 with no message.

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
        # Report the canonical name, not the alias typed, so help is consistent.
        _, cmd, rest = super().resolve_command(ctx, args)
        return (cmd.name if cmd else None), cmd, rest

    def format_help(
        self,
        ctx: click.Context,  # noqa: ARG002  # part of click's Command API
        formatter: click.HelpFormatter,
    ) -> None:
        formatter.write(_HELP_TEXT)

    def main(self, *args: Any, **kwargs: Any) -> Any:
        # Text must print whatever the streams' encoding (PYTHONIOENCODING,
        # a non-UTF-8 Windows pipe): a character the encoding cannot hold is
        # written as a backslash escape (\u5b87) instead of failing the block.
        # stdout defaults to errors="strict"; stderr already escapes.
        for stream in (sys.stdout, sys.stderr):
            reconfigure = getattr(stream, "reconfigure", None)
            if reconfigure is not None:
                reconfigure(errors="backslashreplace")
        return super().main(*args, **kwargs)

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
        # The reader closed stdout (`| head -1`). click's EPIPE handling exits 1
        # without a message; reporting it would blame the command.
        except BrokenPipeError:
            raise
        # Last-resort boundary: any escaped exception becomes agent-readable text
        # instead of a traceback.
        except Exception as e:  # noqa: BLE001
            print(format_error(e), file=sys.stderr)
            sys.exit(1)


@click.group(cls=AliasGroup, invoke_without_command=True)
@click.version_option(package_name="agent-discogs", prog_name="agent-discogs")
@click.option(
    "--debug",
    is_flag=True,
    envvar="AGENT_DISCOGS_DEBUG",
    help="Print request trace and timings to stderr.",
)
@click.option("--cached", is_flag=True, help="Serve only from cache; fail on a miss.")
@click.option("--fresh", is_flag=True, help="Bypass the cache for this command.")
@click.pass_context
def cli(ctx: click.Context, debug: bool, cached: bool, fresh: bool) -> None:
    """Token-efficient Discogs CLI for AI agents."""
    if cached and fresh:
        msg = "--cached and --fresh are mutually exclusive."
        raise click.UsageError(msg)
    trace.begin(ctx, debug=debug)
    if cached:
        client.request_cache_scope(ctx, "cache_only")
    elif fresh:
        client.request_cache_scope(ctx, "no_cache")
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
