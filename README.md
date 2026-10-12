# agent-discogs

A token-efficient Discogs CLI for AI agents that minimizes API calls.

```bash
agent-discogs search "Nine Inch Nails"
agent-discogs get release @r352665      # ref from search output
agent-discogs tracks @r352665          # tracklist shortcut
agent-discogs price @r352665           # price guide shortcut
```

## Installation

```bash
pipx install agent-discogs
```

Or with [uv](https://docs.astral.sh/uv/):

```bash
uv tool install agent-discogs
```

Or with pip:

```bash
pip install agent-discogs
```

Requires Python 3.10+.

### AI Coding Agent Skill

Install the skill so your AI coding agent can use agent-discogs automatically:

```bash
npx skills add jmfontaine/agent-discogs
```

The installed skill is a thin pointer; the usage guide itself ships inside the package and is served by `agent-discogs skills get core`, so agents always read instructions that match the version they run (see [skills](#skills)).

## Authentication

Set your personal access token from [your Discogs developer settings](https://www.discogs.com/settings/developers):

```bash
export DISCOGS_TOKEN="your-token-here"
```

Credentials are required for price lookups, and Discogs' API documentation requires them for search too: unauthenticated searches currently get answers, but Discogs may refuse one with `auth_required`. Without any credentials the limit is 25 requests/minute; with a token, every command works at 60 requests/minute.

`price` needs one thing more than a token or OAuth credentials: Discogs only returns price suggestions to accounts that have filled out their [seller settings](https://www.discogs.com/settings/seller). Until you do, the endpoint answers 404 and `price` tells you so; every other command is unaffected.

The CLI sends whichever credentials discogs-sdk finds in the environment, in this order: `DISCOGS_TOKEN`; OAuth 1.0a (`DISCOGS_CONSUMER_KEY`, `DISCOGS_CONSUMER_SECRET`, `DISCOGS_ACCESS_TOKEN` and `DISCOGS_ACCESS_TOKEN_SECRET`, all four); a consumer key and secret alone (`DISCOGS_CONSUMER_KEY` and `DISCOGS_CONSUMER_SECRET`); otherwise none. A consumer key and secret get 60 requests/minute but identify an app, not a user, so `price` needs a token or OAuth. `agent-discogs status` reports the mode in use.

> [!TIP]
> Use a `.env` file with [direnv](https://direnv.net/) to avoid exporting tokens manually in every shell.

## Commands

### search

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
```

Prefix a type (`release`, `master`, `artist`, `label`) to narrow results. Use `--limit` (1-100, default: 5) for page size. To continue, paste the `Next page:` command printed under the results: the default `--release-type official` filter is applied client-side, so continuation is a cursor (`--after`), not a page number, and the total is an upper bound (`≤N`). `--release-type unofficial` is filtered by Discogs itself (`format=Unofficial Release`), so it shows an exact total and pages with `--page`; combined with `--format` it falls back to the client-side scan, because Discogs cannot reliably combine two format filters. `--page` (from 1) works with those server-side pages, `--release-type all`, and artist/label searches. A search needs a query or at least one non-blank filter; an out-of-range `--limit`/`--page` or an empty search fails with `invalid_argument` before any request. A query starting with `-` goes after `--` (`agent-discogs search --release-type all -- -ambient`); the printed `Next page:` command always puts the type and query there, so it pastes back unchanged.

Discogs occasionally returns a result whose type is not one of those four, or has none. Such a row has no ref, because nothing says what its ID refers to: it reads `id 9001 [playlist] "..."` in text (`[no type]` when Discogs sends none) and carries `id` instead of `ref` under `--json`. The rest of the page prints as usual.

Filters: `--artist`, `--barcode`, `--catno`, `--country`, `--format`, `--genre`, `--label`, `--release-type {official,unofficial,all}` (default: official), `--style`, `--year`. Use `--json` for JSON output (see [JSON output](#json-output)).

`search release ... --sort date_added` orders results by when they were added to Discogs; add `--desc` for newest first. The parameter is undocumented: Discogs lists no sort keys for search, and it has only been seen working on release searches, so `--sort` on any other search (or `--desc` without `--sort`) fails with `invalid_argument` before any request. If Discogs ever drops it, results will most likely come back in relevance order with no error. With the default `--release-type official` filter, the client-side scan keeps the server's order, and the printed `Next page:` command keeps `--sort` and `--desc`.

Client-side scans (`--release-type official`, or `unofficial` with `--format`; `get releases --role`; `get releases @l... --year`) read Discogs' largest pages, 100 rows each, until `--limit` rows match or the list ends. There is no cap by default, so a sparse filter over a long list can take many requests. `--max-requests N` caps the HTTP sends of the whole command, header lookups included (cache hits are free); it is accepted only where a scan runs (or with `--added-since`, below) and with one ref, and must be 1 or more, otherwise `invalid_argument` before any request. A scan that stops at the cap ends in a `Continue scan:` command that repeats `--max-requests`, with `scan stopped at --max-requests N` in the header (`capped: true`, `stop_reason: "max_requests"` in JSON). If text-mode header lookups use the whole budget, the scan has not started: the header says so and shows no counts, and JSON's `total_items`/`total_pages` are `null`. When Discogs answers 429 partway through a scan, the rows read so far are returned with a `Continue scan:` command and a `scan stopped: rate limited` note (`stop_reason: "rate_limited"` and `retry_after` in JSON), exit 0; a 429 on the first page is an error. Agents whose tool calls have a time limit should pass `--max-requests`.

`search release ... --added-since YYYY-MM-DD` lists the releases added to Discogs on or after that day (inclusive, from midnight UTC), newest first, each row ending in its added day in UTC (`· added 2026-09-01`; `added`, a full RFC 3339 timestamp, under `--json`). It is **experimental**: Discogs has no date filter and search results carry no added date, so it walks the undocumented `date_added` search order and looks up each release to read its date (the SDK's `releases_added_since()`), stopping at the first release older than the cutoff. It is complete only while Discogs keeps that order; when a looked-up date breaks it, the command fails with `bad_response` rather than return a doubtful list (the responses are cached, so a plain retry repeats the error for up to an hour; `--fresh` asks Discogs again). It costs one request per release the search matches in the window, before `--release-type` filtering, plus one per 100 search results and one for the release that ends the scan; a repeat within the hour is free. There is no cap by default: `--max-requests N` stops it after N sends, and the result then ends with `Stopped at --max-requests N: the result may be incomplete` (JSON: `complete: false`; `total` counts the rows found). Pass `--max-requests` if your tool calls have a time limit. The CLI never waits out the rate limit: a 429 partway through fails the command with `rate_limited`, and a rerun after `retry_after` resends none of the lookups already made. The whole window comes back unless you pass `--limit`, which cuts the rows, not the requests. `--release-type official` (the default) and `unofficial` with `--format` filter on the looked-up formats at no extra request; `unofficial` alone is filtered by Discogs (`format=Unofficial Release`), which saves the lookups of official releases. It needs an explicit `search release ...` and rejects `--sort`, `--desc`, `--page` and `--after` (`invalid_argument`, before any request). Under `--cached`, any lookup the cache does not hold fails the command with `cache_miss`.

### get

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
```

Nouns: `artist`, `credits`, `identifiers` (alias `ids`), `label`, `master`, `price`, `release`, `releases`, `tracklist`, `versions`.

`release` output carries inline refs for its artists and label (`[@a...]`, `[@l...]`), plus country, release date, and the day the release was added to Discogs (`Country: US · Released: 1994-03-08 · Added: 2007-02-23`; the date is the one in the timestamp's own offset, and the field is omitted when Discogs has none). Under `--json` that date is `added`, a full RFC 3339 timestamp such as `2007-02-23T01:13:39-08:00`. It answers "is this a new entry or an old one?" at no extra request. Unknown date parts are left out, in text and `--json` alike: Discogs' year `0` is omitted, and a release date drops a zero month or day (`1994-05-00` shows as `1994-05`, `1994-00-00` as `1994`, and the text view prints no `Released:` when that equals the year). Its format lists one entry per physical format, separated by `;`: the quantity when above one, the name, then descriptions and free text in parentheses (`Format: 2×Vinyl (LP, Album, 180g, Gatefold); CD (Album)`; `--json` carries the same string). `credits` lists who did what on a release, grouped by role, each person with an artist ref. It includes credits Discogs files on individual tracks and sub-tracks: each person appears once per role with the tracks the credit covers, and no track list means the whole release. `identifiers` lists barcodes, matrix/runout etchings, and other codes: the data that tells two pressings with the same catalog number apart. `-v, --verbose` on `release` appends notes, credits, and identifiers in one call; `-c, --compact` replaces the tracklist with a one-line `Tracks: 14 (65:01)` summary; the total is omitted when any track lacks a duration (use `tracks` when the tracklist is what you want). Index tracks (a suite or medley split into parts) list their sub-tracks indented beneath them. The compact count includes each part, and the total sums the parts' durations; when a part is untimed, the index track's own duration stands in for the suite.

Paginated nouns (`releases`, `versions`) support `--limit` (1-100, default: 5) and `--page` (from 1); `releases --role` filters client-side and continues via the printed `--after` cursor instead. `releases` takes an artist ref (discography, with `--role` and `--sort year|title|format`) or a label ref (catalogue; the API offers no filters or sorting there, so `--year` on a label is a client-side scan that continues via the printed `--after` cursor). `versions` accepts `--country`, `--format`, `--label`, `--year`, and `--sort released|title|format|label|catno|country`; add `--desc` to any `--sort`. Every navigable name in `artist` and `label` output carries a ref: members and former members, parent label, sub-labels. Their `URLs:` line lists each linked site by domain, omitting empty entries. `Former:` lists only members Discogs marks inactive; a member with no recorded status stays under `Members:`. Use `--json` for JSON output.

Each flag applies only to the nouns below. Any other flag you pass (even `--limit 5`, the default value) fails with `invalid_argument` before a request is made, and so do `--after` and `--max-requests` with more than one ref, since a cursor continues one listing and a cap is spent on one.

| Noun | Flags besides `--json` / `--full` |
|---|---|
| `release` | `-v, --verbose`, `-c, --compact` |
| `releases @a...` | `--limit`, `--page`, `--role`, `--sort year\|title\|format`, `--desc`, `--after` and `--max-requests` (with `--role`) |
| `releases @l...` | `--limit`, `--page`, `--year`, `--after` and `--max-requests` (with `--year`) |
| `versions` | `--limit`, `--page`, `--country`, `--format`, `--label`, `--year`, `--sort released\|title\|format\|label\|catno\|country`, `--desc` |
| `artist`, `credits`, `identifiers`, `label`, `master`, `price`, `tracklist` | none |

### tracks / price

Shortcuts for `get tracklist` and `get price`. Both support `--json`.

`price` prints each amount with the ISO currency code Discogs returns, never a symbol: `Mint (M): EUR 20.00`, `Lowest: GBP 9.99`. Under `--json` every price carries its `currency`: `"suggestions":{"Mint (M)":{"value":20.0,"currency":"EUR"}}`, `"market":{"for_sale":9,"lowest":9.99,"currency":"GBP"}`. The `Market: 9 for sale from 9.36` line of `get release` and `get master` is a bare amount: Discogs prices it in the token account's buyer currency (USD when unauthenticated) without naming it, so there it has no code and no `currency` key. Use `price` when the currency matters.

`get`, `tracks`, and `price` accept up to 10 refs and run them in sequence, so comparing pressings is one command: `agent-discogs get release @r352665 @r847868 -c`. Text blocks are separated by a blank line; a ref that fails reports inline and the rest still run (exit code 1 if any failed). With `--json` and several refs the output is a list, with `{"ref": ..., "error": {...}}` items for failures.

```bash
agent-discogs tracks @r352665
agent-discogs price @r352665
```

### status

Show version, authentication mode, and cache location. The `Auth:` line names the credentials requests will carry: `token (authenticated)`, `oauth (authenticated)`, `consumer (authenticated app, not a user: no price suggestions)`, or `none (unauthenticated, 25 req/min)`. It makes no API call and never opens or creates the cache.

```bash
agent-discogs status
```

### cache

Manage the HTTP response cache:

```bash
agent-discogs cache clear          # Cache cleared.
agent-discogs cache clear --json   # {"cleared":true}
```

`cache clear` reports success only when the purge succeeded. If the cache cannot be opened, or the purge fails (the database is locked by another process, read-only, or damaged), it exits 1 with a `cache_error` (the JSON envelope on stdout under `--json`) and leaves the database exactly as it was. The hint says whether to retry, fix permissions, or delete the file by hand; the CLI never deletes it.

### skills

Print the bundled agent guide. The content ships in the package, so it always matches the installed version:

```bash
agent-discogs skills                   # list bundled skills with the installed version (same as skills list)
agent-discogs skills get core          # workflows, output format, common patterns, troubleshooting
agent-discogs skills get core --full   # plus references: command reference, search patterns, pressings guide, Discogs data model
agent-discogs skills path [core]       # directory holding the bundled skill files
```

## JSON output

`search`, `get`, `tracks`, and `price` accept `--json` (as does `cache clear`, which prints `{"cleared":true}` or the error envelope). The output is a compact projection of what the text view shows — refs, names, catalog numbers, counts — not the raw Discogs record, so a release is ~2 KB instead of ~30–50 KB of image URLs and bookkeeping. Empty fields are omitted, so test for a key rather than for null (the `pagination` envelope is the exception: `page`, `total_items` and `total_pages` are always present, with `null` totals only when a scan stopped before reading any page; a filtered list always adds `filtered`, `capped` and `next_cursor`, `null` when nothing remains; a capped one adds `stop_reason`, plus `retry_after` when Discogs sent one). Each release, master, artist, label, and search result also carries `url`, its page on the Discogs website, always absolute: `"url":"https://www.discogs.com/release/847868-Nine-Inch-Nails-The-Downward-Spiral"`. Use it to hand a person a link; text output leaves it out to save tokens. Rows of `get versions` and `get releases` carry `url` only when Discogs includes a link (`uri`) in the row, which it does not today, and the release facets (`tracks`, `price`, `get credits`, `get identifiers`) never do: use `get release @r... --json` for the link. `credits` is grouped by role: `{"Producer":[{"ref":"@a20661","name":"Flood","tracks":"1, 2"}],...}`. Tracklist rows carry `"type": "heading"` or `"type": "index"` when they are not plain tracks, and an index row nests its parts under `sub_tracks`. Lists are wrapped as `{"pagination":{...},"results":[...]}`.

In a filtered list, the totals describe the listing before the filter, not your pages: `page` counts pages of `--limit` matching rows, while `total_items` is the unfiltered total (the `≤N` of the text header) and `total_pages` counts the 100-row pages the scan reads from Discogs. For example, the second page of `get releases @l647 --year 1994 --limit 2 --json` reported `"page":2,"total_items":2723,"total_pages":28`: 28 is the number of Discogs pages a full scan reads, not the number of pages of matches, which nothing tells in advance. Continue with `next_cursor` until it is `null`.

```bash
agent-discogs get release @r352665 --json            # projection
agent-discogs get release @r352665 --json -v         # plus notes, credits, and identifiers
agent-discogs get release @r352665 --json --full     # the complete record, when you need a field the projection drops
agent-discogs get credits @r352665 --json --full     # complete release credits, plus track_credits: [{position, title, extra_artists}]
```

`--full` prints the complete record as the SDK normalizes it, not byte-for-byte what Discogs sends: field names are consistent (`catalog_number` for `catno`, `extra_artists` for `extraartists`, `name_variation` for `anv`, a track's `type` for `type_`), a search result's `uri` is absolute (`https://www.discogs.com/release/1`, not `/release/1`), timestamps are RFC 3339 with an offset (`"date_added":"2005-01-03T23:17:26-08:00"`), and a timestamp that is absent or does not parse is `null`. Fields the SDK does not model appear as Discogs sends them. Unlike the projection, `--full` keeps empty fields, as `null` when unset.

On a UTF-8 stdout, non-ASCII text is written as is (`"title":"無罪モラトリアム"`), which keeps Japanese, Cyrillic, or accented titles short. On any other stdout encoding, it is written as `\uXXXX` escapes so the document stays valid. Either way, C1 controls (U+0080–U+009F), bidi controls (U+061C, U+200E, U+200F, U+202A–U+202E, U+2066–U+2069), U+2028/U+2029, and lone surrogates are always escaped. The escaping is lossless: `json.loads` returns every string unchanged.

Errors under `--json` are a JSON document on stdout with exit code 1:

```json
{"error":{"code":"not_found","message":"Master @m... not found.","hint":"Try: agent-discogs search \"<title>\"","status":404}}
```

Codes: `not_found`, `seller_settings_required`, `auth_required`, `forbidden`, `rate_limited` (with `retry_after` in whole seconds, rounded up, when Discogs sends a usable `Retry-After`, as seconds or an HTTP date; `0` means retry now; and `limit`/`remaining` for the current minute when Discogs reports them), `cache_miss` (only under `--cached`), `cache_error` (with `path`, the cache database; see [Caching](#caching)), `api_error`, `connection_error`, `bad_response` (Discogs answered with something the CLI cannot read: a non-JSON page, data that does not fit the expected shape, or a malformed results page; never your arguments, so retry later, with `--fresh` if the same error repeats), `invalid_argument`, `unexpected` (the message names the exception class).

## Ref system

Output includes typed refs that encode the entity type and Discogs ID. Copy them into subsequent commands.

| Prefix | Type | Example |
|---|---|---|
| `@r` | Release | `@r352665` |
| `@a` | Artist | `@a3857` |
| `@m` | Master | `@m3719` |
| `@l` | Label | `@l647` |

Raw numeric IDs also work: `agent-discogs get release 352665`.

Smart resolution: `get versions @r352665` auto-resolves the release to its master.

## Caching

HTTP responses are cached automatically with a 1-hour TTL. The cache is stored at `$XDG_CACHE_HOME/agent-discogs/` when `XDG_CACHE_HOME` is set to an absolute path, and at `~/.cache/agent-discogs/` otherwise. Clear it with `agent-discogs cache clear`.

Two global flags control it for one command. They precede the subcommand and are mutually exclusive (exit code 2 otherwise):

```bash
agent-discogs --cached price @r352665      # serve only from the cache; never touches the API
agent-discogs --fresh get release @r352665 # bypass the cache; go to the API
```

`--cached` fails with a `cache_miss` error (exit code 1) on any request the cache cannot serve, before any network I/O. With several refs, hits print normally and each miss reports inline, exactly like other per-ref failures; under `--json` the miss item carries `error.code == "cache_miss"`. The check is exact: it uses the real cache key at the real moment, so a command that issues several requests fails at the first one that is not cached. There is no separate "is it cached?" query because the answer depends on the whole request sequence a command issues.

That includes client-side scans: under `--cached` a scan reads cached pages until its page is full, and the first page the cache does not hold fails the whole command with `cache_miss`, rows read so far included. Cache hits never count against `--max-requests`, so the cap does not stop a cache-only scan early. `--fresh` stores nothing, so under it a text `get releases` scan spends one request on the header every run: with `--max-requests 1` it returns the empty capped page, and its header says to continue with a higher `--max-requests`, since the same cap would spend itself on the header again.

`--fresh` fetches every request from the API and does **not** store the response, so a later run without `--fresh` can still be served the older cached entry until its 1-hour TTL expires.

A cache the CLI cannot use (its directory cannot be created, `cache.db` is a symlink, not a regular file, or owned by someone else, or SQLite cannot open it) fails the first request that needs it with a `cache_error` (exit code 1): the message names the database path and what failed, and the hint says what to fix. Nothing deletes the database for you; fix its permissions or ownership, or delete it by hand. `--fresh` never opens the cache, so it still works meanwhile.

The `Next page:` / `Continue scan:` command printed under a list repeats `--cached` or `--fresh` (and `--max-requests`), so the next page is read under the same cache policy and cap as the first.

## Budget

Discogs throttles by source IP: 60 requests per minute with any credentials, 25 without, as a moving 60-second window. After any command that talks to the API, stderr ends with one budget line:

```
api: 3 requests · 43/60 left this minute
api: 0 requests · cached
api: 4 requests · 6/60 left this minute ⚠ pause ~60s before uncached calls
```

The count is what this command spent: every HTTP request sent, including one whose connection failed; a `--cached` miss sends nothing. `43/60` is what Discogs reported after the last request. `⚠` appears when 10 or fewer requests remain: pause about a minute, or keep to refs that are already cached (`--cached` guarantees zero spend). When the limit Discogs reported is below 60/min (the anonymous tier), the warning suggests setting `DISCOGS_TOKEN` instead; which credentials are set does not change it. The line is on stderr and never inside `--json` output; commands that make no request (`status`, `skills`, `cache clear`) print nothing.

What commands cost (network requests; listing rows are per ref):

| Command | Requests |
|---|---|
| `get artist\|label\|master\|release\|credits\|identifiers\|tracklist`, `tracks` | 1 per ref |
| `get price`, `price` | 3 per ref (`--json --full`: 2, the release itself is not read) |
| `get releases @a...`, `get releases @l...`, `get versions @m...` | `--json`: 1; text: 2 (the header's artist, label or master name) |
| `get versions @r...` | 2, text or `--json` (the release, for its master, then the list) |
| Client-side scans: `get releases @a... --role`, `get releases @l... --year` | `--json`: one per 100 rows scanned; text: one more, for the header name |
| `search artist\|label`, `search --release-type all`, `search --release-type unofficial` (filtered by Discogs, without `--format`) | 1 |
| Other searches (`--release-type official`, the default, or `unofficial` with `--format`): client-side scans | one per 100 rows scanned |
| `search release ... --added-since` (experimental) | 1 per release the search matches in the window (before `--release-type` filtering), plus 1 per 100 search results and 1 for the release that ends the scan |
| Anything repeated within 1 hour | 0 (cached) |

A scan reads 100-row pages until the page is full or the list ends: a dense filter costs one request, and a sparse one over 2,000 results up to 20. With `--max-requests N` the command sends at most N.

The CLI reports; it never retries, sleeps, or throttles on your behalf. Each request is sent once: a 429, a 5xx, or a connection failure is reported as that request's error without a retry, and retrying is the caller's call. With several refs, the failing ref gets its error block and the rest still run (exit 1). When Discogs answers 429, the error says how much of the minute is left (`Rate limited: 0/60 requests left this minute.`), and under `--json` the envelope carries `retry_after`, `limit`, and `remaining`. Without credentials, the hint adds `60 req/min with DISCOGS_TOKEN, 25 without.`; with any credentials it states the limit Discogs reported for them instead (`Discogs reported 60 req/min for the credentials in use (oauth).`), or the authenticated 60 req/min when the 429 carried no rate-limit headers.

One exception: a 429 partway through a client-side scan returns the rows read so far, exit 0, with a `Continue scan:` command and `stop_reason: "rate_limited"` (plus `retry_after` when Discogs sent one) under `--json`. A 429 on a scan's first page is the usual error.

## Debugging

`--debug` (or `AGENT_DISCOGS_DEBUG=1`) prints a trace panel to stderr after the budget line: one row per response with status and timing (`cache` for cache hits; a `--cached` miss or a failed connection has no response and no row), scan statistics for client-side filters, cache size, and output size with a rough token estimate. The SDK's own log lines (`[sdk] HTTP request ...`, `[sdk] Cache hit ...`) stream to stderr in real time. stdout is byte-identical with and without the flag, and so is the exit code: when the cache location cannot be resolved (no home directory and no absolute `XDG_CACHE_HOME`), the cache row reads `unknown (no home directory)` and the panel still prints.

```
api: 2 requests · 58/60 left this minute
── debug ───────────────────────────────────────────────
total     1310ms
GET /artists/3857                                200   338ms
GET /artists/3857/releases?page=1&per_page=100   200   947ms
scan      1 API calls, 100 rows fetched, 5 kept, not capped
cache     ~/.cache/agent-discogs  540.0 KB  ttl 1h
output    494 chars, 9 lines, ≈123 tokens
```

## Error handling

The CLI maps API errors to recovery-oriented messages with suggested next steps:

```
✗ Release 999999999 not found.
  Try: agent-discogs search "<title>"
✗ Authentication failed with DISCOGS_TOKEN.
  Check the token, or generate a new one at discogs.com/settings/developers. agent-discogs status shows the credentials in use.
✗ Rate limited: 0/60 requests left this minute.
  Retry in 30s. Discogs reported 60 req/min for the credentials in use (token).
✗ Not cached: GET /releases/352665
  Rerun without --cached to fetch it from the API.
✗ Connection error: [Errno 61] Connection refused
  Check your network and retry.
✗ Connection error: 407 Proxy Authentication Required
  Retrying will not help: check your proxy, protocol, or network configuration.
✗ Cache error at /home/me/.cache/agent-discogs/cache.db: could not vet the database file: PermissionError: /home/me/.cache/agent-discogs/cache.db is not a regular file owned by the current user
  Fix the permissions or ownership of /home/me/.cache/agent-discogs and the files in it, or delete /home/me/.cache/agent-discogs/cache.db and any cache.db-wal or cache.db-shm beside it by hand. --fresh skips the cache.
```

Each error is a `✗` line with the message, then the hint, when there is one, on the next line, indented by two spaces. The same message and hint are `message` and `hint` in the `--json` envelope.

A 401 names the credentials that were sent, as `status` reports them: `DISCOGS_TOKEN`, the consumer key/secret, or OAuth (with the variables behind each), and says what to check. A consumer key and secret identify an app, not a user, so that hint also points at `DISCOGS_TOKEN` or OAuth for price suggestions. With no credentials set, the message reads `Authentication failed: no credentials are set.` and the hint says to export `DISCOGS_TOKEN`.

An API error the CLI has no specific message for reads `API error (503): <summary>`: the status once, then what Discogs said, collapsed to one line and cut at 200 characters. Any other unexpected failure reads `Unexpected error: <exception class>: <message>`.

A connection failure reads `Connection error: <cause>`, the cause summarized like an API error's: one line, cut at 200 characters. The hint says to retry only when the failure is transient (a refused or dropped connection, a timeout); a proxy error or an unsupported URL scheme fails the same way every time, so the hint points at the proxy, protocol, or network configuration instead.

If the reader closes stdout early (`agent-discogs ... | head -1`), the CLI exits 1 without an error message. Under `set -o pipefail` that exit 1 is expected, not a failed lookup.

## Output encoding

Command output follows the encoding of stdout. When it is not UTF-8 (`PYTHONIOENCODING=ascii` or `latin-1`, some Windows pipes), a character the encoding cannot hold prints as a backslash escape (`宇多田` → `\u5b87\u591a\u7530`) and the command still succeeds; an encoding problem is never reported as an argument error. (click itself writes `--help` as UTF-8 when stdout is plain ASCII.)

Discogs text is user-generated, so the text views never print it raw. A line break or tab inside a field becomes a space; any other control or bidi character prints as a visible escape (`\x1b`, `\u202e`), so a title cannot clear the screen, reorder the line, or start a line that looks like `Next page:` or `✗`. Release notes (`get release -v`) keep their line breaks, with every continuation line indented under `Notes:`. Error messages on stderr get the same treatment. `--json` output keeps every value exact (see [JSON output](#json-output)).

## License

agent-discogs is licensed under the [Apache License 2.0](LICENSE.txt).
