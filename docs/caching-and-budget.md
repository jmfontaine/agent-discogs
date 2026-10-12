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

`--fresh` stores nothing, so under it a text `get releases` scan spends one request on the header every run: with `--max-requests 1` it returns the empty capped page, and its header says to continue with a higher `--max-requests`, since the same cap would spend itself on the header again.

`--fresh` fetches every request from the API and does **not** store the response, so a later run without `--fresh` can still be served the older cached entry until its 1-hour TTL expires.

The `Next page:` / `Continue scan:` command printed under a list repeats `--cached` or `--fresh` (and `--max-requests`), so the next page is read under the same cache policy and cap as the first.

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
| `get releases @a...`, `get releases @l...`, `get versions @m...` | `--json`: 1; text: 2 (the header's artist, label or master name) |
| `get versions @r...` | 2, text or `--json` (the release, for its master, then the list) |
| Client-side scans: `get releases @a... --role`, `get releases @l... --year` | `--json`: one per 100 rows scanned; text: one more, for the header name |
| `search artist\|label`, `search --release-type all`, `search --release-type unofficial` (filtered by Discogs, without `--format`) | 1 |
| Other searches (`--release-type official`, the default, or `unofficial` with `--format`): client-side scans | one per 100 rows scanned |
| `search release ... --added-since` (experimental) | 1 per release the search matches in the window (before `--release-type` filtering), plus 1 per 100 search results and 1 for the release that ends the scan |
| Anything repeated within 1 hour | 0 (cached) |

A scan reads 100-row pages until the page is full or the list ends: a dense filter costs one request, and a sparse one over 2,000 results up to 20.

## Capping requests with --max-requests

`--max-requests N` caps the HTTP sends of the whole command, header lookups included (cache hits are free); it is accepted only where a scan runs (or with `--added-since`, see [Releases added since a date](commands.md#releases-added-since-a-date)) and with one ref, and must be 1 or more, otherwise `invalid_argument` before any request.

A scan that stops at the cap ends in a `Continue scan:` command that repeats `--max-requests`, with `scan stopped at --max-requests N` in the header (`capped: true`, `stop_reason: "max_requests"` in JSON).

If text-mode header lookups use the whole budget, the scan has not started: the header says so and shows no counts, and JSON's `total_items`/`total_pages` are `null`.

Agents whose tool calls have a time limit should pass `--max-requests`.

## No retries

The CLI reports; it never retries, sleeps, or throttles on your behalf. Each request is sent once: a 429, a 5xx, or a connection failure is reported as that request's error without a retry, and retrying is the caller's call. With several refs, the failing ref gets its error block and the rest still run (exit 1). What a 429 error says is in [Errors](errors.md#rate-limited-429).

One exception: a 429 partway through a client-side scan returns the rows read so far, exit 0, with a `Continue scan:` command and `stop_reason: "rate_limited"` (plus `retry_after` when Discogs sent one) under `--json`. A 429 on a scan's first page is the usual error.
