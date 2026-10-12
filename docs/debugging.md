# Debugging

## The --debug panel

`--debug` (or `AGENT_DISCOGS_DEBUG=1`) prints a trace panel to stderr after the [budget line](caching-and-budget.md#budget): one row per response with status and timing (`cache` for cache hits; a `--cached` miss or a failed connection has no response and no row), scan statistics for client-side filters, cache size, and output size with a rough token estimate.

The SDK's own log lines (`[sdk] HTTP request ...`, `[sdk] Cache hit ...`) stream to stderr in real time.

stdout is byte-identical with and without the flag, and so is the exit code: when the cache location cannot be resolved (no home directory and no absolute `XDG_CACHE_HOME`), the cache row reads `unknown (no home directory)` and the panel still prints.

```
api: 2 requests · 58/60 left this minute
── debug ───────────────────────────────────────────────
total     1310ms
GET /artists/3857                                200   338ms
GET /artists/3857/releases?page=1&per_page=100   200   947ms
scan      1 API calls, 100 rows fetched, 5 kept, not capped
cache     ~/.cache/agent-discogs  540.0 KB  ttl 1h
output    494 chars, 9 lines, ≈123 tokens
```

## Output encoding

Command output follows the encoding of stdout. When it is not UTF-8 (`PYTHONIOENCODING=ascii` or `latin-1`, some Windows pipes), a character the encoding cannot hold prints as a backslash escape (`宇多田` → `\u5b87\u591a\u7530`) and the command still succeeds; an encoding problem is never reported as an argument error. (click itself writes `--help` as UTF-8 when stdout is plain ASCII.)

## Discogs text in the text views

Discogs text is user-generated, so the text views never print it raw. A line break or tab inside a field becomes a space; any other control or bidi character prints as a visible escape (`\x1b`, `\u202e`), so a title cannot clear the screen, reorder the line, or start a line that looks like `Next page:` or `✗`.

Release notes (`get release -v`) keep their line breaks, with every continuation line indented under `Notes:`. Error messages on stderr get the same treatment. `--json` output keeps every value exact (see [JSON output](json-output.md)).
