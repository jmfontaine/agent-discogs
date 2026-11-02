# Caching and request budget

## Cache

HTTP responses are cached automatically with a 1-hour TTL. The cache is stored at `$XDG_CACHE_HOME/agent-discogs/` when `XDG_CACHE_HOME` is set to an absolute path, and at `~/.cache/agent-discogs/` otherwise. Clear it with `agent-discogs cache clear`.

## --cached and --fresh

Two global flags control the cache for one command. They precede the subcommand and are mutually exclusive (exit code 2 otherwise); after the subcommand, they are [a usage error](errors.md#a-global-flag-after-the-command):

```bash
agent-discogs --cached price @r352665      # serve only from the cache; never touches the API
agent-discogs --fresh get release @r352665 # bypass the cache; go to the API
```

`--cached` fails with a `cache_miss` error (exit code 1) on any request the cache cannot serve, before any network I/O. With several refs, hits print normally and each miss reports inline, exactly like other per-ref failures; under `--json` the miss item carries `error.code == "cache_miss"`. Three lookups instead report a miss inline, beside the data that was read: the want rows of `get wantlist --for-sale` (exit 1), the rows of `get versions --market` (exit 1) and the master of `get release --master-year` (exit 0); all are described below.

The check is exact: it uses the real cache key at the real moment, so a command that issues several requests fails at the first one that is not cached. There is no separate "is it cached?" query because the answer depends on the whole request sequence a command issues.

That includes client-side scans: under `--cached` a scan reads cached pages until its page is full, and the first page the cache does not hold fails the whole command with `cache_miss`, rows read so far included. Cache hits never count against `--max-requests`, so the cap does not stop a cache-only scan early.

`--owned` on `search` and `get inventory` reads your collection through the same cache: under `--cached`, the username or a collection page the cache does not hold fails the whole command with `cache_miss`, as for a scan, and nothing is sent. On `get versions` and `get releases` it reads no collection (each row carries your count), so it needs nothing in the cache beyond what the listing itself needs: with `--json` and an `@m`, `@a` or `@l` ref, the cached listing page alone, and nothing is sent; in text, the cached header lookup too (`get versions @r...` also needs its cached release). `get collection` fails like a scan on the username, the folder list, the fields list (with `--fields`) or a page. Without `--sort` or `--folder`, a filtered `get collection` scan sends the same page requests as the `--owned` read, so after an `--owned` search or inventory within the hour it reads them from the cache (it still asks for the folder list, which `--owned` never reads).

`--wanted` follows the same rules: on `search` it reads your wantlist through the cache, so under `--cached` the username or a wants page the cache does not hold fails the command with `cache_miss` and nothing is sent; on `get versions` and `get releases` each row carries your want count, so it needs nothing beyond the listing's own cached requests.

`get wantlist --for-sale` reads your wantlist through the same cache: under `--cached`, the username or a wants page the cache does not hold fails the whole command with `cache_miss` and nothing is sent. A want whose Marketplace stats are not cached does not fail the command: it becomes a row carrying its own `cache_miss` (`· ✗ Not cached: GET /marketplace/stats/5` in text, then `Rerun without --cached to check the wants marked ✗.`; `error.code == "cache_miss"` under `--json`), counts toward `--limit`, and the command exits 1, like a missed ref of a multi-ref `get`. The want is never reported as not for sale; rerun without `--cached` to check it.

`get versions --market` reads the listing, and as text the master title of the header, before any row: under `--cached` either one not cached fails the command with `cache_miss`, as without `--market`. A row whose Marketplace stats are not cached does not: it ends `· ✗ Not cached: GET /marketplace/stats/847868` (`error.code == "cache_miss"` under `--json`, with no `market`), the text adds `Rerun without --cached to check the rows marked ✗.`, and the command exits 1. With every row cached it sends nothing and exits 0.

`get release --master-year` reads the release, then its master, through the same cache: under `--cached` a release that is not cached fails the ref with `cache_miss` as before, and a cached release whose master is not cached still prints in full, its `Master:` line ending in `· ✗ Not cached: GET /masters/3719` instead of the year (`master_year_error.code == "cache_miss"` under `--json`). That ref does not fail, so the exit code is 0 unless another ref failed: the year is an extra the release does not need. Nothing is sent.

`get inventory` with a filter reads the seller's inventory through the same cache, and `--in-wantlist` reads your wantlist through it first: under `--cached`, the username, a wants page or an inventory page the cache does not hold fails the whole command with `cache_miss` and nothing is sent, as for any other scan. After an empty wantlist read whole, only the first inventory page the scan would read is needed. `get inventory` alone sends no username request, so under `--cached` it fails only when its own page is not cached.

`--fresh` stores nothing, so under it a text `get releases` scan, or a text `get versions --release-type official` (or `unofficial` with `--format`) scan, spends one request on the header every run: with `--max-requests 1` it returns the empty capped page, and its header says to continue with a higher `--max-requests`, since the same cap would spend itself on the header again.

`--fresh` fetches every request from the API and does **not** store the response, so a later run without `--fresh` can still be served the older cached entry until its 1-hour TTL expires.

A collection read by `--owned` on `search` and `get inventory`, or by `get owned @m...`, is cached for an hour like everything else, and on `get versions` and `get releases` the marks come with the cached listing page, so either way a record added to your collection within the last hour may still show as not owned. `--fresh` re-reads the collection, or the listing, from Discogs.

Marketplace stats read by `get wantlist --for-sale`, `get stats` and `get versions --market` are cached for an hour too, so a copy listed or sold within the last hour may not show up. Pass `--fresh` when current Marketplace numbers matter; it re-reads the stats of every want, ref or row checked, one request each (`get stats` re-reads the release too, `get versions` the listing page).

The `Next page:` / `Continue scan:` command printed under a list repeats `--cached` or `--fresh` (and `--max-requests`, `--owned`, `--wanted`, for `get versions` `--market` and `--currency`, for `get collection` and `get inventory` `--limit`, their filters, `--sort` and `--desc`, plus `--fields` for the collection, `--in-wantlist` for the inventory, and for `get lists` and `get wantlist` `--limit`, plus `--for-sale`, `--max-price`, `--currency` for the wantlist), so the next page is read under the same cache policy, cap and filter as the first.

A list read by `get list` is cached for an hour like everything else, so an item added to the list or a comment changed within the last hour may not show; `--fresh` re-reads it.

## Cache errors

A cache the CLI cannot use (its directory cannot be created, `cache.db` is a symlink, not a regular file, or owned by someone else, or SQLite cannot open it) fails the first request that needs it with a `cache_error` (exit code 1): the message names the database path and what failed, and the hint says what to fix.

Nothing deletes the database for you; fix its permissions or ownership, or delete it by hand. `--fresh` never opens the cache, so it still works meanwhile.

## Budget

Discogs throttles by source IP: 60 requests per minute with any credentials, 25 without, as a moving 60-second window. After any command that talks to the API, stderr ends with one budget line:

```
api: 3 requests · 43/60 left this minute
api: 0 requests · cached
api: 4 requests · 6/60 left this minute ⚠ pause ~60s before uncached calls
```

The count is what this command spent: every HTTP request sent, including one whose connection failed; a `--cached` miss sends nothing. `43/60` is what Discogs reported after the last request.

`⚠` appears when 10 or fewer requests remain: pause about a minute, or keep to refs that are already cached (`--cached` guarantees zero spend). When the limit Discogs reported is below 60/min (the anonymous tier), the warning suggests setting `DISCOGS_TOKEN` instead; which credentials are set does not change it.

The line is on stderr and never inside `--json` output; commands that make no request (`status`, `skills`, `cache clear`, `completion`) print nothing.

## What commands cost

What commands cost (network requests; listing rows are per ref):

| Command | Requests |
|---|---|
| `get artist\|label\|master\|release\|credits\|identifiers\|tracklist`, `tracks` | 1 per ref |
| `get release --master-year` | 1 per ref, plus 1 per release with a master whose master is not cached (`GET /masters/{id}`, shared with `get master` and between releases of one master; under `--fresh`, 1 per such ref): at most 2 per ref, 20 for 10 refs |
| `get price`, `price` | 3 per ref (`--json --full`: 2, the release itself is not read) |
| `get stats` | 2 per ref, the release then its Marketplace stats: 20 for 10 refs. The release request is the one `get release` and `get price` send, the stats request the one `get price` sends without `--currency` and `get wantlist --for-sale` sends with the same `--currency` (or none), so they share the cache. A release that does not exist costs 1 (no stats request). Another `--currency` is another stats request: a rerun with a new code sends 1 per ref, the stats only. Under `--cached`, a ref whose release or stats is not cached is its own `cache_miss` block and nothing is sent. No username or profile request |
| `get releases @a...`, `get releases @l...`, `get versions @m...` (also with `--release-type all`, the default, or `unofficial` without `--format`, filtered by Discogs) | `--json`: 1; text: 2 (the header's artist, label or master name) |
| `get versions @r...` | 2, text or `--json` (the release, for its master, then the list) |
| Client-side scans: `get releases @a... --role`, `get releases @l... --year`, `get versions @m... --release-type official` (or `unofficial` with `--format`) | `--json`: one per 100 rows scanned; text: one more, for the header name (`get versions @r...`: one more in either mode, the release, which also gives the header its title) |
| `get versions --market` | the listing's own cost, then 1 per row shown (its Marketplace stats). For N rows, a server-side page is 1 + N with `--json` and `@m...`, 2 + N as text with `@m...` (the header is read first) and 2 + N in either mode with `@r...` (the release lookup); a scan of S pages (`--release-type official`, or `unofficial` with `--format`) is S + N, 1 + S + N and 1 + S + N. `--limit 100` is 101 requests with `--json` on a server-side page. Lookups whose responses are still cached are free, so a rerun after a partial read sends only the rest; another `--currency` is another request per row. Pass `--max-requests` for long pages (fewer rows are then looked up) |
| `search artist\|label`, `search --release-type all`, `search --release-type unofficial` (filtered by Discogs, without `--format`) | 1 |
| Other searches (`--release-type official`, the default, or `unofficial` with `--format`): client-side scans | one per 100 rows scanned |
| `search release ... --added-since` (experimental) | 1 per release the search matches in the window (before `--release-type` filtering), plus 1 per 100 search results and 1 for the release that ends the scan |
| `--owned` or `--wanted` on `get versions` and `get releases` | 0: each row carries your own counts, so no username, no collection page and no wants page is read |
| `--owned` on `search` and `get inventory` (added to the listing's own cost) | 1 for the username, plus 1 per 100 records of your collection, then free for an hour; nothing when the page has no release row (a master, artist or label row is never marked), when the listing's own scan stopped on a 429, or, on `get inventory`, when `--in-wantlist` stopped short (no rows) |
| `--wanted` on `search` (added to the listing's own cost) | 1 for the username (shared with `--owned`), plus 1 per 100 wants (at least 1), then free for an hour; nothing when the page has no release row, or when the listing's scan or the `--owned` collection read stopped on a 429. With both flags: the listing + 1 + the collection pages + the wants pages |
| `get owned @r...` | 1 for the username, plus 1 per ref, plus 1 for the folder list when any ref is owned (`--json --full`: no folder list) |
| `get owned @m...` (one or several masters) | 1 for the username, plus 1 per 100 records in your collection (1 when it is empty), read once per command whatever the number of master refs or the master's versions, plus 1 for the folder list when any copy is found (`--json --full`: none). Release refs mixed in add 1 each; the username and the folder list are shared. `--cached` fails each master ref with `cache_miss` when the username or a page is not cached |
| `get collection` (`--folder` alone: that folder's pages) | 1 for the username (shared with `--owned`), plus 1 for the folder list (`--json --full`: none, it names folders by id, unless `--folder` needs the list to resolve the name), plus 1 for the fields list with `--fields` (read after the folder list, once, then free for an hour), plus 1 per page of up to 100 records (`--limit`) |
| `get collection` with `--format`, `--genre`, `--style`, `--year`, `--label`, `--artist` or `--title` (a client-side scan; with `--folder`, of that folder only) | 1 for the username, plus 1 for the folder list (`--json --full`: none, unless `--folder`), plus 1 for the fields list with `--fields`, plus 1 per 100 records scanned; without `--sort` or `--folder`, the pages are the ones the `--owned` read caches |
| `get collection --value` | 3: the username (shared with `--owned`), your profile (its currency) and the collection value |
| `get collection --count-by` (with or without filters) | 1 for the username (shared with `--owned`), plus 1 per 100 records in your collection (1 when it is empty): `1 + max(1, ceil(N/100))` for N records; the pages are the ones the `--owned` read caches, and no folder list is read. With `--folder`: 1 more for the folder list, then that folder's own pages, `2 + max(1, ceil(F/100))` |
| `get collection --duplicates` (with or without filters) | 1 for the username, 1 for the folder list (none under `--json --full` without `--folder`), plus 1 per 100 records (1 when empty): `2 + max(1, ceil(N/100))` for N records; the same pages as `--count-by` and the `--owned` read. `--fields`: 1 more for the fields list. With `--folder`: that folder's own pages |
| `get collection --random [N]`, unfiltered | 1 for the username, 1 for the folder list (none under `--json --full` without `--folder`), page 1 (100 records, for the total), then 1 per other page holding a pick: `3 + d` (`2 + d` under `--json --full` without `--folder`), at most `3 + min(N, P - 1)` for P pages; the pages `--owned` caches. `--fields`: 1 more for the fields list. With `--folder`: that folder's own pages |
| `get collection --random [N]` with a filter | the whole read of `--duplicates`: `2 + max(1, ceil(N/100))` for N records, whatever the number drawn |
| `get wantlist` | 1 for the username (shared with `--owned`), plus 1 per page of up to 100 wants (`--limit`) |
| `get wantlist --for-sale` (on top of the above) | 1 per want checked (its Marketplace stats, `GET /marketplace/stats/{id}`, cached for an hour), with wants read 100 a request and no default cap: a sparse match costs about one request per want on the list, so 500 wants is about 500 requests, roughly nine minutes of the 60 a minute budget. Pass `--max-requests` |
| `get inventory <username>` | 1 per page of up to 100 listings (`--limit`); no username request, no credentials needed |
| `get inventory` with `--condition`, `--sleeve`, `--ships-from` or `--max-price` (a client-side scan) | 1 per 100 listings scanned: a seller with 50,000 listings is 500 requests, over eight minutes of the 60 a minute budget (20 minutes at 25). Pass `--max-requests` |
| `get inventory --in-wantlist` (on top of the above) | 1 for the username (shared with `--owned`), plus 1 per 100 wants, all before the first listing is read; when `--max-requests` or a 429 stops that read, no listing is scanned. An empty wantlist read whole replaces the scan with 1 inventory page, for the totals: at most 3 requests in all, whatever the seller's size |
| `get lists` | 1 for the username (shared with `--owned` and `get wantlist`), plus 1 per page of up to 100 lists (`--limit`) |
| `get list <id>...` | 1 per list, whatever its length (every item comes in one response) |
| Repeating a command | A response is served from the cache for one hour after it was stored (a hit does not renew it), so repeating a command sends only the requests whose responses are no longer cached: none when the first run completed and its responses are all still within the hour. A rerun still sends requests after a run that stopped early, at `--max-requests` or on a 429 (it sends the pages the first run did not reach, and under a cap it spends the cap on them, so it reads further), and for `get collection --random`, which draws again. `--fresh` sends everything again |

A scan reads 100-row pages until the page is full or the list ends: a dense filter costs one request, and a sparse one over 2,000 results up to 20.

## Capping requests with --max-requests

`--max-requests N` caps the HTTP sends of the whole command: header lookups, the username, the folder and fields lists and every page included. Cache hits are free and never count, so a rerun of the same command within the hour spends the cap on the requests the first run did not reach, and reads further. `N` must be 1 or more, with one ref, otherwise `invalid_argument` before any request.

It is accepted only where something can stop: a client-side scan (see [Client-side scans](commands.md#client-side-scans)), `search --added-since` (see [Releases added since a date](commands.md#releases-added-since-a-date)), or one of the reads of the table below. Where each noun takes it is in [Flags per noun](commands.md#flags-per-noun); the rejection messages are in [Errors](errors.md). `--owned` and `--wanted` on `get versions` and `get releases` read nothing (each row carries your counts), so there the cap needs a scan or `--market` and caps that alone.

A scan that stops at the cap ends in a `Continue scan:` command that repeats `--max-requests`, with `scan stopped at --max-requests N` in the header (`capped: true`, `stop_reason: "max_requests"` in JSON).

If text-mode header lookups use the whole budget, the scan has not started: the header says so and shows no counts, and JSON's `total_items`/`total_pages` are `null`.

Each command below is described once here, for what the cap counts and where it is accepted, and once in its own section, for what it does when the cap stops it (header text, `Continue scan:`, JSON keys, rows left unchecked):

| Command | What the cap counts and where it is accepted |
|---|---|
| [`--owned`](commands.md#marking-rows-with---owned) on `search` and `get inventory` | Counts every send of the command (the listing page, the username and the collection pages), but only stops what can stop: a scan and the collection read. A server-side listing page is always sent; the username and the collection pages are then asked only while the cap allows. Accepted on `search` with `--owned` or `--wanted` (a server-side search with neither rejects it), and on `get inventory` with `--owned` or a filter |
| [`--wanted`](commands.md#marking-rows-with---wanted) on `search` | Caps the wantlist read the way it caps the `--owned` collection read: the read comes after the listing (and after the `--owned` collection read when both flags are given), and every send of the command counts |
| [`get collection`](commands.md#collection) with a filter | Accepted with a filter, which makes it a client-side scan (of the `--folder`'s pages when given); `--folder` alone pages server-side and takes none. The username and the folder list are read before the scan, the way a text header is read, and the fields list after them with `--fields`, so the cap counts them first |
| [`get collection --count-by`](commands.md#--count-by) | Accepted with or without a filter: it reads the whole collection, the username first (then the folder list with `--folder`), 100 records a request. Cache hits are free, so rerunning reads further: with `--max-requests 3`, the first run reads the username and 2 pages, the second finds them cached and can read up to 3 more pages |
| [`get collection --duplicates`](commands.md#--duplicates) | Accepted with or without a filter, like `--count-by`: the username and the folder list first (then the fields list with `--fields`), then 100 records a request. A rerun reads further for the same reason |
| [`get collection --random`](commands.md#--random) | Accepted with a filter only, which makes it a scan of the whole collection like `--duplicates` (the username and the folder list first). An unfiltered draw takes no cap: it reads page 1 and the pages of its picks only |
| [`get wantlist --for-sale`](commands.md#--for-sale) | Accepted with `--for-sale` only, and 3 or more: the username, a wants page and one want's stats, which a cold cache or `--fresh` sends on every run. Counts every send, the username first. A scan has no default cap and spends a request per want (see [What commands cost](#what-commands-cost)), so an uncapped run over a long wantlist can end on a 429 once the per-minute budget is gone: pass it (60 is one minute of budget) |
| [`get versions --market`](commands.md#--market) | Accepted with `--market` (one ref), a scan or not. Caps the whole command: the text header and the server-side listing page are always sent (a `--release-type` scan reads its pages only while the shared cap allows, and the lookups get what it leaves); then each row's Marketplace lookup is sent only while the command has sent fewer than N requests |
| [`get inventory`](commands.md#inventory) | Accepted with a filter or `--owned` only (alone it pages server-side, one request per page). Counts every send: the username, the wants pages `--in-wantlist` reads, the inventory pages and the collection pages of `--owned`. With a filter, pass it (see [What commands cost](#what-commands-cost); 60 is one minute of budget) |

Agents whose tool calls have a time limit should pass `--max-requests`.

## No retries

The CLI reports; it never retries, sleeps, or throttles on your behalf. Each request is sent once: a 429, a 5xx, or a connection failure is reported as that request's error without a retry, and retrying is the caller's call. With several refs, the failing ref gets its error block and the rest still run (exit 1). What a 429 error says is in [Errors](errors.md#rate-limited-429).

One exception: a 429 partway through a client-side scan returns the rows read so far, exit 0, with a `Continue scan:` command and `stop_reason: "rate_limited"` (plus `retry_after` when Discogs sent one) under `--json`. A 429 on a scan's first page is the usual error.

A 429 on the first request a command needs (the username, or the first page) is the usual `rate_limited` error. After it, each read keeps what it has read, and the command says so in its own section: [`get wantlist --for-sale`](commands.md#--for-sale) (a 429 on any stats request, first page included), [`get versions --market`](commands.md#--market) (the lookups stop, the rows read keep their numbers), [`get inventory`](commands.md#inventory) and [`--in-wantlist`](commands.md#--in-wantlist), [`--owned`](commands.md#marking-rows-with---owned) and [`--wanted`](commands.md#marking-rows-with---wanted) (the rows not reached read `owned ?` or `wanted ?`), [`get owned @m...`](commands.md#a-master-any-pressing), [`--count-by`](commands.md#--count-by), [`--duplicates`](commands.md#--duplicates) and [`--random`](commands.md#--random). The exceptions are there too: an unfiltered `--random` fails on a 429 on any page, and the `--owned`, `--wanted` and `get owned @m...` reads and the `--in-wantlist` wantlist read never fail the command, the username and the first page included (a stopped `--in-wantlist` read prints the empty capped page, exit 0).
