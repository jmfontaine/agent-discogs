---
name: core
description: Core agent-discogs usage guide. Read this before running any agent-discogs command. Covers search, refs, navigation between releases/masters/artists/labels, pricing, pagination, and troubleshooting.
allowed-tools: Bash(agent-discogs:*)
---

# agent-discogs

CLI for searching and exploring the Discogs music database. Returns compact text output with a ref system for chaining commands.

## Setup

Requires `DISCOGS_TOKEN` for full access (60 req/min, search, price data):

```bash
export DISCOGS_TOKEN=<your-token>  # discogs.com/settings/developers
```

Without a token: 25 req/min, no search, no price suggestions.

`price` additionally requires that the token's account has filled out its
seller settings (discogs.com/settings/seller) — Discogs returns 404 for price
suggestions otherwise. A token alone covers every other command.

Check status: `agent-discogs status`

## Core Workflow

1. **Search** — find entities by name
   ```bash
   agent-discogs search release "The Downward Spiral" --year 1994
   ```
2. **Inspect** — get full details using refs from search output
   ```bash
   agent-discogs get release @r847868
   ```
3. **Drill down** — tracklist, pricing
   ```bash
   agent-discogs tracks @r847868
   agent-discogs price @r847868
   ```
4. **Explore** — discography, versions
   ```bash
   agent-discogs get versions @m3719 --country US
   ```

## Output Format

- **search** — one row per match: ref, title, year, country, label + catalog number, format, `have N` (how many collectors own it), and `→ @m...` (the release's master). The header echoes the filters you applied. The `[type]` tag appears only on untyped searches.
- **get release** — title, artists `[@a...]`, label `[@l...]` + catalog number, format, country, release date, community stats, market summary, master ref, full tracklist. Refs are always inline.
- **get release --verbose** — the above plus notes, credits, and identifiers.
- **get release --compact** — the above with the tracklist collapsed to `Tracks: 14 (65:01)` (just `Tracks: 14` when a track lacks a duration). Combine with `-v` to compare pressings without paying for tracklists.
- **get credits** — who did what, grouped by role (Producer, Engineer, Mastered By, ...), each person with an `[@a...]` ref and the tracks they worked on.
- **get identifiers** (alias `ids`) — barcodes, matrix/runout etchings, label codes, rights societies. This is what distinguishes pressings that share a catalog number.
- **tracks** — numbered tracklist with durations and per-track artists (for VA releases).
- **price** — price suggestions by condition (Mint, Near Mint, VG+, etc.) and marketplace stats.
- **get versions** — one row per pressing: ref, year, country, label + catalog number, format, `have N`. `--sort released` puts the earliest pressing first; `--year` narrows.
- **get releases @a...** — artist discography: ref, `[type]`, title, year, label, format, role. `--sort year|title|format`, `--desc`.
- **get releases @l...** — label catalogue: ref, title, artist, year, catalog number, format. No server-side filters or sorting (API limit): `--year` is a client-side scan that continues via `--after`, otherwise page with `--page`.
- **get artist** — profile, `Members:` and `Former:` each with `[@a...]` refs. **get label** — profile, `Parent:` and `Sub-labels:` with `[@l...]` refs.

## Common Patterns

| Goal | Commands |
|------|----------|
| Find a specific pressing | `search release "<title>" --year --country` → `get release @r...` |
| Compare pressings | `search master "<title>"` → `get versions @m...` |
| Pick among look-alike rows | Compare catalog number and `have N` in the row itself; `have` is popularity, not identification. Same catno on several rows means variants: see `get release @r...` |
| Release → all its pressings | Copy `→ @m...` from any release row → `get versions @m...` |
| Explore discography | `search artist "<name>"` → `get releases @a... --sort year` |
| Label catalogue | `get releases @l...` — copy `@l...` from a release's `Label:` line |
| Band → member → solo work | `get artist @a...` → copy a `[@a...]` from `Members:` → `get releases @a...` |
| Check price | `search release "<title>"` → `price @r...` |
| Identify by catalog number | `search release --catno "INT-92346"` → `get release @r...` |
| Find by barcode | `search release --barcode "606949235024"` |
| Get original pressing | `search master "<title>"` → `get versions @m... --sort released` → `get release @r...` |
| Narrow release search | `search release "<title>" --artist "<name>"` |
| Get release notes / credits / identifiers together | `get release @r... --verbose` |
| Compare several pressings cheaply | `get release @r... @r... @r... -c` in one command; `tracks @r...` only when the tracklist matters |
| Who produced / engineered / played on this? | `get credits @r...` → follow a `[@a...]` into `get releases @a...` |
| Identify the disc in hand (same catno, several pressings) | `get identifiers @r... @r...` and compare Matrix / Runout |
| Get artist/label refs from a release | `get release @r...` — `[@a...]` and `[@l...]` are always inline |
| VA compilation tracks | `get release @r...` — per-track artists shown automatically |
| Machine-readable output | Add `--json` to `search`, `get`, `tracks`, or `price` |

## Machine-Readable Output

`search`, `get`, `tracks`, and `price` support `--json` (`status`, `cache`, and `skills` are text-only). The JSON is a compact projection of what the text view shows, with refs as strings you can pass straight back: `{"ref":"@r847868","artists":[{"ref":"@a3857","name":"Nine Inch Nails"}],"labels":[{"ref":"@l647","name":"Nothing Records","catno":"92346-2"}],"master":"@m3719",...}`. Empty fields are omitted: test for the key, not for null. `get credits --json` is grouped by role, `{"Producer":[{"ref":"@a...","name":"...","tracks":"1, 2"}],...}`, the same grouping as the text. Lists come wrapped in `{"pagination":{...},"results":[...]}`; the `pagination` keys are always present, and filtered lists carry `pagination.next_cursor` for `--after` (`null` when nothing remains).

```bash
agent-discogs search release "The Downward Spiral" --year 1994 --country US --json
agent-discogs get release @r847868 --json
agent-discogs get release @r847868 --json -v          # adds notes, credits, and identifiers
agent-discogs get release @r847868 --json --full      # raw Discogs record (images, URLs, ~20x larger); only when the projection lacks a field you need
```

With `--json`, errors are a JSON document on stdout and exit code 1: `{"error":{"code":"not_found","message":"Master @m... not found.","hint":"Try: agent-discogs search \"<title>\"","status":404}}`. Branch on `error.code`: `not_found`, `seller_settings_required`, `auth_required`, `forbidden`, `rate_limited` (with `retry_after` seconds when Discogs sends it, plus `limit`/`remaining` for the current minute), `cache_miss` (only under `--cached`), `api_error`, `connection_error`, `invalid_argument`, `unexpected`.

## Anti-Patterns

- **Don't search without a type filter** when you know the entity type.
- **Don't fetch full release details just to check price.** Use `price @r...` directly.
- **Don't paginate through all results.** Narrow with filters first.
- **Don't compute page numbers.** Paste the `Next page:` / `Continue scan:` command printed under a list. Filtered lists (the default search, `releases --role`, `releases @l... --year`) continue with an `--after` cursor; `--page` is rejected there and the error says what to use.
- **Don't guess IDs.** Always search first to find the right entity.
- **Don't use `get versions` on a release ID.** Release rows already show `→ @m...`; use that master ref (smart resolution costs an extra API call).
- **Don't use `--fresh` by default.** Catalogue data an hour old is fine; reserve `--fresh` for `price` when you need current market numbers.

## Error Recovery

- **0 results** — broaden filters (drop `--year`, `--country`), try a different type (`master` instead of `release`), or simplify the query.
- **Auth required** — price suggestions and search require `DISCOGS_TOKEN`. Run `agent-discogs status` to check.
- **"Price data requires seller settings"** — not a bad ref. The release exists, but Discogs only serves price suggestions to accounts with seller settings filled out. Nothing to retry: use `get release @r...` for the `num_for_sale`/`lowest_price` summary instead.
- **Invalid ref** — refs are Discogs IDs and never expire; there is no session. Check the prefix matches the noun (`@r` release, `@m` master, `@a` artist, `@l` label). A bare `@123` is invalid: either add the type letter or pass the raw number `123`.
- **Rate limited** — the error says what is left (`Rate limited: 0/60 requests left this minute.`, `remaining`/`limit` in JSON). Pause ~60s, or continue with `--cached` on refs you already fetched. Authenticated requests get 60/min; unauthenticated get 25/min.
- **Unexpected cost or a stall?** Re-run once with `--debug` to see each request, retries and cache hits on stderr. Do not leave it on.
- **"Continue scan:" instead of "Next page:"** — the filter was sparse and the scan stopped at 5 API calls before filling the page. The next window may also be short or empty; keep pasting the printed command until it disappears (no footer = nothing left to scan). Counts shown as `≤N` are the unfiltered upper bound.

## Refs

Refs encode entity type and Discogs ID: `@a3857` (artist), `@r847868` (release), `@m3719` (master), `@l647` (label). Raw numeric IDs also work.

**Ref chaining:** every entity view embeds refs for the entities it names: `get release` shows `[@a...]` artists and `[@l...]` labels, `get credits` shows `[@a...]` per person, `get artist` shows `[@a...]` members, `get label` shows `[@l...]` parent and sub-labels. Copy them into `get releases`, `get label`, `get artist` without a search.

## Key Concepts

**Master** = canonical album. **Release** = specific pressing. **Version** = a release belonging to a master. Search masters to find all pressings; search releases to find a specific one. See [references/pressings-guide.md](references/pressings-guide.md) for details.

**Formats**, **genres**, **identifiers**, and other Discogs-specific data conventions are documented in [references/discogs-domain.md](references/discogs-domain.md).

## Token Efficiency

Prefer the most specific command: `tracks @r...` over `get release @r...` when you only need the tracklist, and `price @r...` over `get release @r...` when you only need pricing. This reduces output tokens and avoids unnecessary data.

## Budget

Discogs allows 60 requests per minute with a token (25 without), as a moving 60s window shared by every process on this machine. After any command that talks to the API, stderr ends with one line:

```
api: 3 requests · 43/60 left this minute
api: 0 requests · cached
api: 4 requests · 6/60 left this minute ⚠ pause ~60s before uncached calls
```

The count is what this command spent; `43/60` is what Discogs reported after its last request. It is never part of `--json` output. Commands that make no request print nothing.

| Command | Requests |
|---|---|
| `get <noun>`, `tracks` | 1 per ref |
| `price` | 3 per ref; `get versions @r...` 2 (resolve, then list) |
| `search`, `get releases`, `get versions` (server-side pages) | 1 |
| Client-side scans: `search` with `--release-type official\|unofficial` (the default), `get releases --role`, `get releases @l... --year` | up to 5 |
| Anything repeated within 1 hour | 0 (cached) |

At `⚠` (10 or fewer left): pause ~60s before uncached calls, or spend nothing with `--cached`. Unauthenticated runs get 25/min; setting `DISCOGS_TOKEN` is the fix there.

**Try everything with `--cached` first, then spend only on the misses.** `agent-discogs --cached get release @r847868 @r...` serves what the cache has and reports each miss inline as `✗ Not cached: GET ...` (`error.code == "cache_miss"` under `--json`) without touching the API. It matters when several agents share one machine (one cache, one IP budget), and after a `⚠`. The flags precede the command:

```bash
agent-discogs --cached price @r847868     # zero spend; misses fail with cache_miss
agent-discogs --fresh price @r847868      # bypass the cache; the response is not stored
```

## Reference Docs

| Document | Content |
|----------|---------|
| [references/commands.md](references/commands.md) | Full command reference with all flags |
| [references/search-patterns.md](references/search-patterns.md) | Effective search strategies |
| [references/pressings-guide.md](references/pressings-guide.md) | Master/release/version mental model |
| [references/discogs-domain.md](references/discogs-domain.md) | Discogs data model: formats, genres, country, artists, labels, identifiers |
