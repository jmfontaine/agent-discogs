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

The `--owned` collection read shows in the panel as a `scan` row too, since it goes through the same scan machinery; its rows fetched and kept are equal.

The `get wantlist --for-sale` scan has a `scan` row as well: its API calls are the wants pages (100 wants each), its rows fetched every want on those pages (checked or not), and its rows kept the wants it kept. Each want's Marketplace stats lookup is a `GET /marketplace/stats/{id}` row of its own, which the scan row's API calls do not count.

## Output encoding

Command output follows the encoding of stdout. When it is not UTF-8 (`PYTHONIOENCODING=ascii` or `latin-1`, some Windows pipes), a character the encoding cannot hold prints as a backslash escape (`宇多田` → `\u5b87\u591a\u7530`) and the command still succeeds; an encoding problem is never reported as an argument error. (click itself writes `--help` as UTF-8 when stdout is plain ASCII.)

## Discogs text in the text views

Discogs text is user-generated, so the text views never print it raw. A line break or tab inside a field becomes a space; any other control or bidi character prints as a visible escape (`\x1b`, `\u202e`), so a title cannot clear the screen, reorder the line, or start a line that looks like `Next page:` or `✗`.

Release notes (`get release -v`) keep their line breaks, with every continuation line indented under `Notes:`. Error messages on stderr get the same treatment. `--json` output keeps every value exact (see [JSON output](json-output.md)), apart from the Discogs markup the projection converts in profiles and notes (see [Profiles and notes](commands.md#profiles-and-notes)).

That markup is converted before the text is neutralized, so a tag cannot smuggle a control character past it: a raw ESC inside `[b]...[/b]` still prints as `\x1b`.

## Color

Text output is colored for a person at a terminal and never for an agent reading it through a pipe, where an escape sequence would only cost tokens. The global `--color` flag precedes the command:

```bash
agent-discogs --color never get release @r847868
```

- `auto` (the default) colors stdout and stderr separately, each only when all of these hold: it is a terminal, `NO_COLOR` is unset or empty ([no-color.org](https://no-color.org)), and `TERM` is not `dumb`. Piped output is never colored, so it is byte-identical to output without color.
- `always` colors both streams even when piped, whatever `NO_COLOR` and `TERM` say.
- `never` turns color off.

Some agent harnesses run commands in a pseudo-terminal, which `auto` cannot tell from a person's terminal: set `NO_COLOR=1` there, or pass `--color never`. The same applies to a Windows console that does not interpret escape sequences (the legacy console host shows them as `←[36m`).

Only the CLI's own framing is colored, never a Discogs value: refs (`@r847868`) in cyan, section labels (`Tracklist:`, `Members:`, `Format:`) in bold, the `✗` of an error in red, the `⚠` budget warning in yellow, and secondary detail dimmed (page counts, the `Next page:` / `Continue scan:` prefix, the budget line, `--debug` timings). Color is applied after Discogs text is neutralized, so a title can neither carry a color code (it still prints as `\x1b`) nor be colored as framing: a ref or label inside a title stays plain.

The one exception is the markup Discogs writes into profiles and notes. A ref it links to (`[l647]`) prints as a ref, in cyan like any other, and its formatting tags become text attributes, never colors: `[b]` bold, `[i]` italic, `[u]` and a link's text underlined, `[s]` struck through. The CLI writes each of these codes itself and closes each one with a reset before the line ends, so a style cannot run into the next field or line.

A `--json` command is never colored, on either stream: its stdout, the JSON error envelope, and the budget line and `--debug` panel on stderr stay plain even with `--color always`. `--help` and `skills` are never colored either.
