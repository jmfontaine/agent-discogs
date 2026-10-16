# Command Reference

The commands every task uses: the global flags, `search`, the database nouns of `get`, `status`, `cache` and `skills`. The nouns that read the user's own collection and lists are in `agent-discogs skills get collection --full`, the Marketplace ones (`price`, `get wantlist`, `get inventory`) in `agent-discogs skills get marketplace --full`.

## Global Flags

Global flags precede the command (`agent-discogs --cached price @r847868`).

| Flag | Description |
|------|-------------|
| `--cached` | Serve only from the response cache. A request the cache cannot serve fails with error code `cache_miss` (exit 1, per ref when several are given) before any network I/O. Never spends a request. A cache that cannot be opened fails with `cache_error` instead. A client-side scan reads cached pages until its page is full and fails with `cache_miss` at the first uncached one (cache hits never count against `--max-requests`). The collection and marketplace commands fail so on each request they would send (username, folder list, pages): their skills say which. |
| `--fresh` | Bypass the cache for this command. Every request goes to the API; the response is not stored, so a later run without `--fresh` may still see the older cached entry until its 1h TTL expires. It never opens the cache, so it works even when other commands fail with `cache_error`. Mutually exclusive with `--cached` (exit 2). It re-reads the `--owned` collection and the `--for-sale` Marketplace stats too: use it when current numbers matter. |
| `--debug` | Trace panel on stderr after the budget line: one row per response (path, status, timing or `cache`; a `--cached` miss or a failed connection has no row), scan statistics, cache size (`unknown (no home directory)` when the cache location cannot be resolved), output size with a token estimate, plus the SDK's own log lines in real time. `AGENT_DISCOGS_DEBUG=1` does the same. stdout and the exit code are unchanged. |
| `--color auto\|always\|never` | Color the text output's framing (refs, section labels, the `✗` of an error, the `⚠` budget warning, page counts and the `Next page:` prefix), never a Discogs value. `auto` (the default) colors stdout and stderr separately, each only when it is a terminal, `NO_COLOR` is unset or empty, and `TERM` is not `dumb`; `always` and `never` override that. A pipe gets no color, so piped output is unchanged. If your harness runs commands in a pseudo-terminal, set `NO_COLOR=1` or pass `--color never`. A `--json` command is never colored, stderr included, and neither are `--help` and `skills`. |
| `--help` | Show help |
| `--version` | Show version |

After any command that talks to the API, stderr ends with a budget line such as `api: 3 requests · 43/60 left this minute` (`⚠` when 10 or fewer remain). See the Budget section of SKILL.md (`agent-discogs skills get core`).

The `Next page:` / `Continue scan:` command printed under a list repeats `--cached` or `--fresh` when the list was read under one, and repeats `--max-requests` and `--owned` when they were given (and, for `get collection` and `get inventory`, `--limit`, its filters, `--sort` and `--desc`, plus `--folder` and `--fields` for the collection and `--in-wantlist` for the inventory; for `get lists` and `get wantlist`, `--limit`, plus `--for-sale`, `--max-price` and `--currency` for the wantlist). An inventory username starting with `-` is put after `--`.

### Client-side scans

`search` with `--release-type official` (the default) or `unofficial --format ...`, `get releases @a... --role`, `get releases @l... --year`, `get versions` with `--release-type official` or `unofficial --format ...`, `get collection` with a filter, and `get inventory` with a filter: these filter rows the API cannot filter. They read 100-row pages until `--limit` rows match or the list ends, with no cap by default: a dense filter costs one request, a sparse one over 2,000 results up to 20, plus the text header lookup for `get releases` and `get versions` and the username and folder list for `get collection`. `--max-requests N` (N ≥ 1, one ref, only on these listings; otherwise `invalid_argument` before any request) caps every HTTP send of the command, header lookups included, which run first; cache hits are free. A scan that stops early ends in `Continue scan:` and says why in its header:

- `scan stopped at --max-requests N`: the cap ran out (JSON: `capped: true`, `stop_reason: "max_requests"`). If the header lookup used the whole cap, the header reads `(--max-requests N ran out before the scan started)` with no counts, JSON `total_items` and `total_pages` are `null`, and the continuation starts the scan. Under `--fresh` the header adds `; --fresh sends the header lookup again on every run, so continue with a higher --max-requests`: the lookup is not cached, so the same cap would spend itself on it again.
- `scan stopped: rate limited, retry in Ns`: Discogs answered 429 after at least one page, and the rows read so far are returned, exit 0 (JSON: `stop_reason: "rate_limited"`, `retry_after` when Discogs sent one). A 429 on the first page is the `rate_limited` error.

With a tool-call time limit, pass `--max-requests`.

In a filtered list's JSON `pagination`, `page` counts your pages of `--limit` matches, but `total_items` is the unfiltered total (the text header's `≤N`) and `total_pages` counts the 100-row Discogs pages the scan reads, so page 2 can report `"total_pages":28`. Neither says how many pages of matches remain: continue until `next_cursor` is `null` (in text, until no `Next page:` / `Continue scan:` is printed).

## Text encoding

Text output is UTF-8 when the terminal or pipe is. On a stdout that is not (`PYTHONIOENCODING=ascii`, some Windows pipes), characters it cannot hold print as backslash escapes (`宇多田` → `\u5b87\u591a\u7530`) and the command still succeeds. A pasted `Next page:` command then carries the escapes too, so prefer UTF-8 for non-Latin queries.

## Request costs

| Command | Requests |
|---|---|
| `get artist\|label\|master\|release\|credits\|identifiers\|tracklist`, `tracks` | 1 per ref |
| `get releases @a...\|@l...`, `get versions @m...` (also `--release-type unofficial` without `--format`, filtered by Discogs) | `--json`: 1 per ref; text: 2 (the header name costs one) |
| `get versions @r...` | 2 per ref (resolve the master, then list) |
| Client-side scans: `get releases @a... --role`, `get releases @l... --year`, `get versions @m... --release-type official` (or `unofficial` with `--format`) | `--json`: 1 per 100 rows scanned; text: 1 more (the header name) |
| `search artist\|label`, `search --release-type all`, `search --release-type unofficial` (filtered by Discogs, without `--format`) | 1 |
| Other searches (`--release-type official`, the default, or `unofficial` with `--format`): client-side scan | 1 per 100 rows scanned |
| `search release ... --added-since` (experimental) | 1 per release the search matches in the window (before `--release-type` filtering), plus 1 per 100 search results and 1 to end the scan |
| Anything repeated within 1 hour | 0 (cached) |

The collection and marketplace commands, `--owned` and `get wantlist --for-sale` included, list their costs in the Budget section of their skills.

## search

Search the Discogs database. Discogs documents search as needing credentials (`DISCOGS_TOKEN`, OAuth, or a consumer key and secret); unauthenticated searches currently get answers, but Discogs may refuse one with `auth_required`.

```
agent-discogs search [type] <query> [--flags]
```

**Positional arguments:**

| Argument | Required | Description |
|----------|----------|-------------|
| `type` | No | Entity type: `release`, `master`, `artist`, `label`. If omitted, searches all types. |
| `query` | No | Search query string. Required unless at least one non-blank filter is given (e.g., `--catno`, `--barcode`); otherwise `invalid_argument` before any request. A query starting with `-` goes after `--` (`agent-discogs search --release-type all -- -ambient`); printed `Next page:` commands always put the type and query there. |

**Output:** one row per match: ref, title, year, country, label + catalog number, format, `have N` (Discogs' community count: how many Discogs users own it, not you), and `→ @m...` (the release's master). The header echoes the filters you applied, and the sort when there is one. The `[type]` tag appears only on untyped searches. A result whose type is not artist/label/master/release has no ref: its row reads `id 9001 [playlist] "..."` (`[no type]` when Discogs sends none), with `id` instead of `ref` under `--json`, and the rest of the page is unaffected. Don't guess a prefix for it.

**Flags:**

**`--added-since` (experimental):** `search release ... --added-since YYYY-MM-DD` returns the releases added to Discogs on or after that day (inclusive, from midnight UTC), newest first, through the SDK's experimental `releases_added_since()`. Discogs has no date filter and search results carry no added date, so it walks the undocumented `date_added` search order (100 results a page) and looks up each release, stopping at the first one older than the cutoff. Every row ends in `· added YYYY-MM-DD`, the UTC day the cutoff compares; `--json` gives `{"complete":true,"total":N,"results":[...]}`, each row with `added` (RFC 3339). Cost: one request per release the search matches in the window (before `--release-type` filtering), plus one per 100 search results and one for the release that ends the scan; cached lookups are free, so a repeat within the hour costs nothing. No cap by default; with `--max-requests N` the command stops after N sends, and the text ends with `Stopped at --max-requests N: the result may be incomplete` (`complete: false`). `--limit`, when you pass it, cuts the rows shown, not the requests. `--release-type official` (default) and `unofficial --format ...` filter on the looked-up formats at no extra request; `unofficial` alone is filtered by Discogs, so official releases are never looked up. Needs `search release`; `--sort`, `--desc`, `--page` and `--after` are rejected (`invalid_argument`, no request). A 429 partway fails the command with `rate_limited` (the CLI never waits); rerun after `retry_after`, and the lookups already made come from the cache. A broken date order fails with `bad_response`; those responses are cached, so a plain retry repeats it and only `--fresh` asks Discogs again. Under `--cached` an uncached lookup fails with `cache_miss`. With a tool-call time limit, pass `--max-requests`.

| Flag | Description |
|------|-------------|
| `--artist` | Filter by artist name |
| `--barcode` | Filter by barcode |
| `--catno` | Filter by catalog number |
| `--country` | Filter by country |
| `--desc` | With `--sort`: descending (newest first) |
| `--format` | Filter by format (e.g., "Vinyl", "CD") |
| `--genre` | Filter by genre |
| `--json` | JSON output: a compact projection of the text view, plus `url` on each result: its absolute Discogs web page (never in text). Errors become `{"error":{"code",...}}` on stdout |
| `--full` | With `--json`: the complete record instead of the projection, normalized (consistent field names such as `catalog_number`, RFC 3339 timestamps, `null` for an absent or unparseable timestamp; unmodeled fields as Discogs sends them) |
| `--label` | Filter by label name |
| `--limit` | Results per page, 1-100 (default: 5) |
| `--max-requests` | Cap the command's HTTP sends during a client-side scan (`--release-type official`, or `unofficial` with `--format`), `--added-since`, or the `--owned` collection read; cache hits are free. Default: no cap. Rejected on server-side pages without `--owned`. See Client-side scans above. |
| `--owned` | Mark each release row with the copies in your own collection (needs a token or OAuth): see `agent-discogs skills get collection`. |
| `--added-since` | `search release` only, experimental: releases added on or after `YYYY-MM-DD`, newest first, the whole window. See `--added-since` above. |
| `--page` | Page number, from 1. Only for server-side pages: `--release-type all`, `--release-type unofficial` (without `--format`), or artist/label searches. Rejected on the client-side filtered path. |
| `--after` | Continuation cursor copied from the previous `Next page:` / `Continue scan:` line (client-side filtered searches only: `--release-type official`, or `unofficial` with `--format`) |
| `--release-type` | `official` (default): client-side scan, `≤N` total, continues with `--after`. `unofficial`: filtered by Discogs (`format=Unofficial Release`), exact total, pages with `--page`; with `--format` it falls back to the client-side scan. It returns every unofficial release: bootlegs, counterfeits and pirates alike; for bootlegs only, see Unofficial Releases in the pressings skill (`agent-discogs skills get pressings --full`). `all`: no filter. |
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
agent-discogs search release "The Downward Spiral" --after 2:5   # cursor copied from the previous footer
agent-discogs search release "Pretty Hate Machine" --release-type all
agent-discogs search release "When The Whip Comes Down" --release-type unofficial
agent-discogs search release --artist "Nine Inch Nails" --sort date_added --desc   # newest additions first
agent-discogs search release --artist "Nine Inch Nails" --release-type all --added-since 2026-07-08 --max-requests 60
agent-discogs search release --artist "Nine Inch Nails" --release-type unofficial --added-since 2025-10-09   # unofficial releases (bootlegs, counterfeits, pirates) added in the last year
```

## get

Get entity details or paginated lists.

```
agent-discogs get <noun> <ref-or-id>... [--flags]
```

One or more refs (at most 10), run in sequence. Text blocks are separated by a blank line; a failing ref reports inline and the others still run (exit 1 if any failed). `--json` with several refs returns a list, with `{"ref": ..., "error": {...}}` items for failures; with one ref the shape is the single document.

`collection`, `lists` and `wantlist` are the exceptions: they take no ref (`agent-discogs get collection [--flags]`, `agent-discogs get lists [--flags]`, `agent-discogs get wantlist [--flags]`; a ref is `invalid_argument`) and print one document for your own collection, lists or wantlist. `list` takes list ids, plain numbers only: lists have no ref prefix, so a typed ref such as `@r123` is `invalid_argument`.

`inventory` is the third kind: it takes exactly one Discogs username in place of a ref (`agent-discogs get inventory <username> [--flags]`), the seller's, not yours. Two usernames or a blank one is `invalid_argument`, none is a click usage error (exit 2). A username starting with `-` goes after `--` (`get inventory -- -name`); printed continuations do it for you.

### Nouns

- `artist` (`@a` or numeric ID) — artist profile, with Discogs' markup converted (linked entities as refs, see below); `Members:` and `Former:` each with `[@a...]` refs. `Former:` lists only members Discogs marks inactive; a member with no recorded status is listed under `Members:` (JSON `members`/`former` match). `URLs:` lists linked sites by domain, omitting empty entries.
- `credits` (`@r` or numeric ID) — who did what, grouped by role (Producer, Engineer, Mastered By, ...), each person with an `[@a...]` ref and the tracks they worked on. Credits filed on individual tracks and sub-tracks are included: each person appears once per role with every track the credit covers; no track list means the whole release. `--json --full` gives the release's complete `credits` plus `track_credits` (`position`, `title`, and the complete `extra_artists` for each credited track).
- `identifiers`, alias `ids` (`@r` or numeric ID) — barcodes, matrix/runout etchings, label codes, rights societies. This is what distinguishes pressings that share a catalog number.
- `label` (`@l` or numeric ID) — label profile, with Discogs' markup converted (linked entities as refs, see below); `Parent:` and `Sub-labels:` with `[@l...]` refs. `URLs:` lists linked sites by domain, omitting empty entries.
- `master` (`@m` or numeric ID) — master release details.
- `release` (`@r` or numeric ID) — title, artists `[@a...]`, label `[@l...]` + catalog number, format (one entry per physical format: `2×Vinyl (LP, Album, 180g); CD (Album)`), country, release date, `Added: YYYY-MM-DD` (the day it was added to Discogs, in the timestamp's own offset; JSON `added` is the full RFC 3339 timestamp), community stats, market summary, master ref, full tracklist. Refs are always inline. `--verbose` adds notes, credits, and identifiers. `--compact` collapses the tracklist to `Tracks: 14 (65:01)` (just `Tracks: 14` when a track lacks a duration). A suite or medley split into parts counts as its parts, timed by their sum, or by the suite's own duration when a part is untimed. Combine with `-v` to compare pressings without paying for tracklists.
- `releases` (`@a` or `@l`; a numeric ID is an artist) — paginated. `releases @a...` is the artist discography: ref, `[type]`, title, year, label, format, role; `--sort year|title|format`, `--desc`. `releases @l...` is the label catalogue: ref, title, artist, year, catalog number, format. No server-side filters or sorting there (API limit): `--year` is a client-side scan that continues via `--after`, otherwise page with `--page`.
- `tracklist` (`@r` or numeric ID; shortcut `tracks`) — numbered tracklist with durations and per-track artists (for VA releases). Index tracks (a suite or medley) list their parts indented beneath them; in `--json` an index row has `"type": "index"` and nests its parts under `sub_tracks`.
- `versions` (`@m` or numeric ID) — paginated master release versions, one row per pressing: ref, year, country, label + catalog number, format, `have N` (Discogs community count). `--sort released` puts the earliest pressing first; `--year` narrows; `--owned` marks the ones you own. `--release-type official|unofficial|all` filters on the "Unofficial Release" format description like `search`, but **defaults to `all`** (every version, unofficial ones included). `unofficial` is filtered by Discogs (exact total, `--page`); `official` is a client-side scan (`≤N`, continues via `--after`, takes `--max-requests`), as is `unofficial` with `--format`. "Partially Unofficial" versions count as official.

The other nouns have their own skill: `collection`, `list`, `lists` and `owned` read the user's own collection and lists (`agent-discogs skills get collection`); `inventory`, `price` and `wantlist` cover the Marketplace (`agent-discogs skills get marketplace`).

### Markup in profiles and notes

Artist and label profiles (cut to about 300 characters, never inside a ref), release notes (`-v`) and list descriptions (never cut) convert Discogs' markup, in text and `--json` alike; `--json --full` keeps it as sent.

| Discogs markup | Printed |
|----------------|---------|
| `[a3857]`, `[l647]`, `[r352665]`, `[m3719]`, `[r=352665]`, `[m=3719]` | the ref (`@a3857`, `@l647`, ...): pass it to `get` |
| `[a=Name]`, `[l=Name]` | `Name`, no ref (use `search` to find it) |
| `[url=https://host/path]text[/url]` / `[url]https://host/path[/url]` | `text (host)` / `host` |
| `[g1.2.3]`, `[t=262]` | `RSG §1.2.3`, `Forum Topic #262` |
| `[b]`, `[i]`, `[u]`, `[s]` | their text (bold, italic, underlined, struck through when colored) |
| anything else in brackets, uppercase tags included | unchanged |

Discogs writes links into artist and label profiles and release notes as markup, and the CLI converts it in text and `--json`: a linked artist, label, release or master prints as a real ref (`founder of @l647.`), ready to pass back to `get`. A link by name (`[a=Name]`) prints only the name, with no ref: `search artist "<name>"` finds it. A web link prints as `text (host)`. `--json --full` keeps the markup as Discogs sent it.

### Currency

`price` prints amounts as `EUR 20.00`, the ISO code Discogs returns, never a symbol; under `--json` each suggestion is `{"value":20.0,"currency":"EUR"}` and `market` carries `lowest` and `currency`. The `Market: N for sale from 9.36` line of `release` and `master` is a bare amount in the token account's buyer currency (USD when unauthenticated): Discogs does not name it, so its JSON `market` has no `currency` key. Use `price @r...` when the currency matters.

### Flags

| Flag | Description |
|------|-------------|
| `--json` | JSON output: a compact projection of the text view, plus `url` on `release`, `master`, `artist`, `label`, `list` and each `lists` row: the absolute Discogs web page (never in text; `versions` and `releases` rows only when Discogs sends one, which it does not today; never on list items, `tracklist`, `price`, `credits`, and `identifiers`: use `release` for the link). Errors become `{"error":{"code",...}}` on stdout |
| `--full` | With `--json`: the complete record instead of the projection, normalized (consistent field names such as `catalog_number` and a track's `type`, RFC 3339 timestamps, `null` for an absent or unparseable timestamp; unmodeled fields as Discogs sends them) |
| `--limit` | `releases`, `versions`, `collection`, `lists`, `wantlist`, `inventory`: results per page, 1-100 (default: 5); with a `collection` filter, `wantlist --for-sale` or an `inventory` filter, how many matches to collect |
| `--page` | Page number, from 1 (server-side pages: `versions` without a `--release-type` scan, `collection` without a filter, `lists`, `wantlist`, `inventory` without a filter, `releases` without `--role` / label `--year`); rejected with a `collection` filter, `wantlist --for-sale`, an `inventory` filter and `versions --release-type official` (or `unofficial --format ...`) |
| `--after` | Continuation cursor copied from the previous `Next page:` / `Continue scan:` line (`releases --role`, `releases @l... --year`, `versions --release-type official` or `unofficial --format ...`, `collection` with a filter, `wantlist --for-sale`, and `inventory` with a filter, one ref only) |
| `--max-requests` | `releases --role`, `releases @l... --year`, `versions --release-type official` (or `unofficial --format ...`), `releases` / `versions` / `inventory` with `--owned`, `collection` with a filter, `inventory` with a filter, and `wantlist --for-sale` (3 or more there: the username, a wants page and one want's stats); one ref only: cap the command's HTTP sends, the text header lookup, the `--owned` collection read, the `collection` username and folder list, the `--for-sale` username, wants pages and stats lookups, and the `--in-wantlist` username and wants pages included (the header runs first). Default: no cap. With `--owned` the cap only stops scans and the collection read: the header and a server-side listing page are always sent. In text under `--fresh` the header is sent again on every run, so a cap it uses up ends in an empty capped page whose header says to continue with a higher `--max-requests`. See Client-side scans above. |
| `--owned` | `releases`, `versions` and `inventory` only: mark each release row with the copies in your own collection. Master, artist and label rows (an artist discography lists masters) get no mark. Needs a token or OAuth; the marks, their JSON and their cost: `agent-discogs skills get collection`. |
| `-v, --verbose` | `release` only: append notes, credits, and identifiers |
| `-c, --compact` | `release` only: replace the tracklist with `Tracks: N (mm:ss)`; an index track counts as its parts, timed by their sum or, when a part is untimed, by the index track's own duration; otherwise the total appears only when every track has a duration (JSON: `tracks` string instead of `tracklist`) |

### Additional flags for `releases`

| Flag | Description |
|------|-------------|
| `--role` | Artist discographies (`releases @a...`) only: filter by credit role (e.g., "Main", "Remix", "Producer"). Case-insensitive substring match, client-side. |
| `--sort` | Artist discographies (`releases @a...`) only: `year`, `title`, or `format` (server-side). Label catalogues have no sorting. |
| `--desc` | With `--sort` (so `releases @a...` only): descending |
| `--year` | Label catalogues only: keep releases from this year. Client-side scan (the API has no filters or sorting for `/labels/{id}/releases`), continues via `--after`. Artist discographies: use `--sort year` instead. |

### Additional flags for `versions`

| Flag | Description |
|------|-------------|
| `--country` | Filter versions by country |
| `--format` | Filter versions by format |
| `--label` | Filter versions by label |
| `--year` | Filter versions by release year |
| `--sort` | `released`, `title`, `format`, `label`, `catno`, or `country` (server-side) |
| `--desc` | With `--sort`: descending |
| `--release-type` | `all` (default, unlike `search`, whose default is `official`): every version, unofficial ones included. `unofficial`: the versions whose format lists "Unofficial Release", filtered by Discogs (`format=Unofficial Release`), exact total, pages with `--page`; with `--format` a client-side scan instead. `official`: drops them in a client-side scan, `≤N` total, continues via `--after`, takes `--max-requests` (`--page` is `invalid_argument`). The match is on the whole description, so "Partially Unofficial" counts as official. Bootlegs, counterfeits and pirates all carry it: see Unofficial Releases in the pressings skill (`agent-discogs skills get pressings --full`). |

### Which flags apply where

Each flag applies only to the nouns below. Any other flag fails with `invalid_argument` before a request is made, even one set to its default value (`--limit 5`). `--after` and `--max-requests` also need exactly one ref: a cursor continues one listing, and a capped listing continues one ref at a time.

| Noun | Flags besides `--json` / `--full` |
|------|-----------------------------------|
| `release` | `-v, --verbose`, `-c, --compact` |
| `releases @a...` | `--limit`, `--page`, `--role`, `--sort`, `--desc`, `--owned`, `--after` and `--max-requests` (with `--role` or `--owned`) |
| `releases @l...` | `--limit`, `--page`, `--year`, `--owned`, `--after` and `--max-requests` (with `--year` or `--owned`) |
| `versions` | `--limit`, `--page`, `--country`, `--format`, `--label`, `--year`, `--sort`, `--desc`, `--release-type`, `--owned`, `--after` (with a `--release-type` scan) and `--max-requests` (with a `--release-type` scan or `--owned`) |
| `collection` | no ref; `--limit`, `--page`, `--sort`, `--desc`, `--format`, `--genre`, `--style`, `--year`, `--label`, `--artist`, `--title`, `--folder`, `--fields`, `--after` and `--max-requests` (with a filter, not `--folder` or `--fields` alone); or `--value` alone |
| `lists` | no ref; `--limit`, `--page` |
| `wantlist` | no ref; `--limit`, `--page`, `--for-sale`, `--max-price` and `--currency` (both with `--for-sale`), `--after` and `--max-requests` (with `--for-sale`) |
| `inventory` | one username, not a ref; `--limit`, `--page`, `--sort`, `--desc`, `--owned`, `--condition`, `--sleeve`, `--ships-from`, `--in-wantlist`, `--max-price` and `--currency` (each needs the other), `--after` (with a filter) and `--max-requests` (with a filter or `--owned`) |
| `artist`, `credits`, `identifiers`, `label`, `list`, `master`, `owned`, `price`, `tracklist` | none |

### Smart resolution

`get versions @r847868` where `@r847868` is a release will auto-resolve to the release's master_id and fetch versions. Errors with a hint if the release has no master.

### Examples

```bash
agent-discogs get release @r847868
agent-discogs get release 847868
agent-discogs get artist @a3857
agent-discogs get master @m3719
agent-discogs get label @l647
agent-discogs get tracklist @r847868
agent-discogs get credits @r847868
agent-discogs get identifiers @r847868
agent-discogs get ids @r847868
agent-discogs get releases @a3857
agent-discogs get releases @a3857 --page 2 --limit 10
agent-discogs get releases @a3857 --role Remix
agent-discogs get releases @a3857 --role Remix --after 2:112   # cursor copied from the previous footer
agent-discogs get releases @a3857 --role Remix --max-requests 5   # at most 5 requests, header lookup included
agent-discogs get releases @a3857 --sort year --desc
agent-discogs get releases @l647                       # label catalogue
agent-discogs get releases @l647 --year 1999           # client-side scan; continue with the printed --after
agent-discogs get versions @m3719
agent-discogs get versions @m3719 --country US --format "Vinyl"
agent-discogs get versions @m3719 --year 1994 --sort released
agent-discogs get versions @m3719 --release-type official --limit 5 --json   # client-side scan; continue with next_cursor
agent-discogs get versions @m3719 --release-type unofficial   # filtered by Discogs, pages with --page
agent-discogs get release @r847868 --verbose
```

## status

Show version, auth mode, and cache location. No API call, and the cache is never opened. `Auth:` reads `token (authenticated)`, `oauth (authenticated)`, `consumer (authenticated app, not a user: no price suggestions)`, or `none (unauthenticated, 25 req/min)`: the credentials discogs-sdk picked from `DISCOGS_TOKEN`, then all four OAuth variables (`DISCOGS_CONSUMER_KEY`, `DISCOGS_CONSUMER_SECRET`, `DISCOGS_ACCESS_TOKEN`, `DISCOGS_ACCESS_TOKEN_SECRET`), then the consumer key and secret alone.

```bash
agent-discogs status
```

## cache

Manage the HTTP response cache. It lives at `$XDG_CACHE_HOME/agent-discogs` when `XDG_CACHE_HOME` is set to an absolute path, and at `~/.cache/agent-discogs` otherwise (an empty or relative value is ignored). `agent-discogs status` prints the path in use.

```bash
agent-discogs cache clear          # prints "Cache cleared."
agent-discogs cache clear --json   # prints {"cleared":true}
```

It reports success only when every entry was purged. A cache that cannot be opened, or a purge that fails (the database is locked by another process, read-only, or damaged), exits 1 with `cache_error`: text on stderr, or the JSON error envelope on stdout under `--json`. The database is left exactly as it was; nothing is deleted. The hint says what to do: retry once the other process is done, fix the permissions, or delete the file by hand.

## skills

Print the agent guide bundled with the installed package, so it always matches the version you run. No API call.

```bash
agent-discogs skills                     # list bundled skills, tagged with the installed version (same as `skills list`)
agent-discogs skills list --json         # [{"name":...,"description":...,"version":...}], one object per skill, never colored
agent-discogs skills get core            # this guide's SKILL.md
agent-discogs skills get core --full     # SKILL.md followed by every references/*.md
agent-discogs skills get marketplace     # another skill: collection, marketplace or pressings
agent-discogs skills path                # directory holding the bundled skills
agent-discogs skills path core           # directory of one skill
```

`skills get <name> --full` appends only that skill's own references. An unknown skill name prints `✗ Unknown skill '<name>'. Available: collection, core, marketplace, pressings` on stderr and exits 1.

## Aliases

Each alias takes the arguments and flags of its command.

| Alias | Equivalent |
|-------|------------|
| `find`, `query` | `search` |
| `fetch`, `show` | `get` |
| `tracks @r847868 ...` | `get tracklist @r847868 ...` |
| `price @r847868 ...` | `get price @r847868 ...` |
