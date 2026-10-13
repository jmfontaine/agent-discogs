# JSON output

## Projections

`search`, `get`, `tracks`, and `price` accept `--json` (as does `cache clear`, which prints `{"cleared":true}` or the error envelope). The output is a compact projection of what the text view shows — refs, names, catalog numbers, counts — not the raw Discogs record, so a release is ~2 KB instead of ~30–50 KB of image URLs and bookkeeping.

Empty fields are omitted, so test for a key rather than for null; for the `pagination` envelope, see [Pagination](#pagination).

Each release, master, artist, label, and search result also carries `url`, its page on the Discogs website, always absolute: `"url":"https://www.discogs.com/release/847868-Nine-Inch-Nails-The-Downward-Spiral"`. Use it to hand a person a link; text output leaves it out to save tokens.

Rows of `get versions` and `get releases` carry `url` only when Discogs includes a link (`uri`) in the row, which it does not today, and the release facets (`tracks`, `price`, `get credits`, `get identifiers`) never do: use `get release @r... --json` for the link.

An artist or label `profile` and release `notes` (`-v`) carry Discogs' markup converted as in the text view, line breaks kept: `[l647]` → `@l647`, `[a=Name]` → `Name`, `[url=https://host/path]text[/url]` → `text (host)`, and `[b]` and the other formatting tags removed, their text kept. The conversion adds no escape code. See [Profiles and notes](commands.md#profiles-and-notes) for every tag. `--json --full` keeps both fields as Discogs sent them, markup included.

`credits` is grouped by role: `{"Producer":[{"ref":"@a20661","name":"Flood","tracks":"1, 2"}],...}`. Tracklist rows carry `"type": "heading"` or `"type": "index"` when they are not plain tracks, and an index row nests its parts under `sub_tracks`.

Errors under `--json` are described in [Errors](errors.md#errors-under---json).

## Pagination

Lists are wrapped as `{"pagination":{...},"results":[...]}`. The `pagination` envelope is the exception to omitted empty fields:

- `page`, `total_items` and `total_pages` are always present, with `null` totals only when a scan stopped before reading any page;
- a filtered list always adds `filtered`, `capped` and `next_cursor`, `null` when nothing remains;
- a capped one adds `stop_reason`, plus `retry_after` when Discogs sent one.

In a filtered list, the totals describe the listing before the filter, not your pages: `page` counts pages of `--limit` matching rows, while `total_items` is the unfiltered total (the `≤N` of the text header) and `total_pages` counts the 100-row pages the scan reads from Discogs.

For example, the second page of `get releases @l647 --year 1994 --limit 2 --json` reported `"page":2,"total_items":2723,"total_pages":28`: 28 is the number of Discogs pages a full scan reads, not the number of pages of matches, which nothing tells in advance. Continue with `next_cursor` until it is `null`.

## Owned rows

Under `--owned` (`search`, `get versions`, `get releases`; see [Marking rows with --owned](commands.md#marking-rows-with---owned)), each release row gets `owned`, and the document gets `collection` next to `pagination`:

```json
{"pagination":{...},"results":[{"ref":"@r352665","title":"...","have":812,"owned":2},{"ref":"@r847868","title":"...","have":40,"owned":0}],"collection":{"complete":true,"records_read":250,"total_records":250}}
```

- `owned` is an integer, the number of copies you own, `0` when you own none, or `null` when the collection read stopped before it could tell. It is an exception to omitted empty fields: `0` and `null` are always present. `have` is Discogs' community count, not yours.
- `0` is reported only when the whole collection was read. After a partial read, a release found in the part read shows the count found so far (it may be short) and every other row is `null`.
- Only release rows carry `owned`; a master, artist or label row has none. Under `--full` it is added to each release record the SDK returns.
- `collection` is `{"complete": bool, "records_read": int, "total_records": int|null}`. A read that stopped early adds `stop_reason` (`"max_requests"` or `"rate_limited"`) and, for a 429 when Discogs sent a usable `Retry-After`, `retry_after`; `total_records` is `null` when nothing was read. A 429 on the username or the first collection page, or a listing scan that already stopped on one, reads nothing: `{"complete":false,"records_read":0,"total_records":null,"stop_reason":"rate_limited","retry_after":30}` and every release row has `owned: null`. A page with no release row has neither `owned` nor `collection`.

`get owned @r... --json` prints `{"ref":"@r352665","owned":2,"folders":["Vinyl","Uncategorized"]}`, or `{"ref":"@r847868","owned":0}` when not owned. Several refs give a list, with `{"ref","error"}` items for failures. `--full` prints `{"instances":[...]}`, your collection records for the release as the SDK normalizes them (their folders by id, not name).

## Wantlist

`get wantlist --json` is the paginated envelope (see [get wantlist](commands.md#wantlist)):

```json
{"pagination":{"page":1,"total_items":150,"total_pages":30},"results":[{"ref":"@r847868","title":"Nine Inch Nails - The Downward Spiral","year":1994,"label":"Nothing Records","catno":"92346-2","format":"CD (Album)","notes":"Sealed only","rating":4}]}
```

- Rows carry `ref`, `title` (`artist - title`), `year`, `label`, `catno` and `format`, plus `notes` and `rating` when set; `rating` `0` (unrated) is omitted, like any empty field. A want has no `url`.
- `--full` prints the want records as the SDK normalizes them: `id`, `basic_information`, `notes`, `rating`, `resource_url`.

With `--for-sale` (a filtered scan, so `pagination` carries `filtered`, `capped`, `next_cursor`, and `stop_reason` / `retry_after` when it stopped early; see [Pagination](#pagination)) each kept row adds `market`, the shape `price` uses:

```json
{"pagination":{"page":1,"total_items":10,"total_pages":1,"filtered":true,"capped":false,"next_cursor":null},"results":[{"ref":"@r847868","title":"Nine Inch Nails - The Downward Spiral","year":1994,"label":"Nothing Records","catno":"92346-2","format":"CD (Album)","notes":"Sealed only","rating":4,"market":{"for_sale":3,"lowest":12.5,"currency":"EUR"}}]}
```

- `market` is `{"for_sale": int, "lowest": number, "currency": string}`: copies on sale and the lowest asking price across every condition and location, in the `--currency` asked for. `lowest` and `currency` are omitted when Discogs gives no price. Condition, sleeve condition, seller location and shipping are not available: the API lists no individual copies.
- `total_items` is the whole wantlist, not the number for sale; `capped: true` with `stop_reason: "max_requests"` or `"rate_limited"` means more wants remain unchecked, and `next_cursor` continues from the first one.
- Under `--cached`, a want whose stats are not cached is a row with no `market` and `error: {"code":"cache_miss","message":"Not cached: GET /marketplace/stats/5","hint":"..."}`, and the command exits 1. It was not checked: do not read it as not for sale. `--full` keeps the `error` and adds `market` to the want records the same way.

## --full

```bash
agent-discogs get release @r352665 --json            # projection
agent-discogs get release @r352665 --json -v         # plus notes, credits, and identifiers
agent-discogs get release @r352665 --json --full     # the complete record, when you need a field the projection drops
agent-discogs get credits @r352665 --json --full     # complete release credits, plus track_credits: [{position, title, extra_artists}]
```

`--full` prints the complete record as the SDK normalizes it, not byte-for-byte what Discogs sends:

- field names are consistent (`catalog_number` for `catno`, `extra_artists` for `extraartists`, `name_variation` for `anv`, a track's `type` for `type_`);
- a search result's `uri` is absolute (`https://www.discogs.com/release/1`, not `/release/1`);
- timestamps are RFC 3339 with an offset (`"date_added":"2005-01-03T23:17:26-08:00"`), and a timestamp that is absent or does not parse is `null`.

Fields the SDK does not model appear as Discogs sends them. Unlike the projection, `--full` keeps empty fields, as `null` when unset.

## Non-ASCII and control characters

On a UTF-8 stdout, non-ASCII text is written as is (`"title":"無罪モラトリアム"`), which keeps Japanese, Cyrillic, or accented titles short. On any other stdout encoding, it is written as `\uXXXX` escapes so the document stays valid.

Either way, C1 controls (U+0080–U+009F), bidi controls (U+061C, U+200E, U+200F, U+202A–U+202E, U+2066–U+2069), U+2028/U+2029, and lone surrogates are always escaped. The escaping is lossless: `json.loads` returns every string unchanged.
