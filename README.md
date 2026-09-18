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

A token is required for search and price lookups. Without it, only direct entity lookups work (25 requests/minute). With a token, every command except `price` is available at 60 requests/minute.

`price` needs one thing more than a token: Discogs only returns price suggestions to accounts that have filled out their [seller settings](https://www.discogs.com/settings/seller). Until you do, the endpoint answers 404 and `price` tells you so; every other command is unaffected.

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
```

Prefix a type (`release`, `master`, `artist`, `label`) to narrow results. Use `--limit` (default: 5) for page size. To continue, paste the `Next page:` command printed under the results: the default `--release-type official` filter is applied client-side, so continuation is a cursor (`--after`), not a page number. `--page` works only with `--release-type all` (server-side pages) or artist/label searches.

Filters: `--artist`, `--barcode`, `--catno`, `--country`, `--format`, `--genre`, `--label`, `--release-type {official,unofficial,all}` (default: official), `--style`, `--year`. Use `--json` for JSON output (see [JSON output](#json-output)).

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

`release` output carries inline refs for its artists and label (`[@a...]`, `[@l...]`), plus country and release date. `credits` lists who did what on a release, grouped by role, each person with an artist ref. `identifiers` lists barcodes, matrix/runout etchings, and other codes: the data that tells two pressings with the same catalog number apart. `-v, --verbose` on `release` appends notes, credits, and identifiers in one call; `-c, --compact` replaces the tracklist with a one-line `Tracks: 14 (65:01)` summary; the total is omitted when any track lacks a duration (use `tracks` when the tracklist is what you want).

Paginated nouns (`releases`, `versions`) support `--limit` (default: 5) and `--page`; `releases --role` filters client-side and continues via the printed `--after` cursor instead. `releases` takes an artist ref (discography, with `--role` and `--sort year|title|format`) or a label ref (catalogue; the API offers no filters or sorting there, so `--year` on a label is a client-side scan that continues via the printed `--after` cursor). `versions` accepts `--country`, `--format`, `--label`, `--year`, and `--sort released|title|format|label|catno|country`; add `--desc` to any `--sort`. Every navigable name in `artist` and `label` output carries a ref: members and former members, parent label, sub-labels. Use `--json` for JSON output.

### tracks / price

Shortcuts for `get tracklist` and `get price`. Both support `--json`.

`get`, `tracks`, and `price` accept up to 10 refs and run them in sequence, so comparing pressings is one command: `agent-discogs get release @r352665 @r847868 -c`. Text blocks are separated by a blank line; a ref that fails reports inline and the rest still run (exit code 1 if any failed). With `--json` and several refs the output is a list, with `{"ref": ..., "error": {...}}` items for failures.

```bash
agent-discogs tracks @r352665
agent-discogs price @r352665
```

### status

Show version, authentication mode, and cache location:

```bash
agent-discogs status
```

### cache

Manage the HTTP response cache:

```bash
agent-discogs cache clear
```

### skills

Print the bundled agent guide. The content ships in the package, so it always matches the installed version:

```bash
agent-discogs skills                   # list bundled skills with the installed version
agent-discogs skills get core          # workflows, output format, common patterns, troubleshooting
agent-discogs skills get core --full   # plus references: command reference, search patterns, pressings guide, Discogs data model
agent-discogs skills path [core]       # directory holding the bundled skill files
```

## JSON output

`search`, `get`, `tracks`, and `price` accept `--json`. The output is a compact projection of what the text view shows — refs, names, catalog numbers, counts — not the raw Discogs record, so a release is ~2 KB instead of ~30–50 KB of image URLs and bookkeeping. Empty fields are omitted, so test for a key rather than for null (the `pagination` envelope is the exception: its keys are always present, and `next_cursor` is `null` when nothing remains). `credits` is grouped by role: `{"Producer":[{"ref":"@a20661","name":"Flood","tracks":"1, 2"}],...}`. Lists are wrapped as `{"pagination":{...},"results":[...]}`.

```bash
agent-discogs get release @r352665 --json            # projection
agent-discogs get release @r352665 --json -v         # plus notes, credits, and identifiers
agent-discogs get release @r352665 --json --full     # raw SDK model, when you need a field the projection drops
```

Errors under `--json` are a JSON document on stdout with exit code 1:

```json
{"error":{"code":"not_found","message":"Master @m... not found.","hint":"Try: agent-discogs search \"<title>\"","status":404}}
```

Codes: `not_found`, `seller_settings_required`, `auth_required`, `forbidden`, `rate_limited` (with `retry_after` seconds when provided, and `limit`/`remaining` for the current minute when Discogs reports them), `cache_miss` (only under `--cached`), `api_error`, `connection_error`, `invalid_argument`, `unexpected`.

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

HTTP responses are cached automatically with a 1-hour TTL. The cache is stored at `~/.cache/agent-discogs/` (or `$XDG_CACHE_HOME/agent-discogs/`). Clear it with `agent-discogs cache clear`.

Two global flags control it for one command. They precede the subcommand and are mutually exclusive (exit code 2 otherwise):

```bash
agent-discogs --cached price @r352665      # serve only from the cache; never touches the API
agent-discogs --fresh get release @r352665 # bypass the cache; go to the API
```

`--cached` fails with a `cache_miss` error (exit code 1) on any request the cache cannot serve, before any network I/O. With several refs, hits print normally and each miss reports inline, exactly like other per-ref failures; under `--json` the miss item carries `error.code == "cache_miss"`. The check is exact: it uses the real cache key at the real moment, so a command that issues several requests fails at the first one that is not cached. There is no separate "is it cached?" query because the answer depends on the whole request sequence a command issues.

`--fresh` fetches every request from the API and does **not** store the response, so a later run without `--fresh` can still be served the older cached entry until its 1-hour TTL expires.

## Budget

Discogs throttles by source IP: 60 requests per minute with a token, 25 without, as a moving 60-second window. After any command that talks to the API, stderr ends with one budget line:

```
api: 3 requests · 43/60 left this minute
api: 0 requests · cached
api: 4 requests · 6/60 left this minute ⚠ pause ~60s before uncached calls
```

The count is what this command spent; `43/60` is what Discogs reported after the last request. `⚠` appears when 10 or fewer requests remain: pause about a minute, or keep to refs that are already cached (`--cached` guarantees zero spend). Without a token the warning suggests setting `DISCOGS_TOKEN` instead. The line is on stderr and never inside `--json` output; commands that make no request (`status`, `skills`, `cache clear`) print nothing.

What commands cost:

| Command | Requests |
|---|---|
| `get <noun>`, `tracks`, `price` | 1 per ref (`price`: up to 3; `versions @r...`: 2) |
| `search`, `get releases`, `get versions` (server-side pages) | 1 |
| Client-side scans: `search --release-type official\|unofficial`, `get releases --role`, `get releases @l... --year` | up to 5 |
| Anything repeated within 1 hour | 0 (cached) |

The CLI reports; it never sleeps or throttles on your behalf. When Discogs answers 429, the error says how much of the minute is left (`Rate limited: 0/60 requests left this minute.`), and under `--json` the envelope carries `retry_after`, `limit`, and `remaining`.

## Debugging

`--debug` (or `AGENT_DISCOGS_DEBUG=1`) prints a trace panel to stderr after the budget line: one row per request with status and timing (`cache` for cache hits, `×N attempts` when the SDK retried), scan statistics for client-side filters, cache size, and output size with a rough token estimate. The SDK's own log lines (`[sdk] HTTP request ...`, `[sdk] Retrying ... waiting 30s`) stream to stderr in real time, which is the only way to see a rate-limit retry while it is happening. stdout is byte-identical with and without the flag.

```
api: 2 requests · 58/60 left this minute
── debug ───────────────────────────────────────────────
total     1310ms
GET /artists/3857                               200   338ms
GET /artists/3857/releases?page=1&per_page=15   200   947ms
scan      1 API calls, 15 rows fetched, 5 kept, not capped
cache     ~/.cache/agent-discogs  540.0 KB  ttl 1h
output    494 chars, 9 lines, ≈123 tokens
```

## Error handling

The CLI maps API errors to recovery-oriented messages with suggested next steps:

```
✗ Release 999999999 not found. Try: agent-discogs search "<title>"
✗ Authentication failed. Check your DISCOGS_TOKEN.
✗ Rate limited: 0/60 requests left this minute. Retry in 30s.
✗ Not cached: GET /marketplace/price_suggestions/352665 (only under --cached)
✗ Connection error. Check your network and retry.
```

## License

agent-discogs is licensed under the [Apache License 2.0](LICENSE.txt).
