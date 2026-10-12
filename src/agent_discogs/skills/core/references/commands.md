# Command Reference

## Global Flags

Global flags precede the command (`agent-discogs --cached price @r847868`).

| Flag | Description |
|------|-------------|
| `--cached` | Serve only from the response cache. A request the cache cannot serve fails with error code `cache_miss` (exit 1, per ref when several are given) before any network I/O. Never spends a request. |
| `--fresh` | Bypass the cache for this command. Every request goes to the API; the response is not stored, so a later run without `--fresh` may still see the older cached entry until its 1h TTL expires. Mutually exclusive with `--cached` (exit 2). |
| `--debug` | Trace panel on stderr after the budget line: one row per request (path, status, timing or `cache`, `×N attempts` on retry), scan statistics, cache size (`unknown (no home directory)` when the cache location cannot be resolved), output size with a token estimate, plus the SDK's own log lines in real time. `AGENT_DISCOGS_DEBUG=1` does the same. stdout and the exit code are unchanged. |
| `--help` | Show help |
| `--version` | Show version |

After any command that talks to the API, stderr ends with a budget line such as `api: 3 requests · 43/60 left this minute` (`⚠` when 10 or fewer remain). See the Budget section of the core skill.

The `Next page:` / `Continue scan:` command printed under a list repeats `--cached` or `--fresh` when the list was read under one, so the next page follows the same cache policy.


## search

Search the Discogs database.

```
agent-discogs search [type] <query> [--flags]
```

**Positional arguments:**

| Argument | Required | Description |
|----------|----------|-------------|
| `type` | No | Entity type: `release`, `master`, `artist`, `label`. If omitted, searches all types. |
| `query` | No | Search query string. Required unless at least one non-blank filter is given (e.g., `--catno`, `--barcode`); otherwise `invalid_argument` before any request. A query starting with `-` goes after `--` (`agent-discogs search --release-type all -- -ambient`); printed `Next page:` commands always put the type and query there. |

**Flags:**

| Flag | Description |
|------|-------------|
| `--artist` | Filter by artist name |
| `--barcode` | Filter by barcode |
| `--catno` | Filter by catalog number |
| `--country` | Filter by country |
| `--desc` | With `--sort`: descending (newest first) |
| `--format` | Filter by format (e.g., "Vinyl", "CD") |
| `--genre` | Filter by genre |
| `--json` | JSON output: a compact projection of the text view. Errors become `{"error":{"code",...}}` on stdout |
| `--full` | With `--json`: the raw SDK record instead of the projection |
| `--label` | Filter by label name |
| `--limit` | Results per page, 1-100 (default: 5) |
| `--page` | Page number, from 1. Only for server-side pages: `--release-type all`, or artist/label searches. Rejected on the default filtered path. |
| `--after` | Continuation cursor copied from the previous `Next page:` / `Continue scan:` line (filtered searches only) |
| `--release-type` | Filter by release type: `official` (default), `unofficial`, `all` |
| `--sort` | `date_added` only, and only on `search release ...`: order by when releases were added to Discogs (ascending; add `--desc` for newest first). Undocumented by Discogs and only seen working on release searches, so any other search, or `--desc` without `--sort`, fails with `invalid_argument` before any request. If Discogs drops it, results most likely come back in relevance order with no error. The filtered (default) path keeps the server's order. |
| `--style` | Filter by style |
| `--year` | Filter by release year |

**Examples:**

```bash
agent-discogs search "The Downward Spiral"
agent-discogs search release "The Downward Spiral" --year 1994
agent-discogs search artist "Nine Inch Nails"
agent-discogs search label "Nothing Records"
agent-discogs search master "The Downward Spiral"
agent-discogs search release --catno "INT-92346"
agent-discogs search release --barcode "606949235024"
agent-discogs search release "Blue Monday" --artist "New Order"
agent-discogs search release "Pretty Hate Machine" --format "Vinyl" --country US
agent-discogs search release "The Downward Spiral" --release-type all --page 2 --limit 10
agent-discogs search release "The Downward Spiral" --after 2:1.5   # cursor copied from the previous footer
agent-discogs search release "Pretty Hate Machine" --release-type all
agent-discogs search release "When The Whip Comes Down" --release-type unofficial
agent-discogs search release --artist "Nine Inch Nails" --sort date_added --desc   # newest additions first
```

Each row starts with a ref. A result of any other type than the four above starts with `id N [type]` instead (`[no type]` when Discogs sends none) and has no ref (`id` instead of `ref` under `--json`); the rest of the page is unaffected.

## get

Get entity details or paginated lists.

```
agent-discogs get <noun> <ref-or-id>... [--flags]
```

One or more refs (at most 10), run in sequence. Text blocks are separated by a blank line; a failing ref reports inline and the others still run (exit 1 if any failed). `--json` with several refs returns a list, with `{"ref": ..., "error": {...}}` items for failures; with one ref the shape is the single document.

**Nouns:**

| Noun | Expected Ref Type | Description |
|------|-------------------|-------------|
| `artist` | `@a` or numeric ID | Artist profile; `URLs:` lists linked sites by domain, omitting empty entries |
| `credits` | `@r` or numeric ID | Release, track, and sub-track credits grouped by role, each person once per role with an artist ref and track scope (none = whole release) |
| `identifiers` (alias `ids`) | `@r` or numeric ID | Barcodes, matrix/runout, label codes, rights societies |
| `label` | `@l` or numeric ID | Label profile; `URLs:` lists linked sites by domain, omitting empty entries |
| `master` | `@m` or numeric ID | Master release details |
| `price` | `@r` or numeric ID | Marketplace pricing (requires auth *and* seller settings on the token's account), each amount with its ISO currency code |
| `release` | `@r` or numeric ID | Full release details with inline artist/label refs, country, release date, `Added: YYYY-MM-DD` (day added to Discogs, in the timestamp's own offset; JSON `added`, RFC 3339), tracklist |
| `releases` | `@a` or `@l` (numeric ID = artist) | Artist discography, or a label's catalogue (paginated) |
| `tracklist` | `@r` or numeric ID | Tracklist only (from a release); an index track's parts are indented beneath it (JSON: `"type": "index"` with `sub_tracks`) |
| `versions` | `@m` or numeric ID | Master release versions (paginated) |

**Currency:** `price` prints amounts as `EUR 20.00`, the ISO code Discogs returns, never a symbol; under `--json` each suggestion is `{"value":20.0,"currency":"EUR"}` and `market` carries `lowest` and `currency`. The `Market: N for sale from 9.36` line of `release` and `master` is a bare amount in the token account's buyer currency (USD when unauthenticated): Discogs does not name it, so its JSON `market` has no `currency` key. Use `price @r...` when the currency matters.

**Flags:**

| Flag | Description |
|------|-------------|
| `--json` | JSON output: a compact projection of the text view. Errors become `{"error":{"code",...}}` on stdout |
| `--full` | With `--json`: the raw SDK record instead of the projection |
| `--limit` | `releases`, `versions` only: results per page, 1-100 (default: 5) |
| `--page` | Page number, from 1 (server-side pages: `versions`, `releases` without `--role` / label `--year`) |
| `--after` | Continuation cursor copied from the previous `Next page:` / `Continue scan:` line (`releases --role` and `releases @l... --year`, one ref only) |
| `-v, --verbose` | `release` only: append notes, credits, and identifiers |
| `-c, --compact` | `release` only: replace the tracklist with `Tracks: N (mm:ss)`; an index track counts as its parts, timed by their sum or, when a part is untimed, by the index track's own duration; otherwise the total appears only when every track has a duration (JSON: `tracks` string instead of `tracklist`) |

**Additional flags for `releases`:**

| Flag | Description |
|------|-------------|
| `--role` | Filter by credit role (e.g., "Main", "Remix", "Producer"). Case-insensitive substring match, client-side. |
| `--sort` | `year`, `title`, or `format` (server-side) |
| `--desc` | With `--sort`: descending |
| `--year` | Label catalogues only: keep releases from this year. Client-side scan (the API has no filters or sorting for `/labels/{id}/releases`), continues via `--after`. Artist discographies: use `--sort year` instead. |

**Additional flags for `versions`:**

| Flag | Description |
|------|-------------|
| `--country` | Filter versions by country |
| `--format` | Filter versions by format |
| `--label` | Filter versions by label |
| `--year` | Filter versions by release year |
| `--sort` | `released`, `title`, `format`, `label`, `catno`, or `country` (server-side) |
| `--desc` | With `--sort`: descending |

**Which flags apply where:** each flag applies only to the nouns below. Any other flag fails with `invalid_argument` before a request is made, even one set to its default value (`--limit 5`). `--after` also needs exactly one ref: a cursor continues one listing.

| Noun | Flags besides `--json` / `--full` |
|------|-----------------------------------|
| `release` | `-v, --verbose`, `-c, --compact` |
| `releases @a...` | `--limit`, `--page`, `--role`, `--sort`, `--desc`, `--after` (with `--role`) |
| `releases @l...` | `--limit`, `--page`, `--year`, `--after` (with `--year`) |
| `versions` | `--limit`, `--page`, `--country`, `--format`, `--label`, `--year`, `--sort`, `--desc` |
| `artist`, `credits`, `identifiers`, `label`, `master`, `price`, `tracklist` | none |

**Smart resolution:** `get versions @r847868` where `@r847868` is a release will auto-resolve to the release's master_id and fetch versions. Errors with a hint if the release has no master.

**Examples:**

```bash
agent-discogs get release @r847868
agent-discogs get release 847868
agent-discogs get artist @a3857
agent-discogs get master @m3719
agent-discogs get label @l647
agent-discogs get tracklist @r847868
agent-discogs get price @r847868
agent-discogs get credits @r847868
agent-discogs get identifiers @r847868
agent-discogs get ids @r847868
agent-discogs get releases @a3857
agent-discogs get releases @a3857 --page 2 --limit 10
agent-discogs get releases @a3857 --role Remix
agent-discogs get releases @a3857 --role Remix --after 2:6.0   # cursor copied from the previous footer
agent-discogs get releases @a3857 --sort year --desc
agent-discogs get releases @l647                       # label catalogue
agent-discogs get releases @l647 --year 1999           # client-side scan; continue with the printed --after
agent-discogs get versions @m3719
agent-discogs get versions @m3719 --country US --format "Vinyl"
agent-discogs get versions @m3719 --year 1994 --sort released
agent-discogs get release @r847868 --verbose
```

## status

Show session and auth info. No API call.

```bash
agent-discogs status
```

## cache

Manage the HTTP response cache. It lives at `$XDG_CACHE_HOME/agent-discogs` when `XDG_CACHE_HOME` is set to an absolute path, and at `~/.cache/agent-discogs` otherwise (an empty or relative value is ignored). `agent-discogs status` prints the path in use.

```bash
agent-discogs cache clear
```

## Aliases

| Alias | Equivalent |
|-------|------------|
| `agent-discogs find ...` | `agent-discogs search ...` |
| `agent-discogs query ...` | `agent-discogs search ...` |
| `agent-discogs fetch ...` | `agent-discogs get ...` |
| `agent-discogs show ...` | `agent-discogs get ...` |
| `agent-discogs tracks @r847868 ...` | `agent-discogs get tracklist @r847868 ...` |
| `agent-discogs price @r847868 ...` | `agent-discogs get price @r847868 ...` |
