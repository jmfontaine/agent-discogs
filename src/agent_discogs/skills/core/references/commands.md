# Command Reference

## Global Flags

Global flags precede the command (`agent-discogs --cached price @r847868`).

| Flag | Description |
|------|-------------|
| `--cached` | Serve only from the response cache. A request the cache cannot serve fails with error code `cache_miss` (exit 1, per ref when several are given) before any network I/O. Never spends a request. A cache that cannot be opened fails with `cache_error` instead. A client-side scan reads cached pages until its page is full and fails with `cache_miss` at the first uncached one (cache hits never count against `--max-requests`). An `--owned` read fails the same way on the username or a collection page the cache does not hold, and `get collection` on the username, the folder list or a page. `get wantlist` fails so on the username or a wants page; under `--for-sale`, a want whose Marketplace stats are not cached becomes a row with its own `cache_miss` error and the command exits 1. `get inventory` fails so on an inventory page, and with `--in-wantlist` also on the username or a wants page; `get inventory` alone sends no username request. |
| `--fresh` | Bypass the cache for this command. Every request goes to the API; the response is not stored, so a later run without `--fresh` may still see the older cached entry until its 1h TTL expires. It never opens the cache, so it works even when other commands fail with `cache_error`. Mutually exclusive with `--cached` (exit 2). `--owned` then re-reads your collection from Discogs (a record added within the last hour can otherwise still show as not owned), and `get wantlist --for-sale` re-reads every want's Marketplace stats (1 request each): use it when current numbers matter. |
| `--debug` | Trace panel on stderr after the budget line: one row per response (path, status, timing or `cache`; a `--cached` miss or a failed connection has no row), scan statistics, cache size (`unknown (no home directory)` when the cache location cannot be resolved), output size with a token estimate, plus the SDK's own log lines in real time. `AGENT_DISCOGS_DEBUG=1` does the same. stdout and the exit code are unchanged. |
| `--color auto\|always\|never` | Color the text output's framing (refs, section labels, the `✗` of an error, the `⚠` budget warning, page counts and the `Next page:` prefix), never a Discogs value. `auto` (the default) colors stdout and stderr separately, each only when it is a terminal, `NO_COLOR` is unset or empty, and `TERM` is not `dumb`; `always` and `never` override that. A pipe gets no color, so piped output is unchanged. If your harness runs commands in a pseudo-terminal, set `NO_COLOR=1` or pass `--color never`. A `--json` command is never colored, stderr included, and neither are `--help` and `skills`. |
| `--help` | Show help |
| `--version` | Show version |

After any command that talks to the API, stderr ends with a budget line such as `api: 3 requests · 43/60 left this minute` (`⚠` when 10 or fewer remain). See the Budget section of the core skill.

The `Next page:` / `Continue scan:` command printed under a list repeats `--cached` or `--fresh` when the list was read under one, so the next page follows the same cache policy, and repeats `--max-requests` and `--owned` when they were given (and, for `get collection` and `get inventory`, `--limit`, its filters, `--sort` and `--desc`, plus `--in-wantlist` for the inventory; for `get lists` and `get wantlist`, `--limit`, plus `--for-sale`, `--max-price` and `--currency` for the wantlist). An inventory username starting with `-` is put after `--`.

**`bad_response` (exit 1):** Discogs answered with something the CLI cannot read: a non-JSON body (a gateway error page), a record that does not fit the SDK's models, or a malformed results page (`"results" is an object, expected an array`). The message names the problem (a validation failure names its first failing field, never the value), bounded to one line. It is never caused by your arguments, so do not change the command: retry in a moment. Such a response is not cached, so a plain retry asks Discogs again, with one exception: a price guide whose conditions are malformed is cached before they are read, so if the same error repeats, rerun with `--fresh`.

**`bad_response` with an unknown format (exit 1):** `get collection --value` reads numbers out of the formatted amounts Discogs sends (`"€1.250,00"`) and the currency from your profile's `curr_abbr`. An amount in a shape it does not know, or a profile without a currency code, is `bad_response` too, but its hint says the format is unknown and asks for a report at https://github.com/jmfontaine/agent-discogs/issues: retrying will not help. The message names the field (`the collection value's median`, `currency_code`), never the value; `get collection --value --json --full` prints the amounts as sent.

**`auth_required` (401, exit 1):** the message names the credentials that were sent, in the mode `status` reports. The hint says what to check, then `agent-discogs status shows the credentials in use.` Relay it to the user, who fixes the credentials; retrying the same command will not help.

| Mode | Message | Hint (before the `status` pointer) |
|------|---------|------------------------------------|
| `token` | `Authentication failed with DISCOGS_TOKEN.` | Check the token, or generate a new one at discogs.com/settings/developers. |
| `consumer` | `Authentication failed with the consumer key/secret (DISCOGS_CONSUMER_KEY, DISCOGS_CONSUMER_SECRET).` | Check both at discogs.com/settings/developers. They identify an app, not a user: price suggestions need DISCOGS_TOKEN or OAuth. |
| `oauth` | `Authentication failed with OAuth (DISCOGS_CONSUMER_KEY, DISCOGS_CONSUMER_SECRET, DISCOGS_ACCESS_TOKEN, DISCOGS_ACCESS_TOKEN_SECRET).` | Check all four; the access token may have been revoked. |
| `none` | `Authentication failed: no credentials are set.` | Set token: export DISCOGS_TOKEN=<token> (discogs.com/settings/developers). |

**`auth_required` before any request (`--owned`, `--in-wantlist`, `get owned`, `get collection`, `get lists`, `get wantlist`):** these read the authenticated user's own collection, lists or wantlist, so they need `token` or `oauth` in `status`. Under `consumer` or `none` they fail without sending anything: message `--owned reads your own Discogs account, but no credentials are set.` (or `... but a consumer key/secret identifies an app, not a user.`; `--in-wantlist reads ...`, `get owned reads ...`, `get collection reads ...`, `get lists reads ...` and `get wantlist reads ...` for the others), hint `Set a token: export DISCOGS_TOKEN=<token> (discogs.com/settings/developers), or use OAuth (DISCOGS_CONSUMER_KEY, DISCOGS_CONSUMER_SECRET, DISCOGS_ACCESS_TOKEN, DISCOGS_ACCESS_TOKEN_SECRET). agent-discogs status shows the credentials in use.` No `status` field in the JSON envelope. A 403 stays the generic `forbidden`. `get list` needs no user: a public list reads without credentials, and someone else's private list fails with whatever Discogs answered (`not_found`, `auth_required` or `forbidden`). Neither does `get inventory` alone: Discogs shows anyone a seller's For Sale listings.

**`rate_limited` (429, exit 1):** the message says what is left of the minute (`Rate limited: 0/60 requests left this minute.`; `Rate limit exceeded.` when Discogs sent no rate-limit headers), with `limit`/`remaining` in JSON when Discogs sends them. The hint starts with the wait (`Retry in 30s.`, `retry_after` in JSON; `Wait a moment and retry.` without a usable `Retry-After`), then the limit that applies:

- With credentials, the limit Discogs reported for them, e.g. `Discogs reported 60 req/min for the credentials in use (oauth).`, or `Discogs allows 60 req/min for the credentials in use (oauth).` when the 429 carried no rate-limit headers. Setting `DISCOGS_TOKEN` is never suggested here: wait, or continue with `--cached`.
- Without any credentials, `60 req/min with DISCOGS_TOKEN, 25 without.`

**Client-side scans:** `search` with `--release-type official` (the default) or `unofficial --format ...`, `get releases @a... --role`, `get releases @l... --year`, `get versions` with `--release-type official` or `unofficial --format ...`, `get collection` with a filter, and `get inventory` with a filter: these filter rows the API cannot filter. They read 100-row pages until `--limit` rows match or the list ends, with no cap by default: a dense filter costs one request, a sparse one over 2,000 results up to 20, plus the text header lookup for `get releases` and `get versions` and the username and folder list for `get collection`. `--max-requests N` (N ≥ 1, one ref, only on these listings; otherwise `invalid_argument` before any request) caps every HTTP send of the command, header lookups included, which run first; cache hits are free. A scan that stops early ends in `Continue scan:` and says why in its header:

- `scan stopped at --max-requests N`: the cap ran out (JSON: `capped: true`, `stop_reason: "max_requests"`). If the header lookup used the whole cap, the header reads `(--max-requests N ran out before the scan started)` with no counts, JSON `total_items` and `total_pages` are `null`, and the continuation starts the scan. Under `--fresh` the header adds `; --fresh sends the header lookup again on every run, so continue with a higher --max-requests`: the lookup is not cached, so the same cap would spend itself on it again.
- `scan stopped: rate limited, retry in Ns`: Discogs answered 429 after at least one page, and the rows read so far are returned, exit 0 (JSON: `stop_reason: "rate_limited"`, `retry_after` when Discogs sent one). A 429 on the first page is the `rate_limited` error.

With a tool-call time limit, pass `--max-requests`.

In a filtered list's JSON `pagination`, `page` counts your pages of `--limit` matches, but `total_items` is the unfiltered total (the text header's `≤N`) and `total_pages` counts the 100-row Discogs pages the scan reads, so page 2 can report `"total_pages":28`. Neither says how many pages of matches remain: continue until `next_cursor` is `null` (in text, until no `Next page:` / `Continue scan:` is printed).

**`--owned` collection read:** `--owned` (on `search`, `get versions`, `get releases`, `get inventory`) marks release rows with the copies you own. After the listing page it asks for the username (1 request), then reads your whole collection, the "All" folder, at 100 records per request, once per command (every response cached 1h, so a rerun is free). `--max-requests N` counts every send of the command (the text header, the listing page, the username and the collection pages) but only stops scans and the collection read: the header and a server-side listing page are always sent, header first, then the username and collection pages are asked only while the cap allows. `get versions @m... --owned --max-requests 1` therefore sends the header and the listing (2 sends) and reads no collection; with the cap already spent before the username, nothing is asked (`records_read: 0`, `total_records: null`). Rows the read did not reach say `· owned ?` (JSON `owned: null`) and never `not owned`; a count found in the part read may be short. A note line before the footer gives the cause: `Owned: the collection read stopped at --max-requests 2 (100 of 250 records read): "owned ?" rows are not in the part read, and a count may be short.` or `... the collection read was rate limited, retry in 30s (100 of 250 records read): ...`. A 429 on a later collection page keeps the records read. A 429 on the username or the first collection page never fails the command: no collection is read and every release row is `owned ?`, with `collection` = `{"complete":false,"records_read":0,"total_records":null,"stop_reason":"rate_limited","retry_after":N}`. Likewise, when the listing's own client-side scan stopped on a 429 (`pagination.stop_reason: "rate_limited"`), the collection is not read at all (no request) and the rows are `owned ?` with the listing's `retry_after`. See `--owned` under search. On `get inventory` a row is looked up by its listing's release id (never the listing's own id), and a listing with no release id is `owned ?`.

**`get wantlist --for-sale` scan:** a client-side scan over your wantlist (100 wants a page) that asks Discogs for each want's Marketplace stats (`GET /marketplace/stats/{id}`, cached 1h), so it costs 1 request per want checked on top of the username and the wants pages, with no default cap: a 500-want list with few for sale is about 500 requests, roughly 9 minutes of the 60/min budget. Always pass `--max-requests N` (60 is one minute; N must be 3 or more, `invalid_argument` otherwise: the username, a wants page and one want's stats, which a cold cache or `--fresh` sends on every run). The cap counts every send of the command (username, wants pages, stats) and is checked before each want, so the scan can stop mid-page; a wants page is asked for only while at least 2 sends remain after the sends so far (the page and one want's stats), so at a page boundary the scan stops with the boundary cursor without reading the next page. It ends in `Continue scan: agent-discogs get wantlist --for-sale --max-requests N --after P:R` (JSON `capped: true`, `stop_reason: "max_requests"`, `next_cursor`), and the cursor names the first want not yet checked, so the continuation never checks one twice. A 429 on any stats request keeps the rows matched so far (`stop_reason: "rate_limited"`, `retry_after`) with a `Continue scan:` from the want it answered; a 429 on the username or the first wants page is the `rate_limited` error. A want whose stats were not read is never reported as not for sale.

**`get inventory` scan:** with any of `--condition`, `--sleeve`, `--ships-from`, `--max-price` or `--in-wantlist`, `get inventory` is a client-side scan over the seller's listings (100 a page): 1 request per 100 listings scanned, with no default cap, so a seller with 50,000 listings is 500 requests (over 8 minutes of the 60/min budget, 20 at the unauthenticated 25/min). Always pass `--max-requests N` (1 or more; 60 is one minute). It counts every send of the command: the `--in-wantlist` username and wants pages, the inventory pages and the `--owned` collection pages. Without a filter `--max-requests` is accepted only with `--owned` (`invalid_argument` otherwise: a plain inventory pages server-side, 1 request per page). `--in-wantlist` first reads your whole wantlist (1 request for the username, then 1 per 100 wants, cached 1h): a listing is kept only against the complete wantlist, so when that read stops short (the cap, or a 429 on the username or a wants page) no listing is scanned and the command prints an empty capped page, exit 0, with the header `Inventory: seller in wantlist (--max-requests 2 ran out before the scan started)` (or `(rate limited before the scan started, retry in 30s)`), a note `Wantlist: the read stopped at --max-requests 2 (100 of 150 wants read), so no listing was scanned: a listing is kept only against the whole wantlist.` and a `Continue scan:` with the cursor asked for; JSON `results: []`, `pagination` with `total_items: null`, `capped: true`, `next_cursor: "1:0"` and `stop_reason`, plus `wantlist` `{"complete":false,"wants_read":100,"total_wants":150,"stop_reason":"max_requests"}`. Pasting the `Continue scan:` with a higher cap works: the wants already read come from the cache for free. A 429 on an inventory page after the first keeps the rows matched so far.


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

**`--owned`:** `search ... --owned` (an untyped search or `search release` that returns release rows) ends each release row in `· owned` (one copy), `· owned ×2` (two or more), `· not owned`, or `· owned ?` (not in the part of the collection read; see the `--owned` collection read above). Master, artist and label rows get no mark: a collection holds releases and Discogs does not document a master id on its records, so `search artist|label|master ... --owned` and `--owned` with `--added-since` fail with `invalid_argument` before any request, and a page with no release row reads nothing. `--json` gives each release row `owned` (an integer, `0` when not owned, or `null`) and the document a `collection` object: `{"complete": true, "records_read": 250, "total_records": 250}`, plus `stop_reason` (`max_requests` or `rate_limited`) and `retry_after` when the read stopped early; a page with no release row has neither. `--json --full` adds `owned` to the SDK record. The row's `have N` is Discogs' community count (how many Discogs users own the release); `owned` is your own. Cost: the listing, plus 1 for the username and 1 per 100 collection records, free for an hour; `--max-requests N` counts all of it but stops only scans and the collection read. `Next page:` / `Continue scan:` repeats `--owned`. Under `--cached`, any request not cached fails with `cache_miss`.

Each row starts with a ref. A result of any other type than the four above starts with `id N [type]` instead (`[no type]` when Discogs sends none) and has no ref (`id` instead of `ref` under `--json`); the rest of the page is unaffected.

## get

Get entity details or paginated lists.

```
agent-discogs get <noun> <ref-or-id>... [--flags]
```

One or more refs (at most 10), run in sequence. Text blocks are separated by a blank line; a failing ref reports inline and the others still run (exit 1 if any failed). `--json` with several refs returns a list, with `{"ref": ..., "error": {...}}` items for failures; with one ref the shape is the single document.

`collection`, `lists` and `wantlist` are the exceptions: they take no ref (`agent-discogs get collection [--flags]`, `agent-discogs get lists [--flags]`, `agent-discogs get wantlist [--flags]`; a ref is `invalid_argument`) and print one document for your own collection, lists or wantlist. `list` takes list ids, plain numbers only: lists have no ref prefix, so a typed ref such as `@r123` is `invalid_argument`.

`inventory` is the third kind: it takes exactly one Discogs username in place of a ref (`agent-discogs get inventory <username> [--flags]`), the seller's, not yours. Two usernames or a blank one is `invalid_argument`, none is a click usage error (exit 2). A username starting with `-` goes after `--` (`get inventory -- -name`); printed continuations do it for you.

**Nouns:**

| Noun | Expected Ref Type | Description |
|------|-------------------|-------------|
| `artist` | `@a` or numeric ID | Artist profile, with Discogs' markup converted (linked entities as refs, see below); `URLs:` lists linked sites by domain, omitting empty entries |
| `collection` | none | Your own collection, one row per copy (a release owned twice is two rows): `@r847868 "Nine Inch Nails - The Downward Spiral" 1994 · Nothing Records 92346-2 · CD (Album) · folder Vinyl · added 2024-01-04 · rating 4` (folder by name, the day it was added, the rating when set), under `Collection: me (page 1, 5 of 250 records)` and a `Next page:` command. JSON rows: `ref`, `title`, `year`, `label`, `catno`, `format`, `folder`, `added` (RFC 3339), `rating` when set; `--json --full` is the SDK's `CollectionItem` records (`id`, `instance_id`, `folder_id`, `date_added`, `rating`, `notes`, `basic_information`). Cost: 1 for the username + 1 for the folder list (none with `--json --full`) + 1 per page of up to 100 records. Needs a token or OAuth (`auth_required` before any request otherwise). Only your own collection: no `--user`, no folder selection, no editing. Filters and sorting below |
| `credits` | `@r` or numeric ID | Release, track, and sub-track credits grouped by role, each person once per role with an artist ref and track scope (none = whole release) |
| `identifiers` (alias `ids`) | `@r` or numeric ID | Barcodes, matrix/runout, label codes, rights societies |
| `inventory` | one Discogs username | One seller's Marketplace listings (`GET /users/{username}/inventory`), 5 a page: `@r847868 "Nine Inch Nails - The Downward Spiral" · CD, Album · NM/VG+ · USD 12.50 · ships from Germany · listed 2024-09-18` under `Inventory: seller (page 1, 5 of 300 listings)` and a `Next page:` command (filters appear in the header: `condition=NM sleeve=G ships-from=France max-price=45.5 currency=EUR`, `in wantlist` after the username). Grades are short codes (`NM/VG+`; a sleeve that is no grade, such as Generic, Not Graded or No Cover, prints as sent; no sleeve sent gives the media code alone; no media grade but a sleeve reads `?/VG+`), the price carries its ISO code, and `listed` is the day as Discogs sent it. Anything Discogs did not send is left out; a listing with no release id starts `listing <id>` instead of a ref. JSON rows: `listing` (the listing id), `ref` (the release), `title`, `format`, `condition`, `sleeve`, `price`, `currency`, `ships_from`, `listed` (RFC 3339), `url` (`https://www.discogs.com/sell/item/<id>`); `--json --full` is the SDK's listing records (`id`, `condition`, `sleeve_condition`, `price`, `original_price`, `shipping_price`, `ships_from`, `posted`, `release`, `seller`, ...). Cost: 1 request per page; with a filter 1 per 100 listings scanned, `--in-wantlist` adds 1 for the username and 1 per 100 wants, `--owned` adds the collection read. No credentials needed (Discogs shows anyone a seller's For Sale listings); `--in-wantlist` and `--owned` need a token or OAuth. Discogs shows non-owners only For Sale listings. No command finds every seller of one release (the API has no such endpoint); `get inventory` browses one seller you already know, and `price @r...` and `get wantlist --for-sale` give a count and a lowest price only. No blocking, rating, carts, offers, purchases or editing your own listings, and the shipping cost is not shown (Discogs sends an empty shipping price). |
| `label` | `@l` or numeric ID | Label profile, with Discogs' markup converted (linked entities as refs, see below); `URLs:` lists linked sites by domain, omitting empty entries |
| `list` | numeric ID only | One of your lists, or anyone's public list: `List: 123 "Pressings" · private`, `Description:` with the whole description (markup converted, never cut, line breaks kept), then one row per item, its ref (from the item's type: `@r`, `@m`, `@a`, `@l`) and title, with `· comment: ...` when set: `@r847868 "Nine Inch Nails - The Downward Spiral" · comment: check the matrix`. An item of another type reads `id 9 [type] "..."` with no ref. JSON `{"id","name","description","public","url","items":[{"ref","title","comment"}]}` (`id` and `type` instead of `ref` for another type; each comment as Discogs sent it); `--json --full` is the SDK's list record. Cost: 1 per list, whatever its length. No user needed; someone else's private list fails with `not_found`, `auth_required` or `forbidden`. Takes only `--json` / `--full`. Several ids behave as several refs |
| `lists` | none | Your own lists, private ones included, one row per list: `123 "Pressings" · private · changed 2016-06-02 → get list 123` (id, name, visibility, the UTC day it last changed; Discogs sends no item count), under `Lists: me (page 1, 5 of 7 lists)` and a `Next page:` command. JSON rows: `id`, `name`, `description` (markup converted), `public`, `added`, `changed` (RFC 3339), `url`; `--json --full` is the SDK's list summaries. Cost: 1 for the username (cached 1h) + 1 per page of up to 100 lists. Needs a token or OAuth (`auth_required` before any request otherwise). Only your own lists: no `--user`, no editing |
| `master` | `@m` or numeric ID | Master release details |
| `owned` | `@r` or numeric ID | Your own copies of a release: `@r352665 owned ×2: Vinyl, Uncategorized` (one copy: `owned: Vinyl`) or `@r847868 not owned`. JSON `{"ref":"@r352665","owned":2,"folders":["Vinyl","Uncategorized"]}` / `{"ref":"@r847868","owned":0}`; `--json --full` is `{"instances":[...]}` (the SDK's collection records, folders by id; no folder-list request). A copy in a folder Discogs does not name adds no folder name; with no names at all the text is `@r352665 owned` (no colon) and JSON has no `folders`. Cost: 1 for the username, 1 per ref, 1 for the folder list when any ref is owned. Needs a token or OAuth (`auth_required` before any request otherwise). Takes only `--json` / `--full`. Several refs behave as for the other nouns |
| `price` | `@r` or numeric ID | Marketplace pricing (requires a token or OAuth *and* seller settings on that account), each amount with its ISO currency code |
| `release` | `@r` or numeric ID | Full release details with inline artist/label refs, country, release date, `Added: YYYY-MM-DD` (day added to Discogs, in the timestamp's own offset; JSON `added`, RFC 3339), tracklist |
| `releases` | `@a` or `@l` (numeric ID = artist) | Artist discography, or a label's catalogue (paginated) |
| `tracklist` | `@r` or numeric ID | Tracklist only (from a release); an index track's parts are indented beneath it (JSON: `"type": "index"` with `sub_tracks`) |
| `versions` | `@m` or numeric ID | Master release versions (paginated) |
| `wantlist` | none | Your own wantlist, one row per want: `@r847868 "Nine Inch Nails - The Downward Spiral" 1994 · Nothing Records 92346-2 · CD (Album) · notes: Sealed only`, under `Wantlist: me (page 1, 5 of 150 wants)` and a `Next page:` command. JSON rows: `ref`, `title`, `year`, `label`, `catno`, `format`, and `notes`/`rating` when set (`rating` 0 = unrated is omitted); `--json --full` is the SDK's want records (`id`, `basic_information`, `notes`, `rating`, `resource_url`). Cost: 1 for the username (cached 1h) + 1 per page of up to 100 wants. Needs a token or OAuth (`auth_required` before any request otherwise). Only your own wantlist: no `--user`, no editing. `--for-sale` keeps the wants on sale (see below) |

**Markup in profiles and notes:** artist and label profiles (cut to about 300 characters, never inside a ref), release notes (`-v`) and list descriptions (never cut) convert Discogs' markup, in text and `--json` alike; `--json --full` keeps it as sent.

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
| `--json` | JSON output: a compact projection of the text view, plus `url` on `release`, `master`, `artist`, `label`, `list` and each `lists` row: the absolute Discogs web page (never in text; `versions` and `releases` rows only when Discogs sends one, which it does not today; never on list items, `tracklist`, `price`, `credits`, and `identifiers`: use `release` for the link). Errors become `{"error":{"code",...}}` on stdout |
| `--full` | With `--json`: the complete record instead of the projection, normalized (consistent field names such as `catalog_number` and a track's `type`, RFC 3339 timestamps, `null` for an absent or unparseable timestamp; unmodeled fields as Discogs sends them) |
| `--limit` | `releases`, `versions`, `collection`, `lists`, `wantlist`, `inventory`: results per page, 1-100 (default: 5); with a `collection` filter, `wantlist --for-sale` or an `inventory` filter, how many matches to collect |
| `--page` | Page number, from 1 (server-side pages: `versions` without a `--release-type` scan, `collection` without a filter, `lists`, `wantlist`, `inventory` without a filter, `releases` without `--role` / label `--year`); rejected with a `collection` filter, `wantlist --for-sale`, an `inventory` filter and `versions --release-type official` (or `unofficial --format ...`) |
| `--after` | Continuation cursor copied from the previous `Next page:` / `Continue scan:` line (`releases --role`, `releases @l... --year`, `versions --release-type official` or `unofficial --format ...`, `collection` with a filter, `wantlist --for-sale`, and `inventory` with a filter, one ref only) |
| `--max-requests` | `releases --role`, `releases @l... --year`, `versions --release-type official` (or `unofficial --format ...`), `releases` / `versions` / `inventory` with `--owned`, `collection` with a filter, `inventory` with a filter, and `wantlist --for-sale` (3 or more there: the username, a wants page and one want's stats); one ref only: cap the command's HTTP sends, the text header lookup, the `--owned` collection read, the `collection` username and folder list, the `--for-sale` username, wants pages and stats lookups, and the `--in-wantlist` username and wants pages included (the header runs first). Default: no cap. With `--owned` the cap only stops scans and the collection read: the header and a server-side listing page are always sent. In text under `--fresh` the header is sent again on every run, so a cap it uses up ends in an empty capped page whose header says to continue with a higher `--max-requests`. See Client-side scans above. |
| `--owned` | `releases`, `versions` and `inventory` only: mark each release row with the copies in your own collection (`· owned` for one copy, `· owned ×2` for more, `· not owned`, `· owned ?`; JSON `owned` and a `collection` object). Master, artist and label rows (an artist discography lists masters) get no mark. Needs a token or OAuth. Cost: 1 for the username + 1 per 100 collection records, once per command, free for an hour; `--max-requests` counts it with the listing but stops only the collection read. `have N` on a row is the Discogs community count, `owned` is yours. See the `--owned` collection read above, and `--owned` under search. |
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
| `--release-type` | `all` (default, unlike `search`, whose default is `official`): every version, unofficial ones included. `unofficial`: the versions whose format lists "Unofficial Release", filtered by Discogs (`format=Unofficial Release`), exact total, pages with `--page`; with `--format` a client-side scan instead. `official`: drops them in a client-side scan, `≤N` total, continues via `--after`, takes `--max-requests` (`--page` is `invalid_argument`). The match is on the whole description, so "Partially Unofficial" counts as official. Bootlegs, counterfeits and pirates all carry it: see [Unofficial Releases](discogs-domain.md#unofficial-releases-bootlegs-counterfeits-pirates). |

**Additional flags for `collection`:**

| Flag | Description |
|------|-------------|
| `--sort` | `label`, `artist`, `title`, `catno`, `format`, `rating`, `added`, or `year` (server-side, over the whole collection; Tab completes them) |
| `--desc` | With `--sort`: descending (`--sort added --desc`: newest additions first) |
| `--format` | Keep records with this format name or description (`Vinyl`, `12"`, `LP`) |
| `--genre` | Keep records with this genre |
| `--style` | Keep records with this style |
| `--year` | Keep records from this year |
| `--label` | Keep records with this label name |
| `--value` | Instead of the listing, print the collection's value from Discogs' Marketplace sales, in your profile's currency: `Collection value (EUR): min 75.50 · median 100.25 · max 250.00` (JSON `{"currency":"EUR","minimum":75.5,"median":100.25,"maximum":250.0}`; `--json --full`: `{"currency":"EUR","value":{...}}`, the amounts as sent). Amounts are parsed out of Discogs' strings: what precedes the first digit or follows the last is the symbol (a sign, parentheses, or a `.`/`,` right before the first digit is rejected); the last `.` or `,` followed by one or two digits is the decimal point; other `.`, `,`, spaces (no-break included) and `'` separate thousands in groups of three (`1,250` = 1250). Cost: 3 (username, profile, value). Takes no other flag (`invalid_argument` before any request). |

A filter matches one value of the record exactly, ignoring case (`--style techno` matches `Techno`, `--style Tech` matches nothing); several filters must all match. There is no `--country`: collection records carry none. Any filter makes it a client-side scan of 100-record pages in the requested order: `≤N` totals, `--page` rejected, continue with the printed `Continue scan:` / `Next page:` (`--after`), cap with `--max-requests N` (1 request per 100 records scanned; a 429 after the first page keeps the rows read). The username and the folder list are read before the scan and count against `--max-requests`: on a cold cache `--max-requests 2` returns an empty capped page (`--max-requests 2 ran out before the scan started`). Without `--sort` the scan reads the pages the `--owned` read caches, so within the hour of an `--owned` listing it costs only the folder list. `--max-requests` and `--after` without a filter fail with `invalid_argument` before any request, as do an unknown `--sort` key and `--desc` without `--sort`.

**Additional flags for `wantlist`:**

| Flag | Description |
|------|-------------|
| `--for-sale` | Keep only the wants with copies for sale now, each row ending `· for sale: 3 from EUR 12.50` (`· for sale: 3` when Discogs gives no price); JSON rows gain `market`: `{"for_sale":3,"lowest":12.5,"currency":"EUR"}`. A want is kept with at least one copy for sale and not blocked from sale (`blocked_from_sale`). 1 request per want checked: see the `get wantlist --for-sale` scan above, and always pass `--max-requests`. |
| `--max-price` | With `--for-sale`: keep wants whose lowest price is at most this amount (exactly the amount is kept; a want with copies but no lowest price is dropped). Needs `--currency`; a negative amount is `invalid_argument`. |
| `--currency` | With `--for-sale`: `USD`, `GBP`, `EUR`, `CAD`, `AUD`, `JPY`, `CHF`, `MXN`, `BRL`, `NZD`, `SEK` or `ZAR` (any case, sent uppercase; Tab completes it; another code is `invalid_argument` before any request). Prices every stats request in that currency; without it Discogs prices in the account's currency, which it does not name. |

`--max-price`, `--currency` and `--max-requests` without `--for-sale`, and `--after` without it, fail with `invalid_argument` before any request, as do `--max-price` without `--currency`, an unknown `--currency` code, and `--max-requests` below 3 with `--for-sale` (`--max-requests must be 3 or more with --for-sale (2 given): the username, a wants page and one want's stats, which a cold cache or --fresh sends on every run.`). A for-sale row under `--cached` whose stats are not cached ends `· ✗ Not cached: GET /marketplace/stats/5` (JSON `error.code: "cache_miss"`, no `market`), counts toward `--limit`, and exits 1; the text adds `Rerun without --cached to check the wants marked ✗.`

**What `--for-sale` cannot filter:** condition, sleeve condition, seller location and shipping. The Discogs API has no endpoint that lists a release's copies for sale, only per-release stats: the number of copies and one lowest price across every condition and location. For those, send the user to the release's Marketplace page on the Discogs website.

**Additional flags for `inventory`:**

| Flag | Description |
|------|-------------|
| `--sort` | `listed`, `price`, `item`, `artist`, `label` or `catno` (server-side, over the whole inventory; Tab completes them) |
| `--desc` | With `--sort`: descending |
| `--condition` | Keep listings whose media grade is this or better. Scale: `M` > `NM` > `VG+` > `VG` > `G+` > `G` > `F` > `P` (Mint to Poor). Short code or full Discogs name (`Very Good Plus (VG+)`), any case; anything else is `invalid_argument` before any request (`--condition must be a Discogs grade: M, NM, VG+, VG, G+, G, F or P, or its full name such as 'Very Good Plus (VG+)' (any case; 'mint-' given).`). Tab completes the codes. |
| `--sleeve` | Keep listings whose sleeve grade is this or better; same scale and spelling. A Generic, Not Graded or No Cover sleeve fails any `--sleeve`. |
| `--ships-from` | Keep listings shipping from this country, exact match on the country as Discogs names it, any case (`germany` matches `Germany`, `Germ` does not). Blank is `invalid_argument`. |
| `--max-price` | Keep listings priced in `--currency` at this amount or less (exactly the amount is kept; a listing with no price is dropped). Needs `--currency`; a negative or non-finite amount is `invalid_argument` (a value that is not a number, such as `abc`, is a usage error, exit 2). A listing priced in another currency is dropped and counted: the header says `N listings in other currencies skipped` (`1 listing in another currency skipped`) and JSON `pagination.skipped_currency` is present whenever `--max-price` is given, 0 included; the count covers the listings that passed every other filter, in the part this run scanned. |
| `--currency` | With `--max-price` only (alone it is `invalid_argument`): `USD`, `GBP`, `EUR`, `CAD`, `AUD`, `JPY`, `CHF`, `MXN`, `BRL`, `NZD`, `SEK` or `ZAR` (any case; Tab completes it). Here it is matched client-side against each listing's price currency and never sent to Discogs, unlike `wantlist --for-sale`, where it is sent as `curr_abbr` and prices the stats. Discogs reports a seller's price in the seller's own currency (observed: USD, EUR, GBP), so pick the seller's currency and expect skipped counts for a seller who prices in another. |
| `--in-wantlist` | Keep listings whose release is on your wantlist. Needs a token or OAuth (`auth_required` before any request otherwise). Reads your whole wantlist first (see the `get inventory` scan above); JSON adds a top-level `wantlist` object `{"complete":true,"wants_read":150,"total_wants":150}`. |

Any filter makes `get inventory` a client-side scan: 100-listing pages in the requested order, `--limit` counts matches, `≤N` totals, `--page` rejected, continue with the printed `Continue scan:` / `Next page:` (`--after`, which also repeats the filters, `--sort`, `--desc` and `--in-wantlist`), cap with `--max-requests N`. Without a filter it pages server-side (`--limit` 1-100, `--page`).

**Which flags apply where:** each flag applies only to the nouns below. Any other flag fails with `invalid_argument` before a request is made, even one set to its default value (`--limit 5`). `--after` and `--max-requests` also need exactly one ref: a cursor continues one listing, and a capped listing continues one ref at a time.

| Noun | Flags besides `--json` / `--full` |
|------|-----------------------------------|
| `release` | `-v, --verbose`, `-c, --compact` |
| `releases @a...` | `--limit`, `--page`, `--role`, `--sort`, `--desc`, `--owned`, `--after` and `--max-requests` (with `--role` or `--owned`) |
| `releases @l...` | `--limit`, `--page`, `--year`, `--owned`, `--after` and `--max-requests` (with `--year` or `--owned`) |
| `versions` | `--limit`, `--page`, `--country`, `--format`, `--label`, `--year`, `--sort`, `--desc`, `--release-type`, `--owned`, `--after` (with a `--release-type` scan) and `--max-requests` (with a `--release-type` scan or `--owned`) |
| `collection` | no ref; `--limit`, `--page`, `--sort`, `--desc`, `--format`, `--genre`, `--style`, `--year`, `--label`, `--after` and `--max-requests` (with a filter); or `--value` alone |
| `lists` | no ref; `--limit`, `--page` |
| `wantlist` | no ref; `--limit`, `--page`, `--for-sale`, `--max-price` and `--currency` (both with `--for-sale`), `--after` and `--max-requests` (with `--for-sale`) |
| `inventory` | one username, not a ref; `--limit`, `--page`, `--sort`, `--desc`, `--owned`, `--condition`, `--sleeve`, `--ships-from`, `--in-wantlist`, `--max-price` and `--currency` (each needs the other), `--after` (with a filter) and `--max-requests` (with a filter or `--owned`) |
| `artist`, `credits`, `identifiers`, `label`, `list`, `master`, `owned`, `price`, `tracklist` | none |

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
agent-discogs get versions @m3719 --release-type official --limit 5 --json   # client-side scan; continue with next_cursor
agent-discogs get versions @m3719 --release-type unofficial   # filtered by Discogs, pages with --page
agent-discogs get release @r847868 --verbose
agent-discogs get versions @m3719 --country US --owned
agent-discogs get releases @a3857 --role Main --owned --max-requests 6
agent-discogs get owned @r847868 @r352665
agent-discogs get collection
agent-discogs get collection --sort added --desc --limit 20                       # newest additions first
agent-discogs get collection --style Techno --format 12\" --max-requests 20      # client-side scan; continue with the printed --after
agent-discogs get collection --value                                              # what it is worth, in your currency
agent-discogs get wantlist
agent-discogs get wantlist --limit 100 --page 2
agent-discogs get wantlist --for-sale --max-price 20 --currency EUR --max-requests 60
agent-discogs get inventory redscroll --sort price --desc --limit 20
agent-discogs get inventory redscroll --condition NM --sleeve VG+ --ships-from Germany --max-requests 20   # client-side scan; continue with the printed --after
agent-discogs get inventory redscroll --max-price 30 --currency USD --max-requests 20
agent-discogs get inventory <seller> --in-wantlist --condition VG+ --max-requests 60   # your wantlist read first, then the scan
agent-discogs get lists
agent-discogs get lists --limit 100 --page 2
agent-discogs get list 123
agent-discogs get list 123 456 --json
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
