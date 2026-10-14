# Caching and request budget

## Cache

HTTP responses are cached automatically with a 1-hour TTL. The cache is stored at `$XDG_CACHE_HOME/agent-discogs/` when `XDG_CACHE_HOME` is set to an absolute path, and at `~/.cache/agent-discogs/` otherwise. Clear it with `agent-discogs cache clear`.

## --cached and --fresh

Two global flags control the cache for one command. They precede the subcommand and are mutually exclusive (exit code 2 otherwise):

```bash
agent-discogs --cached price @r352665      # serve only from the cache; never touches the API
agent-discogs --fresh get release @r352665 # bypass the cache; go to the API
```

`--cached` fails with a `cache_miss` error (exit code 1) on any request the cache cannot serve, before any network I/O. With several refs, hits print normally and each miss reports inline, exactly like other per-ref failures; under `--json` the miss item carries `error.code == "cache_miss"`.

The check is exact: it uses the real cache key at the real moment, so a command that issues several requests fails at the first one that is not cached. There is no separate "is it cached?" query because the answer depends on the whole request sequence a command issues.

That includes client-side scans: under `--cached` a scan reads cached pages until its page is full, and the first page the cache does not hold fails the whole command with `cache_miss`, rows read so far included. Cache hits never count against `--max-requests`, so the cap does not stop a cache-only scan early.

`--owned` reads your collection through the same cache: under `--cached`, the username or a collection page the cache does not hold fails the whole command with `cache_miss`, as for a scan, and nothing is sent.

`get wantlist --for-sale` reads your wantlist through the same cache: under `--cached`, the username or a wants page the cache does not hold fails the whole command with `cache_miss` and nothing is sent. A want whose Marketplace stats are not cached does not fail the command: it becomes a row carrying its own `cache_miss` (`· ✗ Not cached: GET /marketplace/stats/5` in text, `error.code == "cache_miss"` under `--json`), counts toward `--limit`, and the command exits 1, like a missed ref of a multi-ref `get`. The want is never reported as not for sale; rerun without `--cached` to check it.

`--fresh` stores nothing, so under it a text `get releases` scan spends one request on the header every run: with `--max-requests 1` it returns the empty capped page, and its header says to continue with a higher `--max-requests`, since the same cap would spend itself on the header again.

`--fresh` fetches every request from the API and does **not** store the response, so a later run without `--fresh` can still be served the older cached entry until its 1-hour TTL expires.

A collection read by `--owned` is cached for an hour like everything else, so a record added to your collection within the last hour may still show as not owned. `--fresh` re-reads the collection from Discogs.

Marketplace stats read by `get wantlist --for-sale` are cached for an hour too, so a copy listed or sold within the last hour may not show up. Pass `--fresh` when current Marketplace numbers matter; it re-reads the stats of every want checked, one request each.

The `Next page:` / `Continue scan:` command printed under a list repeats `--cached` or `--fresh` (and `--max-requests`, `--owned`, and for `get wantlist` `--limit`, `--for-sale`, `--max-price`, `--currency`), so the next page is read under the same cache policy, cap and filter as the first.

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
| `get price`, `price` | 3 per ref (`--json --full`: 2, the release itself is not read) |
| `get releases @a...`, `get releases @l...`, `get versions @m...` (also with `--release-type all`, the default, or `unofficial` without `--format`, filtered by Discogs) | `--json`: 1; text: 2 (the header's artist, label or master name) |
| `get versions @r...` | 2, text or `--json` (the release, for its master, then the list) |
| Client-side scans: `get releases @a... --role`, `get releases @l... --year`, `get versions @m... --release-type official` (or `unofficial` with `--format`) | `--json`: one per 100 rows scanned; text: one more, for the header name (`get versions @r...`: one more in either mode, the release, which also gives the header its title) |
| `search artist\|label`, `search --release-type all`, `search --release-type unofficial` (filtered by Discogs, without `--format`) | 1 |
| Other searches (`--release-type official`, the default, or `unofficial` with `--format`): client-side scans | one per 100 rows scanned |
| `search release ... --added-since` (experimental) | 1 per release the search matches in the window (before `--release-type` filtering), plus 1 per 100 search results and 1 for the release that ends the scan |
| `--owned` on `search`, `get versions`, `get releases` (added to the listing's own cost) | 1 for the username, plus 1 per 100 records of your collection, then free for an hour; nothing when the page has no release row (a master, artist or label row is never marked) or when the listing's own scan stopped on a 429 |
| `get owned @r...` | 1 for the username, plus 1 per ref, plus 1 for the folder list when any ref is owned (`--json --full`: no folder list) |
| `get wantlist` | 1 for the username (shared with `--owned`), plus 1 per page of up to 100 wants (`--limit`) |
| `get wantlist --for-sale` (on top of the above) | 1 per want checked (its Marketplace stats), with wants read 100 a request: a sparse match costs about one request per want on the list, so 500 wants is about 500 requests, roughly nine minutes of the 60 a minute budget. Pass `--max-requests` |
| Anything repeated within 1 hour | 0 (cached) |

A scan reads 100-row pages until the page is full or the list ends: a dense filter costs one request, and a sparse one over 2,000 results up to 20.

## Capping requests with --max-requests

`--max-requests N` caps the HTTP sends of the whole command (with `--owned` or `get wantlist --for-sale`, see below), header lookups included (cache hits are free); it is accepted only where a scan runs (or with `--added-since`, see [Releases added since a date](commands.md#releases-added-since-a-date), or with `--owned`) and with one ref, and must be 1 or more (3 or more with `get wantlist --for-sale`: the username, a wants page and one want's stats, which a cold cache or `--fresh` sends on every run), otherwise `invalid_argument` before any request.

A scan that stops at the cap ends in a `Continue scan:` command that repeats `--max-requests`, with `scan stopped at --max-requests N` in the header (`capped: true`, `stop_reason: "max_requests"` in JSON).

If text-mode header lookups use the whole budget, the scan has not started: the header says so and shows no counts, and JSON's `total_items`/`total_pages` are `null`.

With `--owned`, the cap counts every send of the command but only stops what can stop: a scan and the collection read. The text header lookup and a server-side listing page are always sent (the header first, then the listing); the username and the collection pages are then asked only while the cap allows. So `get versions @m3719 --owned --max-requests 1` still sends the header and the listing (2 sends) and reads no collection: the rows read `owned ?`. With the cap already spent before the username, nothing is asked (JSON `owned: null`, `collection.records_read: 0`, `total_records: null`). A collection too big for the cap leaves the rows it did not reach `owned ?`, with a note saying how much was read (see [Marking rows with --owned](commands.md#marking-rows-with---owned)). Cache hits cost nothing, so a second run within the hour reads more of it for free.

`get wantlist --for-sale` has no default cap, like every scan, and spends a request per want, so an uncapped run over a long wantlist with few for-sale wants can take many minutes and end on a 429 once the per-minute budget is gone. Pass `--max-requests` (60 is one minute of budget; the minimum is 3). The cap counts every send, the username first, and is checked before each want, so a scan can stop in the middle of a wants page. A wants page is asked for only while at least 2 sends remain after the sends so far (the page and one want's stats), so when the cap runs out at a page boundary the scan stops with the boundary cursor without reading the next page. Even under `--fresh` the minimum cap checks one want per run. It ends in `Continue scan: agent-discogs get wantlist --for-sale --max-requests N --after P:R`, and the cursor names the first want not yet checked, so the continuation starts there and never checks one twice (within the hour, the wants already checked are cached and free anyway). See [get wantlist](commands.md#wantlist).

Agents whose tool calls have a time limit should pass `--max-requests`.

## No retries

The CLI reports; it never retries, sleeps, or throttles on your behalf. Each request is sent once: a 429, a 5xx, or a connection failure is reported as that request's error without a retry, and retrying is the caller's call. With several refs, the failing ref gets its error block and the rest still run (exit 1). What a 429 error says is in [Errors](errors.md#rate-limited-429).

One exception: a 429 partway through a client-side scan returns the rows read so far, exit 0, with a `Continue scan:` command and `stop_reason: "rate_limited"` (plus `retry_after` when Discogs sent one) under `--json`. A 429 on a scan's first page is the usual error.

On `get wantlist --for-sale` the 429 may come on any stats request, first page included: the rows matched so far are kept (exit 0, `stop_reason: "rate_limited"`), and the `Continue scan:` command resumes at the want the 429 answered. Only a 429 on the username or the first wants page is the usual `rate_limited` error. A want whose stats were not read is never reported as not for sale.

A 429 during the `--owned` collection read never fails the command, on any page: the rows it did not reach read `owned ?`, and `collection.stop_reason` is `"rate_limited"`. A listing whose own scan stopped on a 429 skips the collection read entirely.
