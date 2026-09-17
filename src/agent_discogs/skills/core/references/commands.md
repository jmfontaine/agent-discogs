# Command Reference

## Global Flags

| Flag | Description |
|------|-------------|
| `--help` | Show help |
| `--version` | Show version |


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
agent-discogs get <noun> <ref-or-id> [--flags]
```

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
| `releases` | `@a` or numeric ID | Artist discography (paginated) |
| `tracklist` | `@r` or numeric ID | Tracklist only (from a release) |
| `versions` | `@m` or numeric ID | Master release versions (paginated) |

**Flags:**

| Flag | Description |
|------|-------------|
| `--json` | JSON output: a compact projection of the text view. Errors become `{"error":{"code",...}}` on stdout |
| `--full` | With `--json`: the raw SDK record instead of the projection |
| `--limit` | Results per page (default: 5) |
| `--page` | Page number (server-side pages: `versions`, `releases` without `--role`) |
| `--after` | Continuation cursor copied from the previous `Next page:` / `Continue scan:` line (`releases --role` only) |
| `-v, --verbose` | `release` only: append notes, credits, and identifiers |
| `-c, --compact` | `release` only: replace the tracklist with `Tracks: N (mm:ss)`; the total appears only when every track has a duration (JSON: `tracks` string instead of `tracklist`) |

**Additional flags for `releases`:**

| Flag | Description |
|------|-------------|
| `--role` | Filter by credit role (e.g., "Main", "Remix", "Producer"). Case-insensitive substring match. |

**Additional flags for `versions`:**

| Flag | Description |
|------|-------------|
| `--country` | Filter versions by country |
| `--format` | Filter versions by format |
| `--label` | Filter versions by label |

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
agent-discogs get versions @m3719
agent-discogs get versions @m3719 --country US --format "Vinyl"
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
| `agent-discogs tracks @r847868` | `agent-discogs get tracklist @r847868` |
| `agent-discogs price @r847868` | `agent-discogs get price @r847868` |
