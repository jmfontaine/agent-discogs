# Command Reference

## Global Flags

Global flags precede the command (`agent-discogs --cached price @r847868`).

| Flag | Description |
|------|-------------|
| `--cached` | Serve only from the response cache. A request the cache cannot serve fails with error code `cache_miss` (exit 1, per ref when several are given) before any network I/O. Never spends a request. A cache that cannot be opened fails with `cache_error` instead. A client-side scan reads cached pages until its page is full and fails with `cache_miss` at the first uncached one (cache hits never count against `--max-requests`). An `--owned` read fails the same way on the username or a collection page the cache does not hold. |
| `--fresh` | Bypass the cache for this command. Every request goes to the API; the response is not stored, so a later run without `--fresh` may still see the older cached entry until its 1h TTL expires. It never opens the cache, so it works even when other commands fail with `cache_error`. Mutually exclusive with `--cached` (exit 2). `--owned` then re-reads your collection from Discogs (a record added within the last hour can otherwise still show as not owned). |
| `--debug` | Trace panel on stderr after the budget line: one row per response (path, status, timing or `cache`; a `--cached` miss or a failed connection has no row), scan statistics, cache size (`unknown (no home directory)` when the cache location cannot be resolved), output size with a token estimate, plus the SDK's own log lines in real time. `AGENT_DISCOGS_DEBUG=1` does the same. stdout and the exit code are unchanged. |
| `--color auto\|always\|never` | Color the text output's framing (refs, section labels, the `✗` of an error, the `⚠` budget warning, page counts and the `Next page:` prefix), never a Discogs value. `auto` (the default) colors stdout and stderr separately, each only when it is a terminal, `NO_COLOR` is unset or empty, and `TERM` is not `dumb`; `always` and `never` override that. A pipe gets no color, so piped output is unchanged. If your harness runs commands in a pseudo-terminal, set `NO_COLOR=1` or pass `--color never`. A `--json` command is never colored, stderr included, and neither are `--help` and `skills`. |
| `--help` | Show help |
| `--version` | Show version |

After any command that talks to the API, stderr ends with a budget line such as `api: 3 requests · 43/60 left this minute` (`⚠` when 10 or fewer remain). See the Budget section of the core skill.

The `Next page:` / `Continue scan:` command printed under a list repeats `--cached` or `--fresh` when the list was read under one, so the next page follows the same cache policy, and repeats `--max-requests` and `--owned` when they were given.

**`bad_response` (exit 1):** Discogs answered with something the CLI cannot read: a non-JSON body (a gateway error page), a record that does not fit the SDK's models, or a malformed results page (`"results" is an object, expected an array`). The message names the problem (a validation failure names its first failing field, never the value), bounded to one line. It is never caused by your arguments, so do not change the command: retry in a moment. Such a response is not cached, so a plain retry asks Discogs again, with one exception: a price guide whose conditions are malformed is cached before they are read, so if the same error repeats, rerun with `--fresh`.

**`auth_required` (401, exit 1):** the message names the credentials that were sent, in the mode `status` reports. The hint says what to check, then `agent-discogs status shows the credentials in use.` Relay it to the user, who fixes the credentials; retrying the same command will not help.

| Mode | Message | Hint (before the `status` pointer) |
|------|---------|------------------------------------|
| `token` | `Authentication failed with DISCOGS_TOKEN.` | Check the token, or generate a new one at discogs.com/settings/developers. |
| `consumer` | `Authentication failed with the consumer key/secret (DISCOGS_CONSUMER_KEY, DISCOGS_CONSUMER_SECRET).` | Check both at discogs.com/settings/developers. They identify an app, not a user: price suggestions need DISCOGS_TOKEN or OAuth. |
| `oauth` | `Authentication failed with OAuth (DISCOGS_CONSUMER_KEY, DISCOGS_CONSUMER_SECRET, DISCOGS_ACCESS_TOKEN, DISCOGS_ACCESS_TOKEN_SECRET).` | Check all four; the access token may have been revoked. |
| `none` | `Authentication failed: no credentials are set.` | Set token: export DISCOGS_TOKEN=<token> (discogs.com/settings/developers). |

**`auth_required` before any request (`--owned`, `get owned`):** these read the authenticated user's own collection, so they need `token` or `oauth` in `status`. Under `consumer` or `none` they fail without sending anything: message `--owned reads your own Discogs account, but no credentials are set.` (or `... but a consumer key/secret identifies an app, not a user.`; `get owned reads ...` for the noun), hint `Set a token: export DISCOGS_TOKEN=<token> (discogs.com/settings/developers), or use OAuth (DISCOGS_CONSUMER_KEY, DISCOGS_CONSUMER_SECRET, DISCOGS_ACCESS_TOKEN, DISCOGS_ACCESS_TOKEN_SECRET). agent-discogs status shows the credentials in use.` No `status` field in the JSON envelope. A 403 stays the generic `forbidden`.

**`rate_limited` (429, exit 1):** the message says what is left of the minute (`Rate limited: 0/60 requests left this minute.`; `Rate limit exceeded.` when Discogs sent no rate-limit headers), with `limit`/`remaining` in JSON when Discogs sends them. The hint starts with the wait (`Retry in 30s.`, `retry_after` in JSON; `Wait a moment and retry.` without a usable `Retry-After`), then the limit that applies:

- With credentials, the limit Discogs reported for them, e.g. `Discogs reported 60 req/min for the credentials in use (oauth).`, or `Discogs allows 60 req/min for the credentials in use (oauth).` when the 429 carried no rate-limit headers. Setting `DISCOGS_TOKEN` is never suggested here: wait, or continue with `--cached`.
- Without any credentials, `60 req/min with DISCOGS_TOKEN, 25 without.`

**Client-side scans:** `search` with `--release-type official` (the default) or `unofficial --format ...`, `get releases @a... --role`, and `get releases @l... --year` filter rows the API cannot filter. They read 100-row pages until `--limit` rows match or the list ends, with no cap by default: a dense filter costs one request, a sparse one over 2,000 results up to 20, plus the text header lookup for `get releases`. `--max-requests N` (N ≥ 1, one ref, only on these listings; otherwise `invalid_argument` before any request) caps every HTTP send of the command, header lookups included, which run first; cache hits are free. A scan that stops early ends in `Continue scan:` and says why in its header:

- `scan stopped at --max-requests N`: the cap ran out (JSON: `capped: true`, `stop_reason: "max_requests"`). If the header lookup used the whole cap, the header reads `(--max-requests N ran out before the scan started)` with no counts, JSON `total_items` and `total_pages` are `null`, and the continuation starts the scan. Under `--fresh` the header adds `; --fresh sends the header lookup again on every run, so continue with a higher --max-requests`: the lookup is not cached, so the same cap would spend itself on it again.
- `scan stopped: rate limited, retry in Ns`: Discogs answered 429 after at least one page, and the rows read so far are returned, exit 0 (JSON: `stop_reason: "rate_limited"`, `retry_after` when Discogs sent one). A 429 on the first page is the `rate_limited` error.

With a tool-call time limit, pass `--max-requests`.

In a filtered list's JSON `pagination`, `page` counts your pages of `--limit` matches, but `total_items` is the unfiltered total (the text header's `≤N`) and `total_pages` counts the 100-row Discogs pages the scan reads, so page 2 can report `"total_pages":28`. Neither says how many pages of matches remain: continue until `next_cursor` is `null` (in text, until no `Next page:` / `Continue scan:` is printed).

**`--owned` collection read:** `--owned` (on `search`, `get versions`, `get releases`) marks release rows with the copies you own. After the listing page it asks for the username (1 request), then reads your whole collection, the "All" folder, at 100 records per request, once per command (every response cached 1h, so a rerun is free). `--max-requests N` counts every send of the command (the text header, the listing page, the username and the collection pages) but only stops scans and the collection read: the header and a server-side listing page are always sent, header first, then the username and collection pages are asked only while the cap allows. `get versions @m... --owned --max-requests 1` therefore sends the header and the listing (2 sends) and reads no collection; with the cap already spent before the username, nothing is asked (`records_read: 0`, `total_records: null`). Rows the read did not reach say `· owned ?` (JSON `owned: null`) and never `not owned`; a count found in the part read may be short. A note line before the footer gives the cause: `Owned: the collection read stopped at --max-requests 2 (100 of 250 records read): "owned ?" rows are not in the part read, and a count may be short.` or `... the collection read was rate limited, retry in 30s (100 of 250 records read): ...`. A 429 on a later collection page keeps the records read. A 429 on the username or the first collection page never fails the command: no collection is read and every release row is `owned ?`, with `collection` = `{"complete":false,"records_read":0,"total_records":null,"stop_reason":"rate_limited","retry_after":N}`. Likewise, when the listing's own client-side scan stopped on a 429 (`pagination.stop_reason: "rate_limited"`), the collection is not read at all (no request) and the rows are `owned ?` with the listing's `retry_after`. See `--owned` under search.


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
| `--owned` | Mark each release row with the copies in your own collection (needs a token or OAuth). See `--owned` below. |
| `--added-since` | `search release` only, experimental: releases added on or after `YYYY-MM-DD`, newest first, the whole window. See `--added-since` above. |
| `--page` | Page number, from 1. Only for server-side pages: `--release-type all`, `--release-type unofficial` (without `--format`), or artist/label searches. Rejected on the client-side filtered path. |
| `--after` | Continuation cursor copied from the previous `Next page:` / `Continue scan:` line (client-side filtered searches only: `--release-type official`, or `unofficial` with `--format`) |
| `--release-type` | `official` (default): client-side scan, `≤N` total, continues with `--after`. `unofficial`: filtered by Discogs (`format=Unofficial Release`), exact total, pages with `--page`; with `--format` it falls back to the client-side scan. It returns every unofficial release: bootlegs, counterfeits and pirates alike; for bootlegs only, see [Unofficial Releases](discogs-domain.md#unofficial-releases-bootlegs-counterfeits-pirates). `all`: no filter. |
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
agent-discogs search release --artist "Nine Inch Nails" --year 1994 --release-type unofficial --limit 100 --owned
```

**`--owned`:** `search ... --owned` (an untyped search or `search release` that returns release rows) ends each release row in `· owned ×2` (always with the count, also for one copy), `· not owned`, or `· owned ?` (not in the part of the collection read; see the `--owned` collection read above). Master, artist and label rows get no mark: a collection holds releases and Discogs does not document a master id on its records, so `search artist|label|master ... --owned` and `--owned` with `--added-since` fail with `invalid_argument` before any request, and a page with no release row reads nothing. `--json` gives each release row `owned` (an integer, `0` when not owned, or `null`) and the document a `collection` object: `{"complete": true, "records_read": 250, "total_records": 250}`, plus `stop_reason` (`max_requests` or `rate_limited`) and `retry_after` when the read stopped early; a page with no release row has neither. `--json --full` adds `owned` to the SDK record. The row's `have N` is Discogs' community count (how many Discogs users own the release); `owned` is your own. Cost: the listing, plus 1 for the username and 1 per 100 collection records, free for an hour; `--max-requests N` counts all of it but stops only scans and the collection read. `Next page:` / `Continue scan:` repeats `--owned`. Under `--cached`, any request not cached fails with `cache_miss`.

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
| `artist` | `@a` or numeric ID | Artist profile, with Discogs' markup converted (linked entities as refs, see below); `URLs:` lists linked sites by domain, omitting empty entries |
| `credits` | `@r` or numeric ID | Release, track, and sub-track credits grouped by role, each person once per role with an artist ref and track scope (none = whole release) |
| `identifiers` (alias `ids`) | `@r` or numeric ID | Barcodes, matrix/runout, label codes, rights societies |
| `label` | `@l` or numeric ID | Label profile, with Discogs' markup converted (linked entities as refs, see below); `URLs:` lists linked sites by domain, omitting empty entries |
| `master` | `@m` or numeric ID | Master release details |
| `owned` | `@r` or numeric ID | Your own copies of a release: `@r352665 owned ×2: Vinyl, Uncategorized` or `@r847868 not owned`. JSON `{"ref":"@r352665","owned":2,"folders":["Vinyl","Uncategorized"]}` / `{"ref":"@r847868","owned":0}`; `--json --full` is `{"instances":[...]}` (the SDK's collection records, folders by id; no folder-list request). A copy in a folder Discogs does not name adds no folder name; with no names at all the text is `@r352665 owned ×1` (no colon) and JSON has no `folders`. Cost: 1 for the username, 1 per ref, 1 for the folder list when any ref is owned. Needs a token or OAuth (`auth_required` before any request otherwise). Takes only `--json` / `--full`. Several refs behave as for the other nouns |
| `price` | `@r` or numeric ID | Marketplace pricing (requires a token or OAuth *and* seller settings on that account), each amount with its ISO currency code |
| `release` | `@r` or numeric ID | Full release details with inline artist/label refs, country, release date, `Added: YYYY-MM-DD` (day added to Discogs, in the timestamp's own offset; JSON `added`, RFC 3339), tracklist |
| `releases` | `@a` or `@l` (numeric ID = artist) | Artist discography, or a label's catalogue (paginated) |
| `tracklist` | `@r` or numeric ID | Tracklist only (from a release); an index track's parts are indented beneath it (JSON: `"type": "index"` with `sub_tracks`) |
| `versions` | `@m` or numeric ID | Master release versions (paginated) |

**Markup in profiles and notes:** artist and label profiles (cut to about 300 characters, never inside a ref) and release notes (`-v`) convert Discogs' markup, in text and `--json` alike; `--json --full` keeps it as sent.

| Discogs markup | Printed |
|----------------|---------|
| `[a3857]`, `[l647]`, `[r352665]`, `[m3719]`, `[r=352665]`, `[m=3719]` | the ref (`@a3857`, `@l647`, ...): pass it to `get` |
| `[a=Name]`, `[l=Name]` | `Name`, no ref (use `search` to find it) |
| `[url=https://host/path]text[/url]` / `[url]https://host/path[/url]` | `text (host)` / `host` |
| `[g1.2.3]`, `[t=262]` | `RSG §1.2.3`, `Forum Topic #262` |
| `[b]`, `[i]`, `[u]`, `[s]` | their text (bold, italic, underlined, struck through when colored) |
| anything else in brackets, uppercase tags included | unchanged |

**Currency:** `price` prints amounts as `EUR 20.00`, the ISO code Discogs returns, never a symbol; under `--json` each suggestion is `{"value":20.0,"currency":"EUR"}` and `market` carries `lowest` and `currency`. The `Market: N for sale from 9.36` line of `release` and `master` is a bare amount in the token account's buyer currency (USD when unauthenticated): Discogs does not name it, so its JSON `market` has no `currency` key. Use `price @r...` when the currency matters.

**Flags:**

| Flag | Description |
|------|-------------|
| `--json` | JSON output: a compact projection of the text view, plus `url` on `release`, `master`, `artist`, and `label`: the absolute Discogs web page (never in text; `versions` and `releases` rows only when Discogs sends one, which it does not today; never on `tracklist`, `price`, `credits`, and `identifiers`: use `release` for the link). Errors become `{"error":{"code",...}}` on stdout |
| `--full` | With `--json`: the complete record instead of the projection, normalized (consistent field names such as `catalog_number` and a track's `type`, RFC 3339 timestamps, `null` for an absent or unparseable timestamp; unmodeled fields as Discogs sends them) |
| `--limit` | `releases`, `versions` only: results per page, 1-100 (default: 5) |
| `--page` | Page number, from 1 (server-side pages: `versions`, `releases` without `--role` / label `--year`) |
| `--after` | Continuation cursor copied from the previous `Next page:` / `Continue scan:` line (`releases --role` and `releases @l... --year`, one ref only) |
| `--max-requests` | `releases --role`, `releases @l... --year`, and `releases` / `versions` with `--owned`; one ref only: cap the command's HTTP sends, the text header lookup and the `--owned` collection read included (the header runs first). Default: no cap. With `--owned` the cap only stops scans and the collection read: the header and a server-side listing page are always sent. In text under `--fresh` the header is sent again on every run, so a cap it uses up ends in an empty capped page whose header says to continue with a higher `--max-requests`. See Client-side scans above. |
| `--owned` | `releases` and `versions` only: mark each release row with the copies in your own collection (`· owned ×2`, `· not owned`, `· owned ?`; JSON `owned` and a `collection` object). Master, artist and label rows (an artist discography lists masters) get no mark. Needs a token or OAuth. Cost: 1 for the username + 1 per 100 collection records, once per command, free for an hour; `--max-requests` counts it with the listing but stops only the collection read. `have N` on a row is the Discogs community count, `owned` is yours. See the `--owned` collection read above, and `--owned` under search. |
| `-v, --verbose` | `release` only: append notes, credits, and identifiers |
| `-c, --compact` | `release` only: replace the tracklist with `Tracks: N (mm:ss)`; an index track counts as its parts, timed by their sum or, when a part is untimed, by the index track's own duration; otherwise the total appears only when every track has a duration (JSON: `tracks` string instead of `tracklist`) |

**Additional flags for `releases`:**

| Flag | Description |
|------|-------------|
| `--role` | Artist discographies (`releases @a...`) only: filter by credit role (e.g., "Main", "Remix", "Producer"). Case-insensitive substring match, client-side. |
| `--sort` | Artist discographies (`releases @a...`) only: `year`, `title`, or `format` (server-side). Label catalogues have no sorting. |
| `--desc` | With `--sort` (so `releases @a...` only): descending |
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

**Which flags apply where:** each flag applies only to the nouns below. Any other flag fails with `invalid_argument` before a request is made, even one set to its default value (`--limit 5`). `--after` and `--max-requests` also need exactly one ref: a cursor continues one listing, and a capped listing continues one ref at a time.

| Noun | Flags besides `--json` / `--full` |
|------|-----------------------------------|
| `release` | `-v, --verbose`, `-c, --compact` |
| `releases @a...` | `--limit`, `--page`, `--role`, `--sort`, `--desc`, `--owned`, `--after` and `--max-requests` (with `--role` or `--owned`) |
| `releases @l...` | `--limit`, `--page`, `--year`, `--owned`, `--after` and `--max-requests` (with `--year` or `--owned`) |
| `versions` | `--limit`, `--page`, `--country`, `--format`, `--label`, `--year`, `--sort`, `--desc`, `--owned` and `--max-requests` (with `--owned`) |
| `artist`, `credits`, `identifiers`, `label`, `master`, `owned`, `price`, `tracklist` | none |

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
agent-discogs get releases @a3857 --role Remix --after 2:112   # cursor copied from the previous footer
agent-discogs get releases @a3857 --role Remix --max-requests 5   # at most 5 requests, header lookup included
agent-discogs get releases @a3857 --sort year --desc
agent-discogs get releases @l647                       # label catalogue
agent-discogs get releases @l647 --year 1999           # client-side scan; continue with the printed --after
agent-discogs get versions @m3719
agent-discogs get versions @m3719 --country US --format "Vinyl"
agent-discogs get versions @m3719 --year 1994 --sort released
agent-discogs get release @r847868 --verbose
agent-discogs get versions @m3719 --country US --owned
agent-discogs get releases @a3857 --role Main --owned --max-requests 6
agent-discogs get owned @r847868 @r352665
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
agent-discogs skills                   # list bundled skills, tagged with the installed version (same as `skills list`)
agent-discogs skills get core          # this guide's SKILL.md
agent-discogs skills get core --full   # SKILL.md followed by every references/*.md
agent-discogs skills path              # directory holding the bundled skills
agent-discogs skills path core         # directory of one skill
```

An unknown skill name prints `✗ Unknown skill '<name>'. Available: core` on stderr and exits 1.

## Aliases

| Alias | Equivalent |
|-------|------------|
| `agent-discogs find ...` | `agent-discogs search ...` |
| `agent-discogs query ...` | `agent-discogs search ...` |
| `agent-discogs fetch ...` | `agent-discogs get ...` |
| `agent-discogs show ...` | `agent-discogs get ...` |
| `agent-discogs tracks @r847868 ...` | `agent-discogs get tracklist @r847868 ...` |
| `agent-discogs price @r847868 ...` | `agent-discogs get price @r847868 ...` |
