# Errors

## Layout

The CLI maps API errors to recovery-oriented messages with suggested next steps:

```
✗ Release 999999999 not found.
  Try: agent-discogs search "<title>"
✗ Authentication failed with DISCOGS_TOKEN.
  Check the token, or generate a new one at discogs.com/settings/developers. agent-discogs status shows the credentials in use.
✗ Rate limited: 0/60 requests left this minute.
  Retry in 30s. Discogs reported 60 req/min for the credentials in use (token).
✗ Not cached: GET /releases/352665
  Rerun without --cached to fetch it from the API.
✗ --owned reads your own Discogs account, but no credentials are set.
  Set a token: export DISCOGS_TOKEN=<token> (discogs.com/settings/developers), or use OAuth (DISCOGS_CONSUMER_KEY, DISCOGS_CONSUMER_SECRET, DISCOGS_ACCESS_TOKEN, DISCOGS_ACCESS_TOKEN_SECRET). agent-discogs status shows the credentials in use.
✗ Connection error: [Errno 61] Connection refused
  Check your network and retry.
✗ Connection error: 407 Proxy Authentication Required
  Retrying will not help: check your proxy, protocol, or network configuration.
✗ Cache error at /home/me/.cache/agent-discogs/cache.db: could not vet the database file: PermissionError: /home/me/.cache/agent-discogs/cache.db is not a regular file owned by the current user
  Fix the permissions or ownership of /home/me/.cache/agent-discogs and the files in it, or delete /home/me/.cache/agent-discogs/cache.db and any cache.db-wal or cache.db-shm beside it by hand. --fresh skips the cache.
```

Each error is a `✗` line with the message, then the hint, when there is one, on the next line, indented by two spaces. The same message and hint are `message` and `hint` in the `--json` envelope.

## Errors under --json

Errors under `--json` are a JSON document on stdout with exit code 1:

```json
{"error":{"code":"not_found","message":"Master @m... not found.","hint":"Try: agent-discogs search \"<title>\"","status":404}}
```

Codes:

- `not_found`
- `seller_settings_required`
- `auth_required`
- `forbidden`
- `rate_limited`: with `retry_after` in whole seconds, rounded up, when Discogs sends a usable `Retry-After`, as seconds or an HTTP date; `0` means retry now; and `limit`/`remaining` for the current minute when Discogs reports them
- `cache_miss`: only under `--cached`
- `cache_error`: with `path`, the cache database; see [Cache errors](caching-and-budget.md#cache-errors)
- `api_error`
- `connection_error`
- `bad_response`: Discogs answered with something the CLI cannot read: a non-JSON page, data that does not fit the expected shape, or a malformed results page; never your arguments, so retry later, with `--fresh` if the same error repeats
- `invalid_argument`
- `unexpected`: the message names the exception class

## Authentication failed (401)

A 401 names the credentials that were sent, as `status` reports them: `DISCOGS_TOKEN`, the consumer key/secret, or OAuth (with the variables behind each), and says what to check. A consumer key and secret identify an app, not a user, so that hint also points at `DISCOGS_TOKEN` or OAuth for price suggestions.

With no credentials set, the message reads `Authentication failed: no credentials are set.` and the hint says to export `DISCOGS_TOKEN`.

## --owned and get owned need a user

`--owned` and `get owned` read the authenticated user's own collection, so they need credentials that name a user: a token or OAuth. When `agent-discogs status` shows `consumer` or `none`, they fail with `auth_required` before any request is sent (the budget footer is absent):

- `--owned reads your own Discogs account, but no credentials are set.` (no credentials), or `--owned reads your own Discogs account, but a consumer key/secret identifies an app, not a user.` (`get owned reads ...` for the noun);
- the hint: `Set a token: export DISCOGS_TOKEN=<token> (discogs.com/settings/developers), or use OAuth (DISCOGS_CONSUMER_KEY, DISCOGS_CONSUMER_SECRET, DISCOGS_ACCESS_TOKEN, DISCOGS_ACCESS_TOKEN_SECRET). agent-discogs status shows the credentials in use.`

Under `--json` this is the usual envelope, without `status`, since no HTTP response exists. A 401 on a request that was sent is the [Authentication failed](#authentication-failed-401) case above; a 403 on the collection keeps the generic `forbidden` error.

## Rate limited (429)

When Discogs answers 429, the error says how much of the minute is left (`Rate limited: 0/60 requests left this minute.`), and under `--json` the envelope carries `retry_after`, `limit`, and `remaining`.

Without credentials, the hint adds `60 req/min with DISCOGS_TOKEN, 25 without.`; with any credentials it states the limit Discogs reported for them instead (`Discogs reported 60 req/min for the credentials in use (oauth).`), or the authenticated 60 req/min when the 429 carried no rate-limit headers.

See also [No retries](caching-and-budget.md#no-retries).

## API and unexpected errors

An API error the CLI has no specific message for reads `API error (503): <summary>`: the status once, then what Discogs said, collapsed to one line and cut at 200 characters. Any other unexpected failure reads `Unexpected error: <exception class>: <message>`.

## Connection errors

A connection failure reads `Connection error: <cause>`, the cause summarized like an API error's: one line, cut at 200 characters.

The hint says to retry only when the failure is transient (a refused or dropped connection, a timeout); a proxy error or an unsupported URL scheme fails the same way every time, so the hint points at the proxy, protocol, or network configuration instead.

## Closed stdout

If the reader closes stdout early (`agent-discogs ... | head -1`), the CLI exits 1 without an error message. Under `set -o pipefail` that exit 1 is expected, not a failed lookup.
