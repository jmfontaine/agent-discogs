"""agent-discogs: Token-efficient Discogs CLI for AI agents."""

from __future__ import annotations

import sys
from typing import Any

import click

from agent_discogs import client, style, trace
from agent_discogs.commands.cache import cache
from agent_discogs.commands.completion import completion
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
  cache clear [--json]        Clear HTTP cache; a failed purge exits 1 (cache_error)
  skills [list|get|path]      Print the bundled agent guides (list takes --json;
                              start: skills get core, then collection,
                              marketplace or pressings)
  status                      Show version, auth mode, and cache location
  completion bash|zsh|fish    Print the Tab-completion script for an interactive
                              shell, e.g. eval "$(agent-discogs completion zsh)"

Search Types:  artist, label, master, release
Search Flags:  --artist, --barcode, --catno, --country, --format, --genre,
               --label, --owned, --style, --year; --release-type official
               (default; a client-side scan on release, master and untyped
               searches) | unofficial | all
Search Auth:   Discogs documents search as needing credentials (see
               Environment); unauthenticated searches currently get answers,
               but Discogs may refuse one with auth_required
Get Nouns:     artist, credits, identifiers (ids), label, list, master, owned,
               price, release, releases, tracklist, versions; inventory (a
               seller's username); collection, lists and wantlist (no ref)
               (artist/label 'URLs:' lists site domains, empty entries omitted)
Get Credits:   release, track, and sub-track credits by role; each person once
               per role with the tracks covered (none = whole release);
               --json --full adds track_credits
Get Owned:     get owned <release ref>...: your own copies of each release, as
               '@r352665 owned \u00d72: Vinyl, Uncategorized' ('owned: Vinyl'
               for one copy) or '@r847868 not owned' (--json: owned, folders;
               --json --full: instances). Needs DISCOGS_TOKEN or OAuth. 1
               request for your username, 1 per ref, and 1 more for your
               folder names when any is owned
Get Collection:
               get collection: your own collection, one row per copy, 5 a
               page (--limit, --page), as '@r847868 "Nine Inch Nails - The
               Downward Spiral" 1994 · Nothing Records 92346-2 · CD (Album) ·
               folder Vinyl · added 2024-01-04 · rating 4'. No ref. Needs
               DISCOGS_TOKEN or OAuth. --sort label|artist|title|catno|format|
               rating|added|year and --desc are sent to Discogs. --format,
               --genre, --style, --year, --label, --artist (a credited
               artist), --title (the release title) keep records where one
               value matches the pattern: the whole value, any case, * for
               any run of characters, \\ before * or \\ for a literal one
               (--artist '\\*NSYNC': single-quote it in a shell); a filter
               makes it a client-side scan (--after, --max-requests; no
               --page, no --country). --folder NAME lists only that folder
               (as rows print it, any case; All, Uncategorized; no pattern):
               not a filter, it pages server-side (--page; no --after or
               --max-requests) and, with a filter, scans only that folder.
               1 request for your username, 1 for your folder names (not
               with --json --full, except with --folder, which needs them),
               then 1 per page or per 100 records scanned; an unknown or
               ambiguous --folder fails after those 2 and lists the
               folders. Without --sort or --folder a scan reuses the pages
               --owned cached. --json: ref, title, artists, year, label,
               catno, format, genres, styles, master, folder, added, rating
               (artists, genres, styles and master are not in the text row);
               --json --full: the collection records (folder_id).
               get collection --value instead prints its value from Discogs'
               Marketplace sales in your profile's currency, as 'Collection
               value (EUR): min 75.50 · median 100.25 · max 250.00' (--json:
               currency, minimum, median, maximum; --json --full: currency
               and the value record as sent). No other flag. 3 requests:
               username, profile (currency), value
Get Wantlist:  get wantlist: your own wants, 5 a page (--limit, --page), as
               '@r847868 "Nine Inch Nails - The Downward Spiral" 1994 · Nothing
               Records 92346-2 · CD (Album)'. No ref.
               Needs DISCOGS_TOKEN or OAuth. 1 request for your username, then
               1 per page. --json: ref, title, year, label, catno, format,
               notes, rating; --json --full: the want records. --for-sale
               keeps only the wants with copies on sale, with '· for sale: 3
               from EUR 12.50' (--json: market). Condition, sleeve condition,
               seller location and shipping cannot be filtered: the API lists
               no copies, only a count and one lowest price per release; see
               the release's Marketplace page on discogs.com
Get Inventory: get inventory <username>: one seller's Marketplace listings, 5
               a page (--limit, --page), as '@r847868 "Nine Inch Nails - The
               Downward Spiral" · CD, Album · NM/VG+ · USD 12.50 · ships from
               Germany · listed 2024-09-18'. Takes one Discogs username in
               place of a ref (after -- if it starts with -). A seller's For
               Sale listings need no credentials. --sort listed|price|item|
               artist|label|catno and --desc are sent to Discogs.
               --condition and --sleeve (minimum grade), --ships-from,
               --max-price with --currency and --in-wantlist keep the
               listings that match; any of them makes it a client-side scan
               (--after, --max-requests; no --page). 1 request per page, or
               per 100 listings scanned, so always pass --max-requests with a
               filter: a seller with 50,000 listings is 500 requests.
               --in-wantlist reads your whole wantlist first (1 request for
               your username, 1 per 100 wants). --json: listing, ref, title,
               format, condition, sleeve, price, currency, ships_from,
               listed, url; --json --full: the listing records. No command
               finds every seller of one release (the API has no such
               endpoint); a listing's shipping cost is not shown
Get Lists:     get lists: your own lists, private ones included, 5 a page
               (--limit, --page), as '123 "Pressings" · private · changed
               2016-06-02 → get list 123' (the day in UTC; Discogs sends no
               item count). No ref. Needs DISCOGS_TOKEN or OAuth. 1 request
               for your username, then 1 per page. --json: id, name,
               description, public, added, changed, url
Get List:      get list <id>...: a list by its id, a plain number (a typed ref
               such as @r123 is invalid_argument): its name, its whole
               description, then one row per item, its ref and title, with
               '· comment: ...' when set; an item of a type with no ref shows
               'id N [type]'. 1 request per list, whatever its length; a
               public list needs no credentials. --json: id, name,
               description, public, url, items (ref, title, comment);
               --json --full: the list records

On a row, 'have N' is Discogs' community count (how many Discogs users own the
release); 'owned' is your own copies (--owned).

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
  and list descriptions keep their line breaks, indented under 'Notes:' or
  'Description:'. --json keeps values exact, except Discogs markup in
  profiles, notes and list descriptions, converted in text and --json
  (not --full): [l647] prints as the ref @l647, [a=Name] as Name, a [url] as
  'text (host)', [b]/[i]/[u]/[s] as their text. Color marks only the CLI's
  own framing (refs, labels, the error mark, the budget warning, dimmed counts
  and prefixes) and that markup's text attributes (never a color); by default
  a pipe gets none (--color).

Options:
  --cached         Serve only from the cache; a miss fails (cache_miss), spends
                   nothing. Precedes the command: agent-discogs --cached price @r...
  --fresh          Bypass the cache for this command (response is not stored).
                   Never opens the cache, so it works while other commands fail
                   with cache_error (an unusable cache; the hint says what to fix)
  --debug          Request rows, timings, scan stats, cache and output size on
                   stderr, plus the SDK's own log lines. Never changes stdout
                   or the exit code; an unresolvable cache location shows as
                   'unknown (no home directory)'
  --color WHEN     auto (default): color stdout and stderr each only when it
                   is a terminal, NO_COLOR is unset or empty, and TERM is not
                   dumb; always; never. Agents whose harness runs commands in
                   a pseudo-terminal: set NO_COLOR=1 or pass --color never.
                   A --json command is never colored, stderr included; nor
                   are --help and skills
  --json           JSON output: a compact projection of the text view (search,
                   get, tracks, price); cache clear prints {"cleared":true},
                   skills list one {name, description, version} per skill.
                   Non-ASCII is literal on a UTF-8 stdout, \\uXXXX escapes
                   otherwise
  --full           With --json: the complete record instead of the projection,
                   normalized: consistent field names, RFC 3339 timestamps (null when
                   absent or unparseable), unmodeled fields as Discogs sends them
  --limit N        Results per page, 1-100 (search, get)
  --page N         Page number, from 1, for server-side pages (search, get)
  --after CURSOR   Continue a client-side filtered list; copy from Next page /
                   Continue scan (search with --release-type official, or
                   unofficial with --format; get releases --role, get releases
                   @l --year, get versions --release-type official, or
                   unofficial with --format; get collection with a filter; get
                   wantlist --for-sale; get inventory with a filter)
  --max-requests N Cap the command's HTTP sends (header lookups included; cache
                   hits are free) for a client-side scan, --added-since, the
                   --owned collection read or get wantlist --for-sale: search
                   or get versions with --release-type official, or unofficial
                   with --format; get releases --role / @l --year; get
                   collection with a filter, not --folder alone (its username
                   and folder list count first); get releases, get versions and
                   get inventory with --owned; get wantlist --for-sale; get
                   inventory with a filter (--in-wantlist's wantlist read
                   counts too; about 1 request per 100 listings, so a
                   50,000-listing seller is 500: always pass it); one ref only.
                   Default: no cap, the
                   scan runs until the page fills or the list ends (--for-sale:
                   about 1 request per want, 500 wants = ~9 minutes of the
                   60/min budget, so always pass it; 3 or more there: the
                   username, a wants page and one want's stats; a page is read
                   only while 2 sends remain). Pass it if your tool calls have
                   a time limit. Text get releases and get versions scans under
                   --fresh resend their header every run: 2 or more there.
                   Under --owned the cap counts every send but stops only
                   scans and the collection read: the text header and a
                   server-side listing page are always sent, then the
                   username and collection pages while the cap allows
  --release-type T get versions: all (the default, unlike search's official)
                   lists every version; unofficial keeps those whose format
                   lists "Unofficial Release", filtered by Discogs (exact
                   total, --page); official drops them, a client-side scan
                   (≤N total, --after, --max-requests). "Partially Unofficial"
                   counts as official. With --format, unofficial is a scan too
  --added-since D  search release only (experimental): releases added to Discogs
                   on or after D (YYYY-MM-DD, from midnight UTC), newest first,
                   each with its added day in UTC (--json: added; complete:
                   false when --max-requests stopped it). Walks Discogs'
                   undocumented date_added sort and looks up every release the
                   search matches: one request per release in the window, plus
                   one per 100 results and one to end the scan; a repeat within
                   1h is free. A 429 partway fails it (a rerun reuses the cached
                   lookups). Whole window unless you pass --limit; not with
                   --sort, --desc, --page, --after. --release-type filters on the
                   looked-up formats (unofficial asks Discogs, unless --format)
  --owned          Mark each release row with the copies in your own
                   collection: '· owned' (one copy), '· owned \u00d72' (two or
                   more), '· not owned', or '· owned ?' (not in the part
                   read). search, get releases @a|@l, get versions and get
                   inventory (a listing with no release id is 'owned ?');
                   release rows only (a master, artist or label row
                   has no mark), so not search artist|label|master, and not
                   with --added-since. Needs DISCOGS_TOKEN or OAuth (see
                   auth_required below). Reads your whole collection once per
                   command: 1 request for your username, then 1 per 100
                   records, free for an hour. --max-requests stops the
                   collection read (see there); when it or a 429 stops the
                   read, a note says how much was read and the rows it did not
                   reach are 'owned ?' (--json: owned null, plus a collection
                   object). A 429 on the username or the first page, or after
                   the listing's own scan stopped on one, reads nothing: every
                   release row is 'owned ?', exit 0. A record added in the
                   last hour can show as not owned: --fresh re-reads the
                   collection. --cached fails (cache_miss) on any page not
                   cached. 'Next page:' repeats it
  --for-sale       get wantlist: keep the wants with copies for sale now. A
                   client-side scan of 100-want pages, 1 request per want (its
                   Marketplace stats, cached 1h; --fresh when current numbers
                   matter). A want is kept with at least 1 copy for sale and
                   not blocked from sale; --limit counts kept wants (and
                   --cached misses). Not with --page. A 429 on a stats request
                   keeps the rows matched so far (Continue scan: resumes at
                   that want). --max-requests
                   is checked before each want and Continue scan: --after
                   never re-checks one. --cached: a want whose stats are not
                   cached is a row with its cache_miss error (JSON error),
                   exit 1
  --max-price N    get wantlist --for-sale: keep wants whose lowest price is at
                   most N (exactly N is kept; no price is dropped). Needs
                   --currency. get inventory: keep listings priced in
                   --currency at most N (exactly N is kept; no price is
                   dropped); a listing in another currency is dropped and
                   counted (header 'N listings in other currencies skipped';
                   JSON skipped_currency). Needs --currency
  --currency CODE  get wantlist --for-sale: prices in USD, GBP, EUR, CAD, AUD,
                   JPY, CHF, MXN, BRL, NZD, SEK or ZAR (any case; another code
                   is invalid_argument); without it Discogs uses the account's
                   currency (sent as curr_abbr). get inventory: only with
                   --max-price; matched client-side against each listing's
                   price currency, never sent (sellers price in their own
                   currency, so pick theirs)
  --condition GRADE, --sleeve GRADE
                   get inventory: keep listings whose media (--condition) or
                   sleeve grade is GRADE or better. Order: M, NM, VG+, VG, G+,
                   G, F, P, or the full name such as 'Very Good Plus (VG+)'
                   (any case; another value is invalid_argument). A Generic,
                   Not Graded or No Cover sleeve fails any --sleeve
  --ships-from COUNTRY
                   get inventory: keep listings shipping from COUNTRY, as
                   Discogs names it (exact, any case: germany, not Germ)
  --in-wantlist    get inventory: keep listings whose release is on your
                   wantlist. Needs DISCOGS_TOKEN or OAuth. Reads your whole
                   wantlist first (1 request for your username, 1 per 100
                   wants, all counted by --max-requests). When that read stops
                   short (--max-requests or a 429) no listing is scanned: an
                   empty page with Continue scan:, exit 0 (JSON: wantlist
                   complete false, wants_read, total_wants)
  -v, --verbose    get release: also print notes, credits, identifiers
  -c, --compact    get release: one-line "Tracks: N (mm:ss)" instead of the tracklist
                   (a suite counts as its parts, timed by their sum or else its
                   own duration; total omitted if any track lacks a duration)
  --sort KEY       Server-side order. get releases @a: year|title|format;
                   get versions: released|title|format|label|catno|country;
                   get collection: label|artist|title|catno|format|rating|
                   added|year; get inventory: listed|price|item|artist|label|
                   catno;
                   search release: date_added (undocumented by Discogs and only
                   seen on release searches; if Discogs drops it, results most
                   likely come back in relevance order with no error)
  --desc           With --sort: descending
  --year YEAR      search: filter by release year (server-side); get versions:
                   filter by year (server-side); get releases @l and get
                   collection: keep that year (client-side scan, continues via
                   --after)
  --format, --label, --genre, --style, --artist, --title
                   get collection: keep records matching the pattern: format
                   name or description, label name, genre, style, credited
                   artist, release title; * is any run of characters
  --folder NAME    get collection: list only that folder, by name as rows print
                   it (any case; not a pattern); pages server-side on its own
  --value          get collection: its value (min, median, max) in your
                   currency instead of the listing; takes no other flag
  --version        Show version and exit
  --help           Show this message and exit

Get flags by noun (any other get flag fails with invalid_argument):
  release          -v/--verbose, -c/--compact
  releases @a      --limit, --page, --role, --sort, --desc, --owned, --after
                   and --max-requests (with --role or --owned)
  releases @l      --limit, --page, --year, --owned, --after and --max-requests
                   (with --year or --owned)
  versions         --limit, --page, --country, --format, --label, --year,
                   --sort, --desc, --release-type, --owned, --after (with a
                   --release-type scan) and --max-requests (with a scan or
                   --owned)
  collection       no ref; --limit, --page, --sort, --desc, --format, --genre,
                   --style, --year, --label, --artist, --title, --folder,
                   --after and --max-requests (with a filter, not --folder
                   alone); or --value alone
  lists            no ref; --limit, --page
  wantlist         no ref; --limit, --page, --for-sale, --max-price and
                   --currency (both with --for-sale), --after and
                   --max-requests (with --for-sale, 3 or more)
  inventory        one username; --limit, --page, --sort, --desc, --owned,
                   --condition, --sleeve, --ships-from, --in-wantlist,
                   --max-price and --currency (each needs the other), --after
                   (with a filter) and --max-requests (with a filter or
                   --owned)
  any other noun   none beyond --json and --full (list and owned included)
  --after and --max-requests continue or cap one listing, so they take exactly
  one ref.

Environment:
  DISCOGS_TOKEN        Personal access token (60 req/min; price data needs it
                       or OAuth)
  DISCOGS_CONSUMER_KEY, DISCOGS_CONSUMER_SECRET
                       Used when DISCOGS_TOKEN is unset: an app key/secret (60
                       req/min, not a user, no price data), or OAuth 1.0a with
                       DISCOGS_ACCESS_TOKEN and DISCOGS_ACCESS_TOKEN_SECRET.
                       'status' shows the mode in use
  AGENT_DISCOGS_DEBUG  Same as --debug
  NO_COLOR             Non-empty: no color under --color auto (no-color.org)
  XDG_CACHE_HOME       Cache under $XDG_CACHE_HOME/agent-discogs when set to an
                       absolute path; otherwise ~/.cache/agent-discogs
  PYTHONIOENCODING     Non-UTF-8 stdout (e.g. ascii) prints characters it
                       cannot hold as backslash escapes (\\u5b87)

Paging:
  Lists end with a 'Next page:' or 'Continue scan:' command. Paste it unchanged:
  it repeats --cached or --fresh, --max-requests, --owned, for get collection
  and get inventory --limit, its filters (get collection also --folder, not a
  filter), --sort and --desc (inventory also --in-wantlist), and, for get
  lists and get wantlist, --limit (and
  --for-sale, --max-price and --currency), and puts a search's type and query
  after --, so a query such as -ambient survives (a seller's name starting
  with - too).
  Filtered lists scan Discogs' 100-row pages until --limit rows match or the
  list ends. 'Continue scan:' means the scan stopped early: --max-requests ran
  out (the header says so; before the scan started, it shows no counts and JSON
  totals are null), or Discogs answered 429 partway, in which case the rows read
  so far are kept (JSON: capped, stop_reason "max_requests" or "rate_limited",
  retry_after). A 429 on the first page is an error.
  In a filtered list, page counts pages of --limit matches, but JSON
  total_items is the unfiltered total (the ≤N in the header) and total_pages
  counts Discogs' 100-row pages, so neither tells how many pages of matches
  remain. Continue until no command is printed (JSON: next_cursor null).

Budget:
  After any command that talks to the API, stderr ends with a budget line, e.g.
  'api: 3 requests · 43/60 left this minute'. ⚠ means 10 or fewer requests
  remain in the moving 60s window: pause ~60s or stick to cached refs. Cache
  hits cost nothing ('api: 0 requests · cached'). Never inside --json output.
  A 429 names the wait Discogs asked for, rounded up: 'Retry in 2s.' (--json:
  retry_after; 0 = retry now; omitted without a usable Retry-After). With
  credentials set, its hint states the limit Discogs reported for them (60/min
  when it sent none); without any, it suggests DISCOGS_TOKEN for 60/min.

Errors:
  With --json an error is {"error":{"code",...}} on stdout, exit 1; a malformed
  command line instead exits 2 with text on stderr.
  bad_response means Discogs sent something unreadable (not JSON, or the wrong
  shape). It is not your input: retry in a moment, with --fresh if it repeats.
  When its hint says the format is unknown (a collection value amount or the
  profile's currency code), retrying will not help: report it.
  auth_required names the credentials that were sent (DISCOGS_TOKEN, the
  consumer key/secret, or OAuth), or says none are set, as 'status' reports.
  --owned, --in-wantlist, get owned, get collection, get lists and get
  wantlist raise it before any request when 'status' shows consumer or none:
  they read your own account, which an app key does not identify; the hint
  says to set a token or use OAuth. A 403 stays forbidden. get list needs no
  user: a public list reads without credentials, and someone else's private
  list fails with the error its status maps to (not_found, auth_required or
  forbidden).
  get inventory needs none either: a seller's For Sale listings read without
  credentials (only --in-wantlist and --owned need a user).

Price data also needs seller settings filled out on the Discogs account the
token or OAuth credentials belong to (discogs.com/settings/seller). Without
them the API answers 404 and only 'price' is affected.

get artist groups members as Members: and Former: (JSON members/former).
Only members Discogs marks inactive are Former; a member with no recorded
status stays under Members.

Prices carry the ISO code Discogs returns ('EUR 20.00'), never a symbol. The
'Market: 9 for sale from 9.36' line of get release/master is a bare amount in
the account's buyer currency (USD without a token); 'price' names the currency.

get release prints 'Added: YYYY-MM-DD', the day the release was added to
Discogs (--json: 'added', an RFC 3339 timestamp), at no extra request.

--json gives each release, master, artist, label, search result, list and
get lists row a 'url': its Discogs web page, absolute, to hand to a person.
Text output omits it. versions/releases rows carry it only if Discogs sends
one (not today); list items, tracks, price, credits, and identifiers never do.

Piping into a reader that closes early (| head -1) exits 1 with no message.

A connection error names its cause ('Connection error: <cause>'). Its hint
says to retry only for a transient failure (refused or dropped connection,
timeout); for a proxy or protocol failure, fix the configuration instead.

Examples:
  agent-discogs search "The Downward Spiral"
  agent-discogs search artist "Nine Inch Nails"
  agent-discogs get release @r847868
  agent-discogs get versions @m3719 --country US --format Vinyl
  agent-discogs get versions @m3719 --release-type official --json
  agent-discogs tracks @r847868
  agent-discogs price @r847868
  agent-discogs get releases @a3857 --role Main --limit 5
  agent-discogs search release --artist Wire --added-since 2026-07-08
  agent-discogs search release --artist Wire --limit 20 --owned
  agent-discogs get owned @r847868 @r352665
  agent-discogs get collection --style Techno --format 12\\" --max-requests 20
  agent-discogs get collection --value
  agent-discogs get wantlist --for-sale --max-price 20 --currency EUR --max-requests 60
  agent-discogs get inventory <seller> --in-wantlist --condition VG+ --max-requests 60
  agent-discogs get lists
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
            print(format_error(e, color=style.enabled(sys.stderr)), file=sys.stderr)
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
@click.option(
    "--color",
    type=click.Choice(style.MODES),
    default="auto",
    help="Color text for a person at a terminal: auto, always, never.",
)
@click.pass_context
def cli(
    ctx: click.Context, debug: bool, cached: bool, fresh: bool, color: style.Mode
) -> None:
    """Token-efficient Discogs CLI for AI agents."""
    style.configure(ctx, color)
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
cli.add_command(completion)
cli.add_command(get)
cli.add_command(price)
cli.add_command(search)
cli.add_command(skills)
cli.add_command(status)
cli.add_command(tracks)


def main() -> None:
    """Entry point for the CLI."""
    cli()
