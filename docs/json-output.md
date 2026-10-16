# JSON output

## Projections

`search`, `get`, `tracks`, and `price` accept `--json` (as do `cache clear`, which prints `{"cleared":true}` or the error envelope, and `skills list`, which prints one `{"name","description","version"}` object per bundled skill). The output is a compact projection of what the text view shows — refs, names, catalog numbers, counts — not the raw Discogs record, so a release is ~2 KB instead of ~30–50 KB of image URLs and bookkeeping.

Empty fields are omitted, so test for a key rather than for null; for the `pagination` envelope, see [Pagination](#pagination).

Each release, master, artist, label, search result, list (`get list`) and `get lists` row also carries `url`, its page on the Discogs website, always absolute: `"url":"https://www.discogs.com/release/847868-Nine-Inch-Nails-The-Downward-Spiral"`. Use it to hand a person a link; text output leaves it out to save tokens.

Rows of `get versions` and `get releases` carry `url` only when Discogs includes a link (`uri`) in the row, which it does not today, and the release facets (`tracks`, `price`, `get credits`, `get identifiers`) never do: use `get release @r... --json` for the link.

An artist or label `profile`, release `notes` (`-v`) and a list's `description` carry Discogs' markup converted as in the text view, line breaks kept: `[l647]` → `@l647`, `[a=Name]` → `Name`, `[url=https://host/path]text[/url]` → `text (host)`, and `[b]` and the other formatting tags removed, their text kept. The conversion adds no escape code. See [Profiles and notes](commands.md#profiles-and-notes) for every tag. `--json --full` keeps these fields as Discogs sent them, markup included.

`credits` is grouped by role: `{"Producer":[{"ref":"@a20661","name":"Flood","tracks":"1, 2"}],...}`. Tracklist rows carry `"type": "heading"` or `"type": "index"` when they are not plain tracks, and an index row nests its parts under `sub_tracks`.

Errors under `--json` are described in [Errors](errors.md#errors-under---json).

## Pagination

Lists are wrapped as `{"pagination":{...},"results":[...]}`. The `pagination` envelope is the exception to omitted empty fields:

- `page`, `total_items` and `total_pages` are always present, with `null` totals only when a scan stopped before reading any page;
- a filtered list always adds `filtered`, `capped` and `next_cursor`, `null` when nothing remains;
- a capped one adds `stop_reason`, plus `retry_after` when Discogs sent one;
- a label catalogue page (`get releases @l...`) that dropped rows equal to the row Discogs sent just before them adds `duplicates_dropped`, their count, only when it is above 0: `results` holds the remaining rows, and `total_items` and `total_pages` keep Discogs' counts, repeats included (see [Repeated rows in a label catalogue](commands.md#repeated-rows-in-a-label-catalogue)).

In a filtered list, the totals describe the listing before the filter, not your pages: `page` counts pages of `--limit` matching rows, while `total_items` is the unfiltered total (the `≤N` of the text header) and `total_pages` counts the 100-row pages the scan reads from Discogs.

For example, the second page of `get releases @l647 --year 1994 --limit 2 --json` reported `"page":2,"total_items":2723,"total_pages":28`: 28 is the number of Discogs pages a full scan reads, not the number of pages of matches, which nothing tells in advance. Continue with `next_cursor` until it is `null`.

## Owned rows

Under `--owned` (`search`, `get versions`, `get releases`, `get inventory`; see [Marking rows with --owned](commands.md#marking-rows-with---owned)), each release row gets `owned`, and the document gets `collection` next to `pagination`:

```json
{"pagination":{...},"results":[{"ref":"@r352665","title":"...","have":812,"owned":2},{"ref":"@r847868","title":"...","have":40,"owned":0}],"collection":{"complete":true,"records_read":250,"total_records":250}}
```

- `owned` is an integer, the number of copies you own, `0` when you own none, or `null` when the collection read stopped before it could tell. It is an exception to omitted empty fields: `0` and `null` are always present. `have` is Discogs' community count, not yours.
- `0` is reported only when the whole collection was read. After a partial read, a release found in the part read shows the count found so far (it may be short) and every other row is `null`.
- Only release rows carry `owned`; a master, artist or label row has none. Under `--full` it is added to each release record the SDK returns.
- `collection` is `{"complete": bool, "records_read": int, "total_records": int|null}`. A read that stopped early adds `stop_reason` (`"max_requests"` or `"rate_limited"`) and, for a 429 when Discogs sent a usable `Retry-After`, `retry_after`; `total_records` is `null` when nothing was read. A 429 on the username or the first collection page, or a listing scan that already stopped on one, reads nothing: `{"complete":false,"records_read":0,"total_records":null,"stop_reason":"rate_limited","retry_after":30}` and every release row has `owned: null`. A page with no release row has neither `owned` nor `collection`.

`get owned @r... --json` prints `{"ref":"@r352665","owned":2,"folders":["Vinyl","Uncategorized"]}`, or `{"ref":"@r847868","owned":0}` when not owned. Several refs give a list, with `{"ref","error"}` items for failures. `--full` prints `{"instances":[...]}`, your collection records for the release as the SDK normalizes them (their folders by id, not name).

## Collection

`get collection --json` is the paginated envelope (see [get collection](commands.md#collection)), one row per copy:

```json
{"pagination":{"page":1,"total_items":250,"total_pages":50},"results":[{"ref":"@r847868","title":"Nine Inch Nails - The Downward Spiral","year":1994,"label":"Nothing Records","catno":"92346-2","format":"CD (Album)","folder":"Vinyl","added":"2024-01-04T10:00:00-08:00","rating":4}]}
```

- Rows carry `ref`, `title` (`artist - title`), `year`, `label`, `catno` and `format`, then the copy's `folder` by name (`folder 123` for a folder the folder list does not name), `added` (when it was added to the collection, RFC 3339) and `rating`; `rating` `0` (unrated) is omitted, like any empty field. A record has no `url`. A release owned twice is two rows.
- With a filter (`--format`, `--genre`, `--style`, `--year`, `--label`) it is a filtered scan: `pagination` carries `filtered`, `capped`, `next_cursor`, and `stop_reason` / `retry_after` when it stopped early, and `total_items` is the whole collection, an upper bound on the matches (see [Pagination](#pagination)).
- `--full` prints the records as the SDK normalizes them (`CollectionItem`: `id`, `instance_id`, `folder_id`, `date_added`, `rating`, `notes`, `basic_information`): folders by id, so the folder list is not requested.

`get collection --value --json` is the collection's value in your profile's currency, each amount a number: `{"currency":"EUR","minimum":75.5,"median":100.25,"maximum":250.0}`. `--full` is `{"currency":"EUR","value":{"maximum":"€250.00","median":"€100.25","minimum":"€75.50"}}`: the SDK's `CollectionValue` record, its amounts the strings Discogs sent, unparsed.

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

## Inventory

`get inventory <username> --json` is the paginated envelope (see [get inventory](commands.md#inventory)), one row per listing:

```json
{"pagination":{"page":1,"total_items":300,"total_pages":60},"results":[{"listing":3254229818,"ref":"@r847868","title":"Nine Inch Nails - The Downward Spiral","format":"CD, Album","condition":"NM","sleeve":"VG+","price":12.5,"currency":"USD","ships_from":"Germany","listed":"2024-09-18T07:13:38-07:00","url":"https://www.discogs.com/sell/item/3254229818"}]}
```

- Rows carry `listing` (the listing's id), `ref` (the release's), `title` (`artist - title`), `format`, `condition` and `sleeve` (short codes: `M`, `NM`, `VG+`, `VG`, `G+`, `G`, `F`, `P`; a sleeve that is no grade is as Discogs sent it, `"Not Graded"`), `price` (a number) with its `currency` (ISO code), `ships_from`, `listed` (RFC 3339, as Discogs sends it, which is US Pacific time, not the seller's) and `url` (the listing's page, `https://www.discogs.com/sell/item/<id>`). Empty fields are omitted, so a listing with no release id has no `ref`.
- With `--owned`, each row gets `owned` (`null` for a listing with no release id) and the document gets `collection`, exactly as in [Owned rows](#owned-rows); the copies are looked up by the listing's release id, never the listing's own id.
- With a filter (`--condition`, `--sleeve`, `--ships-from`, `--max-price`, `--in-wantlist`) it is a filtered scan: `pagination` carries `filtered`, `capped`, `next_cursor`, and `stop_reason` / `retry_after` when it stopped early, and `total_items` is the seller's whole inventory, an upper bound on the matches (see [Pagination](#pagination)). With `--max-price`, `pagination` also carries `skipped_currency`, the number of listings that passed every other filter but were priced in another currency than `--currency` (in the part this run scanned), `0` included; a listing with no price is dropped and not counted.
- With `--in-wantlist`, the document has a top-level `wantlist` object: `{"complete":true,"wants_read":150,"total_wants":150}` after a full read of your wantlist. When that read stopped (`--max-requests` or a 429) it is `{"complete":false,"wants_read":100,"total_wants":150,"stop_reason":"max_requests"}` (`"rate_limited"` plus `retry_after` for a 429), no listing is scanned, and the document is `"results":[]` with `pagination` `{"page":1,"total_items":null,"total_pages":null,"filtered":true,"capped":true,"next_cursor":"1:0","stop_reason":"max_requests"}` (`next_cursor` is the cursor asked for, so continuing reads the wants already cached).
- `--full` prints the listings as the SDK normalizes them (`Listing`: `id`, `condition`, `sleeve_condition`, `price`, `original_price`, `shipping_price`, `ships_from`, `posted`, `release`, `seller`, ...); `shipping_price` is `null` because Discogs sends an empty one on listings today.

## Lists

`get lists --json` is the paginated envelope (see [lists and list](commands.md#lists-and-list)):

```json
{"pagination":{"page":1,"total_items":7,"total_pages":2},"results":[{"id":123,"name":"Pressings","description":"The best pressings.","public":false,"added":"2016-05-31T10:30:51-07:00","changed":"2016-06-01T20:00:00-07:00","url":"https://www.discogs.com/lists/Pressings/123"}]}
```

- Rows carry `id` (what `get list` takes), `name`, `description` (markup converted), `public` (kept when `false`), `added` and `changed` (RFC 3339 timestamps, as `--full` gives them), and `url`. Discogs sends no item count.
- `--full` prints the list records as the SDK normalizes them: `id`, `name`, `description`, `public`, `date_added`, `date_changed`, `uri`, `resource_url`.

`get list <id> --json` is one document per list (a JSON list of them, with `{"ref","error"}` items for failures, when several ids are given):

```json
{"id":123,"name":"Pressings","description":"The best pressings of @a3857.","public":false,"url":"https://www.discogs.com/lists/Pressings/123","items":[{"ref":"@r847868","title":"Nine Inch Nails - The Downward Spiral","comment":"check the matrix"},{"ref":"@l647","title":"Nothing Records"}]}
```

- Each item carries `ref` (`@r`, `@m`, `@a` or `@l`, from its type), `title` (Discogs' `display_title`) and `comment` when set, exactly as Discogs sent it. An item of a type with no ref prefix carries `id` and `type` instead of `ref`. An item has no `url`.
- `--full` prints the list record as the SDK normalizes it, its `items` with `display_title`, `comment`, `uri`, `resource_url` and `image_url`.
- A list with no items has no `items` key, like any empty field.

## Master year

`get release --master-year --json` adds `master_year` right after `master` (see [release](commands.md#release)):

```json
{"ref":"@r352665","title":"...","master":"@m3719","master_year":1994,...}
```

- When the master was not read, `master_year` is absent and `master_year_error` takes its place: `{"code":"cache_miss","message":"Not cached: GET /masters/3719"}` under `--cached`, or `"code":"not_found"` for a deleted master, with `hint` when there is one. The release document is complete either way.
- A release with no master, or a master year Discogs does not know, has neither key.
- `--json --full` adds a top-level `master_year` (or `master_year_error`) beside the release record's own keys, as `get price --full` adds `marketplace_stats`.
- Without `--master-year`, neither key appears in any shape.

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
