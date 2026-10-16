# Errors

A failed command exits 1. In text, its error is a `✗` line with a hint on stderr; with several refs, a failing ref's `✗` block takes its place among the others on stdout, and under `--cached` a `get wantlist --for-sale` want whose stats are not cached is a row ending `· ✗ Not cached: ...`. Under `--json` the error is a document on stdout.

## Error codes

With `--json`, errors are a JSON document on stdout and exit code 1: `{"error":{"code":"not_found","message":"Master @m... not found.","hint":"Try: agent-discogs search \"<title>\"","status":404}}`. Branch on `error.code`: `not_found`, `seller_settings_required`, `auth_required`, `forbidden`, `rate_limited` (with `retry_after` whole seconds, rounded up, when Discogs sends a usable `Retry-After`; `0` means retry now; plus `limit`/`remaining` for the current minute), `cache_miss` (only under `--cached`), `cache_error` (the local cache is unusable; `path` names the database), `api_error`, `connection_error`, `bad_response` (Discogs sent something unreadable: not JSON, or data of the wrong shape; not your input, so retry later rather than changing the command, with `--fresh` if the same error repeats), `invalid_argument`, `unexpected` (the message names the exception class).

## auth_required

**`auth_required` (401, exit 1):** the message names the credentials that were sent, in the mode `status` reports. The hint says what to check, then `agent-discogs status shows the credentials in use.` Relay it to the user, who fixes the credentials; retrying the same command will not help.

| Mode | Message | Hint (before the `status` pointer) |
|------|---------|------------------------------------|
| `token` | `Authentication failed with DISCOGS_TOKEN.` | Check the token, or generate a new one at discogs.com/settings/developers. |
| `consumer` | `Authentication failed with the consumer key/secret (DISCOGS_CONSUMER_KEY, DISCOGS_CONSUMER_SECRET).` | Check both at discogs.com/settings/developers. They identify an app, not a user: price suggestions need DISCOGS_TOKEN or OAuth. |
| `oauth` | `Authentication failed with OAuth (DISCOGS_CONSUMER_KEY, DISCOGS_CONSUMER_SECRET, DISCOGS_ACCESS_TOKEN, DISCOGS_ACCESS_TOKEN_SECRET).` | Check all four; the access token may have been revoked. |
| `none` | `Authentication failed: no credentials are set.` | Set token: export DISCOGS_TOKEN=<token> (discogs.com/settings/developers). |

**`auth_required` before any request (`--owned`, `--in-wantlist`, `get owned`, `get collection`, `get lists`, `get wantlist`):** these read the authenticated user's own collection, lists or wantlist, so they need `token` or `oauth` in `status`. Under `consumer` or `none` they fail without sending anything: message `--owned reads your own Discogs account, but no credentials are set.` (or `... but a consumer key/secret identifies an app, not a user.`; `--in-wantlist reads ...`, `get owned reads ...`, `get collection reads ...`, `get lists reads ...` and `get wantlist reads ...` for the others), hint `Set a token: export DISCOGS_TOKEN=<token> (discogs.com/settings/developers), or use OAuth (DISCOGS_CONSUMER_KEY, DISCOGS_CONSUMER_SECRET, DISCOGS_ACCESS_TOKEN, DISCOGS_ACCESS_TOKEN_SECRET). agent-discogs status shows the credentials in use.` No `status` field in the JSON envelope. A 403 stays the generic `forbidden`. `get list` needs no user: a public list reads without credentials, and someone else's private list fails with whatever Discogs answered (`not_found`, `auth_required` or `forbidden`). Neither does `get inventory` alone: Discogs shows anyone a seller's For Sale listings.

## rate_limited

**`rate_limited` (429, exit 1):** the message says what is left of the minute (`Rate limited: 0/60 requests left this minute.`; `Rate limit exceeded.` when Discogs sent no rate-limit headers), with `limit`/`remaining` in JSON when Discogs sends them. The hint starts with the wait (`Retry in 30s.`, `retry_after` in JSON; `Wait a moment and retry.` without a usable `Retry-After`), then the limit that applies:

- With credentials, the limit Discogs reported for them, e.g. `Discogs reported 60 req/min for the credentials in use (oauth).`, or `Discogs allows 60 req/min for the credentials in use (oauth).` when the 429 carried no rate-limit headers. Setting `DISCOGS_TOKEN` is never suggested here: wait, or continue with `--cached`.
- Without any credentials, `60 req/min with DISCOGS_TOKEN, 25 without.`

## bad_response

**`bad_response` (exit 1):** Discogs answered with something the CLI cannot read: a non-JSON body (a gateway error page), a record that does not fit the SDK's models, or a malformed results page (`"results" is an object, expected an array`). The message names the problem (a validation failure names its first failing field, never the value), bounded to one line. It is never caused by your arguments, so do not change the command: retry in a moment. Such a response is not cached, so a plain retry asks Discogs again, with one exception: a price guide whose conditions are malformed is cached before they are read, so if the same error repeats, rerun with `--fresh`.

**`bad_response` with an unknown format (exit 1):** `get collection --value` reads numbers out of the formatted amounts Discogs sends (`"€1.250,00"`) and the currency from your profile's `curr_abbr`. An amount in a shape it does not know, or a profile without a currency code, is `bad_response` too, but its hint says the format is unknown and asks for a report at https://github.com/jmfontaine/agent-discogs/issues: retrying will not help. The message names the field (`the collection value's median`, `currency_code`), never the value; `get collection --value --json --full` prints the amounts as sent.

## cache_error

The local response cache cannot be used (its directory cannot be created, or `cache.db` is not a regular file you own, or SQLite cannot open it), or `cache clear` could not purge it (locked by another process, read-only, or damaged; the database is left as it was). The message names the path and what failed; the hint says what to fix. Do not delete anything yourself: relay the message and hint to the user, who retries, fixes the permissions or ownership, or deletes the file. Meanwhile `--fresh` still works for lookups, since it never opens the cache, but every request then spends budget.

## connection_error

The message names the cause (`Connection error: [Errno 61] Connection refused`). Follow the hint: `Check your network and retry.` means a transient failure (refused or dropped connection, timeout), so one retry may succeed; `Retrying will not help: ...` means a proxy, URL-scheme or protocol failure that repeats every time, so fix the configuration instead of retrying.

## invalid_argument

`--limit` takes 1-100, `--page` starts at 1, and a search needs a query or at least one non-blank search filter (`--year`, `--catno`, ...; a type prefix or `--release-type` alone is not enough). These fail with `invalid_argument` before any request, so a rejected command spent nothing. A flag the noun does not take fails the same way (`--x does not apply to 'get noun'`), and the error lists what the noun takes: drop the flag. Which noun takes which flag, and where `--after` and `--max-requests` apply: [Which flags apply where](commands.md#which-flags-apply-where).

## Exit 1 with no message after `| head`

The reader closed the pipe early; the CLI stops quietly. Nothing failed: rerun without the pipe, or with `--limit`, if you need the full output.
