# agent-discogs

A token-efficient Discogs CLI for AI agents that minimizes API calls.

```bash
agent-discogs search "Nine Inch Nails"
agent-discogs get release @r3857       # ref from search output
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

## Authentication

Set your personal access token from [your Discogs developer settings](https://www.discogs.com/settings/developers):

```bash
export DISCOGS_TOKEN="your-token-here"
```

A token is required for search and price lookups. Without it, only direct entity lookups work (25 requests/minute). With a token, all commands are available at 60 requests/minute.

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

Prefix a type (`release`, `master`, `artist`, `label`) to narrow results. Use `--limit` (default: 5) and `--page` for pagination.

Filters: `--artist`, `--barcode`, `--catno`, `--country`, `--format`, `--genre`, `--label`, `--release-type {official,unofficial,all}` (default: official), `--style`, `--year`. Use `--json` for raw JSON output.

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

Nouns: `artist`, `label`, `master`, `price`, `release`, `releases`, `tracklist`, `versions`.

Paginated nouns (`releases`, `versions`) support `--limit` (default: 5) and `--page`. Versions also accept `--country`, `--format`, and `--label` filters. Use `-v, --verbose` with `release` to include release notes and inline entity refs (`[@a...]`, `[@l...]`). Use `--json` for raw JSON output.

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
