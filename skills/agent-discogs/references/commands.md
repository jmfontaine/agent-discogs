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
| `--json` | Output raw JSON |
| `--label` | Filter by label name |
| `--limit` | Results per page (default: 5) |
| `--page` | Page number (default: 1) |
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
agent-discogs search release "The Downward Spiral" --page 2 --limit 10
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
| `label` | `@l` or numeric ID | Label profile |
| `master` | `@m` or numeric ID | Master release details |
| `price` | `@r` or numeric ID | Marketplace pricing (requires auth) |
| `release` | `@r` or numeric ID | Full release details with tracklist |
| `releases` | `@a` or numeric ID | Artist discography (paginated) |
| `tracklist` | `@r` or numeric ID | Tracklist only (from a release) |
| `versions` | `@m` or numeric ID | Master release versions (paginated) |

**Flags for paginated nouns (releases, versions):**

| Flag | Description |
|------|-------------|
| `--json` | Output raw JSON |
| `--limit` | Results per page (default: 5) |
| `--page` | Page number (default: 1) |

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

**Smart resolution:** `get versions @r367113` where `@r367113` is a release will auto-resolve to the release's master_id and fetch versions. Errors with a hint if the release has no master.

**Examples:**

```bash
agent-discogs get release @r367113
agent-discogs get release 367113
agent-discogs get artist @a3857
agent-discogs get master @m4917
agent-discogs get label @l2919
agent-discogs get tracklist @r367113
agent-discogs get price @r367113
agent-discogs get releases @a3857
agent-discogs get releases @a3857 --page 2 --limit 10
agent-discogs get releases @a3857 --role Remix
agent-discogs get versions @m4917
agent-discogs get versions @m4917 --country US --format "Vinyl"
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
| `agent-discogs tracks @r367113` | `agent-discogs get tracklist @r367113` |
| `agent-discogs price @r367113` | `agent-discogs get price @r367113` |
