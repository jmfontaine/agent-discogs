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

The installed skill is a thin pointer; the usage guide itself ships inside the package and is served by `agent-discogs skills get core`, so agents always read instructions that match the version they run (see [skills](https://github.com/jmfontaine/agent-discogs/blob/main/docs/commands.md#skills)).

## Authentication

Set your personal access token from [your Discogs developer settings](https://www.discogs.com/settings/developers):

```bash
export DISCOGS_TOKEN="your-token-here"
```

Credentials are required for price lookups, and Discogs' API documentation requires them for search too: unauthenticated searches currently get answers, but Discogs may refuse one with `auth_required`. Without any credentials the limit is 25 requests/minute; with a token, every command works at 60 requests/minute.

`price` needs one thing more than a token or OAuth credentials: Discogs only returns price suggestions to accounts that have filled out their [seller settings](https://www.discogs.com/settings/seller). Until you do, the endpoint answers 404 and `price` tells you so; every other command is unaffected.

The CLI sends whichever credentials discogs-sdk finds in the environment, in this order:

1. `DISCOGS_TOKEN`;
2. OAuth 1.0a (`DISCOGS_CONSUMER_KEY`, `DISCOGS_CONSUMER_SECRET`, `DISCOGS_ACCESS_TOKEN` and `DISCOGS_ACCESS_TOKEN_SECRET`, all four);
3. a consumer key and secret alone (`DISCOGS_CONSUMER_KEY` and `DISCOGS_CONSUMER_SECRET`);
4. otherwise none.

A consumer key and secret get 60 requests/minute but identify an app, not a user, so `price` needs a token or OAuth. `agent-discogs status` reports the mode in use.

> [!TIP]
> Use a `.env` file with [direnv](https://direnv.net/) to avoid exporting tokens manually in every shell.

## Commands

```bash
agent-discogs search release "The Downward Spiral" --year 1994
agent-discogs search artist "Nine Inch Nails" --limit 10
agent-discogs get artist @a3857
agent-discogs get versions @m3719 --country US --format Vinyl
agent-discogs get release @r352665 --json
agent-discogs status
agent-discogs cache clear
agent-discogs skills get core
```

Each command, its own flags and its text output are described in [Commands](https://github.com/jmfontaine/agent-discogs/blob/main/docs/commands.md). The global flags (`--cached`, `--fresh`, `--debug`, `--color`), the output modes (`--json`, `--full`) and the `--max-requests` cap have their own pages:

- `--json` and `--full`: [JSON output](https://github.com/jmfontaine/agent-discogs/blob/main/docs/json-output.md);
- `--cached`, `--fresh` and `--max-requests`: [Caching and request budget](https://github.com/jmfontaine/agent-discogs/blob/main/docs/caching-and-budget.md);
- `--debug`: [Debugging](https://github.com/jmfontaine/agent-discogs/blob/main/docs/debugging.md);
- `--color`: [Color](https://github.com/jmfontaine/agent-discogs/blob/main/docs/debugging.md#color); the default, `auto`, colors a terminal only, never a pipe.

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

## Documentation

- [Commands](https://github.com/jmfontaine/agent-discogs/blob/main/docs/commands.md): `search` (filters, sorting, client-side scans, `--added-since`), `get` and its nouns, `tracks` / `price`, `status`, `cache`, `skills`.
- [JSON output](https://github.com/jmfontaine/agent-discogs/blob/main/docs/json-output.md): projections, `--full`, the pagination envelope, non-ASCII and control-character escaping.
- [Caching and request budget](https://github.com/jmfontaine/agent-discogs/blob/main/docs/caching-and-budget.md): `--cached` / `--fresh`, cache errors, rate limits, what each command costs, `--max-requests`.
- [Errors](https://github.com/jmfontaine/agent-discogs/blob/main/docs/errors.md): the error layout, error codes, and the 401, 429 and connection-error hints.
- [Debugging](https://github.com/jmfontaine/agent-discogs/blob/main/docs/debugging.md): the `--debug` panel, output encoding, how Discogs text is neutralized, and `--color`.

## License

agent-discogs is licensed under the [Apache License 2.0](LICENSE.txt).
