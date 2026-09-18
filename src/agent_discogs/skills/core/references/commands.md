# Command Reference

## Global Flags

Global flags precede the command (`agent-discogs --cached price @r847868`).

| Flag | Description |
|------|-------------|
| `--cached` | Serve only from the response cache. A request the cache cannot serve fails with error code `cache_miss` (exit 1, per ref when several are given) before any network I/O. Never spends a request. |
| `--fresh` | Bypass the cache for this command. Every request goes to the API; the response is not stored, so a later run without `--fresh` may still see the older cached entry until its 1h TTL expires. Mutually exclusive with `--cached` (exit 2). |
| `--debug` | Trace panel on stderr after the budget line: one row per request (path, status, timing or `cache`, `×N attempts` on retry), scan statistics, cache size, output size with a token estimate, plus the SDK's own log lines in real time. `AGENT_DISCOGS_DEBUG=1` does the same. stdout is unchanged. |
| `--help` | Show help |
| `--version` | Show version |

After any command that talks to the API, stderr ends with a budget line such as `api: 3 requests · 43/60 left this minute` (`⚠` when 10 or fewer remain). See the Budget section of the core skill.


## search

Search the Discogs database.

```
agent-discogs search [type] <query> [--flags]
```

**Positional arguments:**

| Argument | Required | Description |
|----------|----------|-------------|
| `type` | No | Entity type: `release`, `master`, `artist`, `label`. If omitted, searches all types. |
| `query` | No | Search query string. Required unless filters are provided (e.g., `--catno`, `--barcode`). |

**Flags:**

| Flag | Description |
|------|-------------|
| `--artist` | Filter by artist name |
| `--barcode` | Filter by barcode |
| `--catno` | Filter by catalog number |
| `--country` | Filter by country |
| `--format` | Filter by format (e.g., "Vinyl", "CD") |
| `--genre` | Filter by genre |
| `--json` | JSON output: a compact projection of the text view. Errors become `{"error":{"code",...}}` on stdout |
| `--full` | With `--json`: the raw SDK record instead of the projection |
| `--label` | Filter by label name |
| `--limit` | Results per page (default: 5) |
| `--page` | Page number. Only for server-side pages: `--release-type all`, or artist/label searches. Rejected on the default filtered path. |
| `--after` | Continuation cursor copied from the previous `Next page:` / `Continue scan:` line (filtered searches only) |
| `--release-type` | Filter by release type: `official` (default), `unofficial`, `all` |
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
```

## get

Get entity details or paginated lists.

```
agent-discogs get <noun> <ref-or-id>... [--flags]
```

One or more refs (at most 10), run in sequence. Text blocks are separated by a blank line; a failing ref reports inline and the others still run (exit 1 if any failed). `--json` with several refs returns a list, with `{"ref": ..., "error": {...}}` items for failures; with one ref the shape is the single document.

**Nouns:**

| Noun | Expected Ref Type | Description |
|------|-------------------|-------------|
| `artist` | `@a` or numeric ID | Artist profile |
| `credits` | `@r` or numeric ID | Credits grouped by role, each person with an artist ref and track scope |
| `identifiers` (alias `ids`) | `@r` or numeric ID | Barcodes, matrix/runout, label codes, rights societies |
| `label` | `@l` or numeric ID | Label profile |
| `master` | `@m` or numeric ID | Master release details |
| `price` | `@r` or numeric ID | Marketplace pricing (requires auth *and* seller settings on the token's account) |
| `release` | `@r` or numeric ID | Full release details with inline artist/label refs, country, release date, tracklist |
| `releases` | `@a` or `@l` (numeric ID = artist) | Artist discography, or a label's catalogue (paginated) |
| `tracklist` | `@r` or numeric ID | Tracklist only (from a release) |
| `versions` | `@m` or numeric ID | Master release versions (paginated) |

**Flags:**

| Flag | Description |
|------|-------------|
| `--json` | JSON output: a compact projection of the text view. Errors become `{"error":{"code",...}}` on stdout |
| `--full` | With `--json`: the raw SDK record instead of the projection |
| `--limit` | Results per page (default: 5) |
| `--page` | Page number (server-side pages: `versions`, `releases` without `--role` / label `--year`) |
| `--after` | Continuation cursor copied from the previous `Next page:` / `Continue scan:` line (`releases --role` and `releases @l... --year`) |
| `-v, --verbose` | `release` only: append notes, credits, and identifiers |
| `-c, --compact` | `release` only: replace the tracklist with `Tracks: N (mm:ss)`; the total appears only when every track has a duration (JSON: `tracks` string instead of `tracklist`) |

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

Manage the HTTP response cache.

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
