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

pipx, uv and pip need Python 3.11 or later.

### Homebrew

```bash
brew install jmfontaine/tap/agent-discogs
```

The formula comes from the maintainer's [Homebrew](https://brew.sh/) tap. It installs the PyPI release into a virtualenv on Homebrew's own Python, whatever Python you have.

To tap once and then use the short name:

```bash
brew tap jmfontaine/tap
brew install agent-discogs
```

Or in a Brewfile:

```ruby
tap "jmfontaine/tap"
brew "agent-discogs"
```

A prebuilt bottle exists only for Apple Silicon Macs on macOS 26 or later. Everywhere else (Intel Macs, earlier macOS, Linux) Homebrew builds from source, which needs Rust (for pydantic-core; Homebrew installs it as a build dependency) and takes a few minutes.

`brew upgrade` installs a new release once the tap has picked it up from PyPI.

### AI Coding Agent Skill

Install the skill so your AI coding agent can use agent-discogs automatically:

```bash
npx skills add jmfontaine/agent-discogs
```

The installed skill is a thin pointer; the usage guides themselves ship inside the package and are served by `agent-discogs skills get core`, plus `collection`, `marketplace` and `pressings` for the tasks that need them, so agents always read instructions that match the version they run (see [skills](https://github.com/jmfontaine/agent-discogs/blob/main/docs/commands.md#skills)).

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

A consumer key and secret get 60 requests/minute but identify an app, not a user, so `price` needs a token or OAuth, and so do `--owned`, `--wanted`, `--in-wantlist`, `get owned`, `get collection`, `get lists` and `get wantlist`, which read your own collection, lists and wantlist. `get inventory` alone works without credentials: Discogs shows anyone a seller's For Sale listings. `agent-discogs status` reports the mode in use.

> [!TIP]
> Use a `.env` file with [direnv](https://direnv.net/) to avoid exporting tokens manually in every shell.

## Commands

```bash
agent-discogs search release "The Downward Spiral" --year 1994
agent-discogs search artist "Nine Inch Nails" --limit 10
agent-discogs get artist @a3857
agent-discogs get versions @m3719 --country US --format Vinyl
agent-discogs get stats @r847868 @r352665 --currency EUR
agent-discogs get owned @r352665 @r847868
agent-discogs search release --artist "Nine Inch Nails" --year 1994 --limit 20 --owned
agent-discogs get collection --style Techno --format 12\" --max-requests 20
agent-discogs get wantlist --for-sale --max-price 20 --currency EUR --max-requests 60
agent-discogs get inventory <seller> --in-wantlist --condition VG+ --max-requests 60
agent-discogs get lists
agent-discogs get list 123
agent-discogs get release @r352665 --json
agent-discogs status
agent-discogs cache clear
agent-discogs skills get core
agent-discogs skills get pressings
```

Each command, its own flags and its text output are described in [Commands](https://github.com/jmfontaine/agent-discogs/blob/main/docs/commands.md). The global flags (`--cached`, `--fresh`, `--debug`, `--color`), the output modes (`--json`, `--full`) and the `--max-requests` cap have their own pages:

- `--json` and `--full`: [JSON output](https://github.com/jmfontaine/agent-discogs/blob/main/docs/json-output.md);
- `--cached`, `--fresh` and `--max-requests`: [Caching and request budget](https://github.com/jmfontaine/agent-discogs/blob/main/docs/caching-and-budget.md);
- `--debug`: [Debugging](https://github.com/jmfontaine/agent-discogs/blob/main/docs/debugging.md);
- `--color`: [Color](https://github.com/jmfontaine/agent-discogs/blob/main/docs/debugging.md#color); the default, `auto`, colors a terminal only, never a pipe.

For Tab completion in an interactive bash, zsh or fish shell, add `eval "$(agent-discogs completion zsh)"` (or `bash`) to the shell's startup file; see [Shell completion](https://github.com/jmfontaine/agent-discogs/blob/main/docs/commands.md#shell-completion) for fish.

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

- [Commands](https://github.com/jmfontaine/agent-discogs/blob/main/docs/commands.md): `search` (filters, sorting, client-side scans, `--added-since`, `--owned`, `--wanted`), `get` and its nouns (`get stats`, `get owned`, `get collection` and its filters, `get wantlist` and its `--for-sale` scan, `get inventory` and its grade, origin, price and `--in-wantlist` filters, `get lists` and `get list`), `tracks` / `price`, `status`, `cache`, `skills`, shell completion.
- [JSON output](https://github.com/jmfontaine/agent-discogs/blob/main/docs/json-output.md): projections, `--full`, the pagination envelope, `owned` and `wanted` rows, collection rows, `get stats` and its `market` object, wantlist rows and their `market`, inventory listings and their `wantlist` object, lists and their items, non-ASCII and control-character escaping.
- [Caching and request budget](https://github.com/jmfontaine/agent-discogs/blob/main/docs/caching-and-budget.md): `--cached` / `--fresh`, cache errors, rate limits, what each command costs, `--max-requests`.
- [Errors](https://github.com/jmfontaine/agent-discogs/blob/main/docs/errors.md): the error layout, error codes, and the 401, 429 and connection-error hints.
- [Debugging](https://github.com/jmfontaine/agent-discogs/blob/main/docs/debugging.md): the `--debug` panel, output encoding, how Discogs text is neutralized, and `--color`.
- [What the Discogs API cannot answer](https://github.com/jmfontaine/agent-discogs/blob/main/docs/api-limits.md): sales history, the worth of one record or a folder, who sells a release, alerts, finding sellers, tempo and key, writes; and what to do instead.

## License

agent-discogs is licensed under the [Apache License 2.0](LICENSE.txt).
