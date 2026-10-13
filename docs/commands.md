# Commands

Each command, its own flags and its text output. The global flags (`--cached`, `--fresh`, `--debug`, `--color`), the output modes (`--json`, `--full`), the `--max-requests` cap and errors are described on the pages that cover them:

- `--json` and `--full`: [JSON output](json-output.md) and its [--full](json-output.md#--full) section;
- `--cached` and `--fresh`: [--cached and --fresh](caching-and-budget.md#--cached-and---fresh);
- `--max-requests`: [Capping requests with --max-requests](caching-and-budget.md#capping-requests-with---max-requests), next to what each command costs;
- `--debug`: [The --debug panel](debugging.md#the---debug-panel);
- `--color`: [Color](debugging.md#color);
- error messages: [Errors](errors.md).

## search

Search the Discogs database. Aliases: `find`, `query`.

```bash
agent-discogs search "The Downward Spiral"
agent-discogs search release "The Downward Spiral" --year 1994
agent-discogs search artist "Nine Inch Nails" --limit 10
agent-discogs search label "Nothing Records"
agent-discogs search release "The Downward Spiral" --release-type all
agent-discogs search release "When The Whip Comes Down" --release-type unofficial
agent-discogs search release --artist "Nine Inch Nails" --sort date_added --desc
agent-discogs search release --artist "Nine Inch Nails" --release-type all --added-since 2026-07-08
agent-discogs search release --artist "Nine Inch Nails" --year 1994 --release-type unofficial --limit 100 --owned
```

### Types and paging

Prefix a type (`release`, `master`, `artist`, `label`) to narrow results. Use `--limit` (1-100, default: 5) for page size.

Discogs occasionally returns a result whose type is not one of those four, or has none. Such a row has no ref, because nothing says what its ID refers to: it reads `id 9001 [playlist] "..."` in text (`[no type]` when Discogs sends none) and carries `id` instead of `ref` under `--json`. The rest of the page prints as usual.

To continue, paste the `Next page:` command printed under the results: the default `--release-type official` filter is applied client-side, so continuation is a cursor (`--after`), not a page number, and the total is an upper bound (`≤N`).

`--release-type unofficial` is filtered by Discogs itself (`format=Unofficial Release`), so it shows an exact total and pages with `--page`; combined with `--format` it falls back to the client-side scan, because Discogs cannot reliably combine two format filters. `--page` (from 1) works with those server-side pages, `--release-type all`, and artist/label searches.

Discogs' "Unofficial Release" covers bootlegs, counterfeits and pirate copies alike, and no field tells them apart, so `--release-type unofficial` returns all three. To keep bootlegs only, check each row's master: the bundled guide (`agent-discogs skills get core --full`, section "Unofficial Releases: Bootlegs, Counterfeits, Pirates") describes how.

A search needs a query or at least one non-blank filter; an out-of-range `--limit`/`--page` or an empty search fails with `invalid_argument` before any request.

A query starting with `-` goes after `--` (`agent-discogs search --release-type all -- -ambient`); the printed `Next page:` command always puts the type and query there, so it pastes back unchanged.

### Filters

Filters: `--artist`, `--barcode`, `--catno`, `--country`, `--format`, `--genre`, `--label`, `--release-type {official,unofficial,all}` (default: official), `--style`, `--year`. Use `--json` for JSON output (see [JSON output](json-output.md)).

### Sorting

`search release ... --sort date_added` orders results by when they were added to Discogs; add `--desc` for newest first.

The parameter is undocumented: Discogs lists no sort keys for search, and it has only been seen working on release searches, so `--sort` on any other search (or `--desc` without `--sort`) fails with `invalid_argument` before any request. If Discogs ever drops it, results will most likely come back in relevance order with no error.

With the default `--release-type official` filter, the client-side scan keeps the server's order, and the printed `Next page:` command keeps `--sort` and `--desc`.

### Client-side scans

Client-side scans (`--release-type official`, or `unofficial` with `--format`; `get releases --role`; `get releases @l... --year`) read Discogs' largest pages, 100 rows each, until `--limit` rows match or the list ends. There is no cap by default, so a sparse filter over a long list can take many requests.

To bound one, pass `--max-requests N` (see [Capping requests with --max-requests](caching-and-budget.md#capping-requests-with---max-requests)).

When Discogs answers 429 partway through a scan, the rows read so far are returned with a `Continue scan:` command and a `scan stopped: rate limited` note (`stop_reason: "rate_limited"` and `retry_after` in JSON), exit 0; a 429 on the first page is an error.

### Releases added since a date

`search release ... --added-since YYYY-MM-DD` lists the releases added to Discogs on or after that day (inclusive, from midnight UTC), newest first, each row ending in its added day in UTC (`· added 2026-09-01`; `added`, a full RFC 3339 timestamp, under `--json`).

It is **experimental**: Discogs has no date filter and search results carry no added date, so it walks the undocumented `date_added` search order and looks up each release to read its date (the SDK's `releases_added_since()`), stopping at the first release older than the cutoff.

It is complete only while Discogs keeps that order; when a looked-up date breaks it, the command fails with `bad_response` rather than return a doubtful list (the responses are cached, so a plain retry repeats the error for up to an hour; `--fresh` asks Discogs again).

- It costs one request per release the search matches in the window, before `--release-type` filtering, plus one per 100 search results and one for the release that ends the scan; a repeat within the hour is free.
- There is no cap by default: `--max-requests N` stops it after N sends, and the result then ends with `Stopped at --max-requests N: the result may be incomplete` (JSON: `complete: false`; `total` counts the rows found). Pass `--max-requests` if your tool calls have a time limit.
- The CLI never waits out the rate limit: a 429 partway through fails the command with `rate_limited`, and a rerun after `retry_after` resends none of the lookups already made.
- The whole window comes back unless you pass `--limit`, which cuts the rows, not the requests.
- `--release-type official` (the default) and `unofficial` with `--format` filter on the looked-up formats at no extra request; `unofficial` alone is filtered by Discogs (`format=Unofficial Release`), which saves the lookups of official releases.
- It needs an explicit `search release ...` and rejects `--sort`, `--desc`, `--page` and `--after` (`invalid_argument`, before any request).
- Under `--cached`, any lookup the cache does not hold fails the command with `cache_miss`.

### Marking rows with --owned

`--owned` marks every release row with the copies in your own Discogs collection, on `search`, `get versions` and `get releases` (artist and label). A row ends in `· owned ×2` (always with the count, also for one copy), `· not owned`, or `· owned ?` when the read did not reach it (below). In `--json`, each row gets `owned`, and the document gets a `collection` object (see [JSON output](json-output.md#owned-rows)).

It is not the `have 1,234` on a row, which is Discogs' community count: how many Discogs users own that release. `owned` is the authenticated user's own copies.

```bash
agent-discogs search release --artist "Nine Inch Nails" --year 1994 --release-type unofficial --limit 100 --owned
agent-discogs get versions @m3719 --country US --owned
agent-discogs get releases @a3857 --role Main --owned --max-requests 6
```

- **Release rows only.** A collection holds releases and Discogs does not document a master id on its records, so master, artist and label rows (an untyped search, or an artist's discography, lists some) get no mark. `search artist|label|master ... --owned` and `--owned` with `--added-since` fail with `invalid_argument` before any request, as does `--owned` on any `get` noun but `releases` and `versions`. A page with no release row reads nothing.
- **It needs a user.** A token or OAuth credentials name one; a consumer key/secret or no credentials do not, and the command fails with `auth_required` before any request (see [Errors](errors.md#--owned-and-get-owned-need-a-user)). It reads only the authenticated user's own collection: there is no `--user` flag.
- **Cost.** After the listing page, one request for the username, then your whole collection (the "All" folder) at 100 records per request, read once per command even with several refs (`get versions @m... @m... --owned`). Every response is cached for an hour, so a rerun costs nothing. See [What commands cost](caching-and-budget.md#what-commands-cost).
- **`--max-requests N`** counts every send of the command (the text header lookup, the listing page, the username and the collection pages; cache hits are free), but it only stops a scan and the collection read. The header and a server-side listing page are always sent, header first; the username and the collection pages are then asked only while the cap allows. So `get versions @m3719 --owned --max-requests 1` still sends the header and the listing (2 sends) and reads no collection, and a cold server-side `search release ... --release-type all --owned --max-requests 3` is the listing, the username and one collection page. It is accepted on `search` (otherwise rejected on a server-side search), on `get versions`, and on `get releases`, with one ref only.
- **A read that stops early** (the cap, or a 429) never claims a release is not owned: a row found in the part read shows the count found so far (it may be short), and any other row reads `· owned ?` (JSON `owned: null`). A note before the footer says why: `Owned: the collection read stopped at --max-requests 2 (100 of 250 records read): "owned ?" rows are not in the part read, and a count may be short.` or `... the collection read was rate limited, retry in 30s (100 of 250 records read): ...`. Rerun with a higher `--max-requests`, or after the wait. A 429 on a later collection page keeps the records read. A 429 on the username or the first collection page, or a listing scan that already stopped on one (`pagination.stop_reason: "rate_limited"`; the collection is then not read at all, with no request), leaves every release row `owned ?`; the command still succeeds, and JSON `collection` is `{"complete":false,"records_read":0,"total_records":null,"stop_reason":"rate_limited","retry_after":N}` (the listing's `retry_after` in the second case).
- **Paging.** The printed `Next page:` / `Continue scan:` command repeats `--owned`, so the next page is marked too; within the hour the collection comes from the cache.
- **Freshness.** A record added to your collection within the last hour may still show as not owned; `--fresh` re-reads the collection. Under `--cached`, a collection page the cache does not hold fails the command with `cache_miss`.

## get

Get entity details by noun and ref. Aliases: `fetch`, `show`.

```bash
agent-discogs get release @r352665
agent-discogs get artist @a3857
agent-discogs get master @m3719
agent-discogs get label @l647
agent-discogs get tracklist @r352665
agent-discogs get releases @a3857 --limit 20
agent-discogs get versions @m3719 --country US --format Vinyl
agent-discogs get price @r352665
agent-discogs get owned @r352665 @r847868
agent-discogs get versions @m3719 --country US --owned
```

Nouns: `artist`, `credits`, `identifiers` (alias `ids`), `label`, `master`, `owned`, `price`, `release`, `releases`, `tracklist`, `versions`. Use `--json` for JSON output.

### release

`release` output carries inline refs for its artists and label (`[@a...]`, `[@l...]`), plus country, release date, and the day the release was added to Discogs (`Country: US · Released: 1994-03-08 · Added: 2007-02-23`; the date is the one in the timestamp's own offset, and the field is omitted when Discogs has none).

Under `--json` that date is `added`, a full RFC 3339 timestamp such as `2007-02-23T01:13:39-08:00`. It answers "is this a new entry or an old one?" at no extra request.

Unknown date parts are left out, in text and `--json` alike: Discogs' year `0` is omitted, and a release date drops a zero month or day (`1994-05-00` shows as `1994-05`, `1994-00-00` as `1994`, and the text view prints no `Released:` when that equals the year).

Its format lists one entry per physical format, separated by `;`: the quantity when above one, the name, then descriptions and free text in parentheses (`Format: 2×Vinyl (LP, Album, 180g, Gatefold); CD (Album)`; `--json` carries the same string).

`-v, --verbose` on `release` appends notes, credits, and identifiers in one call; `-c, --compact` replaces the tracklist with a one-line `Tracks: 14 (65:01)` summary; the total is omitted when any track lacks a duration (use `tracks` when the tracklist is what you want).

Index tracks (a suite or medley split into parts) list their sub-tracks indented beneath them. The compact count includes each part, and the total sums the parts' durations; when a part is untimed, the index track's own duration stands in for the suite.

### Profiles and notes

`artist` and `label` print a profile, cut to about 300 characters, and `release -v` prints the release notes. Discogs writes links and formatting into these fields as markup, which its website renders; the CLI converts it in the text view and the `--json` projection alike, so a link to another entity arrives as a ref you can pass straight back to `get`:

| Discogs markup | Printed |
|---|---|
| `[a3857]`, `[l647]`, `[r352665]`, `[m3719]`, and the title-only `[r=352665]`, `[m=3719]` | the ref: `@a3857`, `@l647`, `@r352665`, `@m3719` |
| `[a=Name]`, `[l=Name]` | `Name` (not resolved to a ref: that would take a search per name, which may not match exactly) |
| `[url=https://host/path]text[/url]` | `text (host)`, without a leading `www.` |
| `[url]https://host/path[/url]` | `host` |
| `[g1.2.3]` (a database guideline), `[t=262]` (a forum topic) | the text Discogs shows: `RSG §1.2.3`, `Forum Topic #262` |
| `[b]`, `[i]`, `[u]`, `[s]` and their closing tags | the text alone; with color on, bold, italic, underlined or struck through |
| anything else in brackets, uppercase tags included (`[A3857]`, `[Booklet]`) | unchanged |

An opening tag Discogs never closes ends with the field, and a closing tag with nothing to close is dropped. The cut counts the converted text and never splits a ref. `--json --full` keeps both fields exactly as Discogs sent them, markup included.

### credits and identifiers

`credits` lists who did what on a release, grouped by role, each person with an artist ref. It includes credits Discogs files on individual tracks and sub-tracks: each person appears once per role with the tracks the credit covers, and no track list means the whole release.

`identifiers` lists barcodes, matrix/runout etchings, and other codes: the data that tells two pressings with the same catalog number apart.

### owned

`get owned @r... [@r...]` tells whether the authenticated user owns each release, and in which folders:

```
@r352665 owned ×2: Vinyl, Uncategorized
@r847868 not owned
```

It asks about one release at a time (`GET /users/{username}/collection/releases/{id}`), so it is the cheap way to check a few refs; for a page of rows, use `--owned` instead. Several refs work as with the other nouns: blocks separated by a blank line, a failing ref as its `✗` block, exit 1 if any failed.

- **Cost:** one request for the username (once per command), one per ref, and one for the folder list when any ref is owned. A copy in a folder Discogs does not name adds no folder name: with no names at all the text is `@r352665 owned ×1` (no colon) and JSON has no `folders`.
- **`--json`:** `{"ref":"@r352665","owned":2,"folders":["Vinyl","Uncategorized"]}`, or `{"ref":"@r847868","owned":0}` when not owned; several refs give a list, with `{"ref","error"}` items for failures. `--json --full` is `{"instances":[...]}`, the collection records as the SDK normalizes them (each names its folder by id), and sends no folder-list request.
- **Needs a token or OAuth**, like `--owned` (`auth_required` before any request otherwise). Only `--json` and `--full` apply.

### releases and versions

Paginated nouns (`releases`, `versions`) support `--limit` (1-100, default: 5) and `--page` (from 1); `releases --role` filters client-side and continues via the printed `--after` cursor instead.

`releases` takes an artist ref (discography, with `--role` and `--sort year|title|format`) or a label ref (catalogue; the API offers no filters or sorting there, so `--year` on a label is a client-side scan that continues via the printed `--after` cursor).

`versions` accepts `--country`, `--format`, `--label`, `--year`, and `--sort released|title|format|label|catno|country`; add `--desc` to any `--sort`.

`releases` and `versions` also take `--owned`, which marks each release row with your own copies: see [Marking rows with --owned](#marking-rows-with---owned).

### artist and label

Every navigable name in `artist` and `label` output carries a ref: members and former members, parent label, sub-labels. Their `URLs:` line lists each linked site by domain, omitting empty entries. `Former:` lists only members Discogs marks inactive; a member with no recorded status stays under `Members:`.

### Flags per noun

Each flag applies only to the nouns below. Any other flag you pass (even `--limit 5`, the default value) fails with `invalid_argument` before a request is made, and so do `--after` and `--max-requests` with more than one ref, since a cursor continues one listing and a cap is spent on one.

| Noun | Flags besides `--json` / `--full` |
|---|---|
| `release` | `-v, --verbose`, `-c, --compact` |
| `releases @a...` | `--limit`, `--page`, `--role`, `--sort year\|title\|format`, `--desc`, `--owned`, `--after` and `--max-requests` (with `--role` or `--owned`) |
| `releases @l...` | `--limit`, `--page`, `--year`, `--owned`, `--after` and `--max-requests` (with `--year` or `--owned`) |
| `versions` | `--limit`, `--page`, `--country`, `--format`, `--label`, `--year`, `--sort released\|title\|format\|label\|catno\|country`, `--desc`, `--owned` and `--max-requests` (with `--owned`) |
| `artist`, `credits`, `identifiers`, `label`, `master`, `owned`, `price`, `tracklist` | none |

## tracks / price

Shortcuts for `get tracklist` and `get price`. Both support `--json`.

```bash
agent-discogs tracks @r352665
agent-discogs price @r352665
```

`price` prints each amount with the ISO currency code Discogs returns, never a symbol: `Mint (M): EUR 20.00`, `Lowest: GBP 9.99`. Under `--json` every price carries its `currency`: `"suggestions":{"Mint (M)":{"value":20.0,"currency":"EUR"}}`, `"market":{"for_sale":9,"lowest":9.99,"currency":"GBP"}`.

The `Market: 9 for sale from 9.36` line of `get release` and `get master` is a bare amount: Discogs prices it in the token account's buyer currency (USD when unauthenticated) without naming it, so there it has no code and no `currency` key. Use `price` when the currency matters.

## Several refs

`get`, `tracks`, and `price` accept up to 10 refs and run them in sequence, so comparing pressings is one command: `agent-discogs get release @r352665 @r847868 -c`. Text blocks are separated by a blank line; a ref that fails reports inline and the rest still run (exit code 1 if any failed). With `--json` and several refs the output is a list, with `{"ref": ..., "error": {...}}` items for failures.

## status

Show version, authentication mode, and cache location. The `Auth:` line names the credentials requests will carry: `token (authenticated)`, `oauth (authenticated)`, `consumer (authenticated app, not a user: no price suggestions)`, or `none (unauthenticated, 25 req/min)`. It makes no API call and never opens or creates the cache.

```bash
agent-discogs status
```

## cache

Manage the HTTP response cache:

```bash
agent-discogs cache clear          # Cache cleared.
agent-discogs cache clear --json   # {"cleared":true}
```

`cache clear` reports success only when the purge succeeded. If the cache cannot be opened, or the purge fails (the database is locked by another process, read-only, or damaged), it exits 1 with a `cache_error` (the JSON envelope on stdout under `--json`) and leaves the database exactly as it was.

The hint says whether to retry, fix permissions, or delete the file by hand; the CLI never deletes it.

## skills

Print the bundled agent guide. The content ships in the package, so it always matches the installed version:

```bash
agent-discogs skills                   # list bundled skills with the installed version (same as skills list)
agent-discogs skills get core          # workflows, output format, common patterns, troubleshooting
agent-discogs skills get core --full   # plus references: command reference, search patterns, pressings guide, Discogs data model
agent-discogs skills path [core]       # directory holding the bundled skill files
```

## Shell completion

`agent-discogs completion bash|zsh|fish` prints click's completion script for that shell, for a person typing in an interactive shell (agents get the same lists from `--help` and `skills get core`). It makes no API call and never opens the cache. Any other shell is a usage error (exit 2) naming the supported ones. Set it up once:

```bash
eval "$(agent-discogs completion bash)"   # in ~/.bashrc (bash 4.4 or later)
eval "$(agent-discogs completion zsh)"    # in ~/.zshrc, after compinit
agent-discogs completion fish > ~/.config/fish/completions/agent-discogs.fish
```

The script asks agent-discogs for the candidates on each Tab, so it follows the installed version without being regenerated.

Tab completes every closed set of values:

- commands, each under one name: the aliases (`find`, `query`, `fetch`, `show`) are not offered, but typed in full they still complete what follows;
- option names (`--<Tab>`);
- `get` nouns, `ids` included;
- the `search` type word (`release`, `master`, `artist`, `label`), only as the first positional after `search` (options or `--` may come before it); the query after it gets no suggestions;
- `search --release-type`, `search --sort`, the global `--color`, `cache clear`, the `skills` subcommands and skill names, and the `completion` shells;
- `get --sort`: the keys of the noun typed before it (`get versions --sort <Tab>` offers `released title format label catno country`), or every key of `releases` and `versions` before a noun, and none after a noun that takes no `--sort`. A key the noun does not take is still rejected when the command runs.

Refs, queries, Discogs names and free-text filters (`--artist`, `--format`, `--role`, ...) are not completed: refs are stateless, so there is no list to offer.
