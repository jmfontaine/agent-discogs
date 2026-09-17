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
npx skills upgrade jmfontaine/agent-discogs
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

Paginated nouns (`releases`, `versions`) support `--limit` (default: 5) and `--page`; `releases --role` filters client-side and continues via the printed `--after` cursor instead. Versions also accept `--country`, `--format`, and `--label` filters. Use `--json` for JSON output.

### tracks / price

Shortcuts for `get tracklist` and `get price`. Both support `--json`.

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

Codes: `not_found`, `seller_settings_required`, `auth_required`, `forbidden`, `rate_limited` (with `retry_after` seconds when provided), `api_error`, `connection_error`, `invalid_argument`, `unexpected`.

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

## Error handling

The CLI maps API errors to recovery-oriented messages with suggested next steps:

```
✗ Release 999999999 not found. Try: agent-discogs search "<title>"
✗ Authentication failed. Check your DISCOGS_TOKEN.
✗ Rate limit exceeded. Wait a moment and retry.
✗ Connection error. Check your network and retry.
```

## License

agent-discogs is licensed under the [Apache License 2.0](LICENSE.txt).
