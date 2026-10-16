# Commands

Each command, its own flags and its text output. The global flags (`--cached`, `--fresh`, `--debug`, `--color`), the output modes (`--json`, `--full`), the `--max-requests` cap and errors are described on the pages that cover them:

- `--json` and `--full`: [JSON output](json-output.md) and its [--full](json-output.md#--full) section;
- `--cached` and `--fresh`: [--cached and --fresh](caching-and-budget.md#--cached-and---fresh);
- `--max-requests`: [Capping requests with --max-requests](caching-and-budget.md#capping-requests-with---max-requests), next to what each command costs;
- `--debug`: [The --debug panel](debugging.md#the---debug-panel);
- `--color`: [Color](debugging.md#color);
- error messages: [Errors](errors.md).

## search

Search the Discogs database. Aliases: `find`, `query`.

```bash
agent-discogs search "The Downward Spiral"
agent-discogs search release "The Downward Spiral" --year 1994
agent-discogs search artist "Nine Inch Nails" --limit 10
agent-discogs search label "Nothing Records"
agent-discogs search release "The Downward Spiral" --release-type all
agent-discogs search release "When The Whip Comes Down" --release-type unofficial
agent-discogs search release --artist "Nine Inch Nails" --sort date_added --desc
agent-discogs search release --artist "Nine Inch Nails" --release-type all --added-since 2026-07-08
agent-discogs search release --artist "Nine Inch Nails" --year 1994 --release-type unofficial --limit 100 --owned
```

### Types and paging

Prefix a type (`release`, `master`, `artist`, `label`) to narrow results. Use `--limit` (1-100, default: 5) for page size.

Discogs occasionally returns a result whose type is not one of those four, or has none. Such a row has no ref, because nothing says what its ID refers to: it reads `id 9001 [playlist] "..."` in text (`[no type]` when Discogs sends none) and carries `id` instead of `ref` under `--json`. The rest of the page prints as usual.

To continue, paste the `Next page:` command printed under the results: the default `--release-type official` filter is applied client-side, so continuation is a cursor (`--after`), not a page number, and the total is an upper bound (`≤N`).

`--release-type unofficial` is filtered by Discogs itself (`format=Unofficial Release`), so it shows an exact total and pages with `--page`; combined with `--format` it falls back to the client-side scan, because Discogs cannot reliably combine two format filters. `--page` (from 1) works with those server-side pages, `--release-type all`, and artist/label searches.

Discogs' "Unofficial Release" covers bootlegs, counterfeits and pirate copies alike, and no field tells them apart, so `--release-type unofficial` returns all three. To keep bootlegs only, check each row's master: the bundled pressings guide (`agent-discogs skills get pressings --full`, section "Unofficial Releases: Bootlegs, Counterfeits, Pirates") describes how.

A search needs a query or at least one non-blank filter; an out-of-range `--limit`/`--page` or an empty search fails with `invalid_argument` before any request.

A query starting with `-` goes after `--` (`agent-discogs search --release-type all -- -ambient`); the printed `Next page:` command always puts the type and query there, so it pastes back unchanged.

### Filters

Filters: `--artist`, `--barcode`, `--catno`, `--country`, `--format`, `--genre`, `--label`, `--release-type {official,unofficial,all}` (default: official), `--style`, `--year`. Use `--json` for JSON output (see [JSON output](json-output.md)).

### Sorting

`search release ... --sort date_added` orders results by when they were added to Discogs; add `--desc` for newest first.

The parameter is undocumented: Discogs lists no sort keys for search, and it has only been seen working on release searches, so `--sort` on any other search (or `--desc` without `--sort`) fails with `invalid_argument` before any request. If Discogs ever drops it, results will most likely come back in relevance order with no error.

With the default `--release-type official` filter, the client-side scan keeps the server's order, and the printed `Next page:` command keeps `--sort` and `--desc`.

### Client-side scans

Client-side scans (`--release-type official`, or `unofficial` with `--format`; `get releases --role`; `get releases @l... --year`; `get versions --release-type official`, or `unofficial` with `--format`; `get collection` with a filter; `get inventory` with a filter) read Discogs' largest pages, 100 rows each, until `--limit` rows match or the list ends. There is no cap by default, so a sparse filter over a long list can take many requests.

To bound one, pass `--max-requests N` (see [Capping requests with --max-requests](caching-and-budget.md#capping-requests-with---max-requests)).

When Discogs answers 429 partway through a scan, the rows read so far are returned with a `Continue scan:` command and a `scan stopped: rate limited` note (`stop_reason: "rate_limited"` and `retry_after` in JSON), exit 0; a 429 on the first page is an error.

### Releases added since a date

`search release ... --added-since YYYY-MM-DD` lists the releases added to Discogs on or after that day (inclusive, from midnight UTC), newest first, each row ending in its added day in UTC (`· added 2026-09-01`; `added`, a full RFC 3339 timestamp, under `--json`).

It is **experimental**: Discogs has no date filter and search results carry no added date, so it walks the undocumented `date_added` search order and looks up each release to read its date (the SDK's `releases_added_since()`), stopping at the first release older than the cutoff.

It is complete only while Discogs keeps that order; when a looked-up date breaks it, the command fails with `bad_response` rather than return a doubtful list (the responses are cached, so a plain retry repeats the error for up to an hour; `--fresh` asks Discogs again).

- It costs one request per release the search matches in the window, before `--release-type` filtering, plus one per 100 search results and one for the release that ends the scan; a repeat within the hour is free.
- There is no cap by default: `--max-requests N` stops it after N sends, and the result then ends with `Stopped at --max-requests N: the result may be incomplete` (JSON: `complete: false`; `total` counts the rows found). Pass `--max-requests` if your tool calls have a time limit.
- The CLI never waits out the rate limit: a 429 partway through fails the command with `rate_limited`, and a rerun after `retry_after` resends none of the lookups already made.
- The whole window comes back unless you pass `--limit`, which cuts the rows, not the requests.
- `--release-type official` (the default) and `unofficial` with `--format` filter on the looked-up formats at no extra request; `unofficial` alone is filtered by Discogs (`format=Unofficial Release`), which saves the lookups of official releases.
- It needs an explicit `search release ...` and rejects `--sort`, `--desc`, `--page` and `--after` (`invalid_argument`, before any request).
- Under `--cached`, any lookup the cache does not hold fails the command with `cache_miss`.

### Marking rows with --owned

`--owned` marks every release row with the copies in your own Discogs collection, on `search`, `get versions`, `get releases` (artist and label) and `get inventory`. A row ends in `· owned` (one copy), `· owned ×2` (two or more), `· not owned`, or `· owned ?` when the read did not reach it (below). In `--json`, each row gets `owned`, the count, and the document gets a `collection` object (see [JSON output](json-output.md#owned-rows)).

It is not the `have 1,234` on a row, which is Discogs' community count: how many Discogs users own that release. `owned` is the authenticated user's own copies.

```bash
agent-discogs search release --artist "Nine Inch Nails" --year 1994 --release-type unofficial --limit 100 --owned
agent-discogs get versions @m3719 --country US --owned
agent-discogs get releases @a3857 --role Main --owned --max-requests 6
```

- **Release rows only.** A collection holds releases and Discogs does not document a master id on its records, so master, artist and label rows (an untyped search, or an artist's discography, lists some) get no mark. `search artist|label|master ... --owned` and `--owned` with `--added-since` fail with `invalid_argument` before any request, as does `--owned` on any `get` noun but `releases`, `versions` and `inventory`. A page with no release row reads nothing. On `get inventory` a row is looked up by its listing's release id (never the listing's own id), and a listing with no release id reads `owned ?` (JSON `owned: null`).
- **It needs a user.** A token or OAuth credentials name one; a consumer key/secret or no credentials do not, and the command fails with `auth_required` before any request (see [Errors](errors.md#--owned---in-wantlist-get-owned-get-collection-get-lists-and-get-wantlist-need-a-user)). It reads only the authenticated user's own collection: there is no `--user` flag.
- **Cost.** After the listing page, one request for the username, then your whole collection (the "All" folder) at 100 records per request, read once per command even with several refs (`get versions @m... @m... --owned`). Every response is cached for an hour, so a rerun costs nothing. See [What commands cost](caching-and-budget.md#what-commands-cost).
- **`--max-requests N`** counts every send of the command (the text header lookup, the listing page, the username and the collection pages; cache hits are free), but it only stops a scan and the collection read. The header and a server-side listing page are always sent, header first; the username and the collection pages are then asked only while the cap allows. So `get versions @m3719 --owned --max-requests 1` still sends the header and the listing (2 sends) and reads no collection, and a cold server-side `search release ... --release-type all --owned --max-requests 3` is the listing, the username and one collection page. It is accepted on `search` (otherwise rejected on a server-side search), on `get versions`, on `get releases` and on `get inventory` (otherwise rejected unless a filter or `--owned` is given), with one ref only.
- **A read that stops early** (the cap, or a 429) never claims a release is not owned: a row found in the part read shows the count found so far (it may be short), and any other row reads `· owned ?` (JSON `owned: null`). A note before the footer says why: `Owned: the collection read stopped at --max-requests 2 (100 of 250 records read): "owned ?" rows are not in the part read, and a count may be short.` or `... the collection read was rate limited, retry in 30s (100 of 250 records read): ...`. Rerun with a higher `--max-requests`, or after the wait. A 429 on a later collection page keeps the records read. A 429 on the username or the first collection page, or a listing scan that already stopped on one (`pagination.stop_reason: "rate_limited"`; the collection is then not read at all, with no request), leaves every release row `owned ?`; the command still succeeds, and JSON `collection` is `{"complete":false,"records_read":0,"total_records":null,"stop_reason":"rate_limited","retry_after":N}` (the listing's `retry_after` in the second case).
- **Paging.** The printed `Next page:` / `Continue scan:` command repeats `--owned`, so the next page is marked too; within the hour the collection comes from the cache.
- **Freshness.** A record added to your collection within the last hour may still show as not owned; `--fresh` re-reads the collection. Under `--cached`, a collection page the cache does not hold fails the command with `cache_miss`.

## get

Get entity details by noun and ref. Aliases: `fetch`, `show`.

```bash
agent-discogs get release @r352665
agent-discogs get artist @a3857
agent-discogs get master @m3719
agent-discogs get label @l647
agent-discogs get tracklist @r352665
agent-discogs get releases @a3857 --limit 20
agent-discogs get versions @m3719 --country US --format Vinyl
agent-discogs get price @r352665
agent-discogs get owned @r352665 @r847868
agent-discogs get versions @m3719 --country US --owned
agent-discogs get collection --sort added --desc
agent-discogs get collection --style Techno --format 12\" --max-requests 20
agent-discogs get collection --value
agent-discogs get wantlist
agent-discogs get wantlist --for-sale --max-price 20 --currency EUR --max-requests 60
agent-discogs get inventory redscroll --sort price
agent-discogs get inventory redscroll --condition NM --ships-from Germany --max-requests 20
agent-discogs get inventory redscroll --in-wantlist --condition VG+ --max-requests 60
agent-discogs get lists
agent-discogs get list 123
```

Nouns: `artist`, `collection` (takes no ref), `credits`, `identifiers` (alias `ids`), `inventory` (takes a seller's username), `label`, `list` (takes a list id), `lists` (takes no ref), `master`, `owned`, `price`, `release`, `releases`, `tracklist`, `versions`, `wantlist` (takes no ref). Use `--json` for JSON output.

### release

`release` output carries inline refs for its artists and label (`[@a...]`, `[@l...]`), plus country, release date, and the day the release was added to Discogs (`Country: US · Released: 1994-03-08 · Added: 2007-02-23`; the date is the one in the timestamp's own offset, and the field is omitted when Discogs has none).

Under `--json` that date is `added`, a full RFC 3339 timestamp such as `2007-02-23T01:13:39-08:00`. It answers "is this a new entry or an old one?" at no extra request.

Unknown date parts are left out, in text and `--json` alike: Discogs' year `0` is omitted, and a release date drops a zero month or day (`1994-05-00` shows as `1994-05`, `1994-00-00` as `1994`, and the text view prints no `Released:` when that equals the year).

Its format lists one entry per physical format, separated by `;`: the quantity when above one, the name, then descriptions and free text in parentheses (`Format: 2×Vinyl (LP, Album, 180g, Gatefold); CD (Album)`; `--json` carries the same string).

`-v, --verbose` on `release` appends notes, credits, and identifiers in one call; `-c, --compact` replaces the tracklist with a one-line `Tracks: 14 (65:01)` summary; the total is omitted when any track lacks a duration (use `tracks` when the tracklist is what you want).

Index tracks (a suite or medley split into parts) list their sub-tracks indented beneath them. The compact count includes each part, and the total sums the parts' durations; when a part is untimed, the index track's own duration stands in for the suite.

### Profiles and notes

`artist` and `label` print a profile, cut to about 300 characters, `release -v` prints the release notes, and `list` prints the list's whole description. Discogs writes links and formatting into these fields as markup, which its website renders; the CLI converts it in the text view and the `--json` projection alike, so a link to another entity arrives as a ref you can pass straight back to `get`:

| Discogs markup | Printed |
|---|---|
| `[a3857]`, `[l647]`, `[r352665]`, `[m3719]`, and the title-only `[r=352665]`, `[m=3719]` | the ref: `@a3857`, `@l647`, `@r352665`, `@m3719` |
| `[a=Name]`, `[l=Name]` | `Name` (not resolved to a ref: that would take a search per name, which may not match exactly) |
| `[url=https://host/path]text[/url]` | `text (host)`, without a leading `www.` |
| `[url]https://host/path[/url]` | `host` |
| `[g1.2.3]` (a database guideline), `[t=262]` (a forum topic) | the text Discogs shows: `RSG §1.2.3`, `Forum Topic #262` |
| `[b]`, `[i]`, `[u]`, `[s]` and their closing tags | the text alone; with color on, bold, italic, underlined or struck through |
| anything else in brackets, uppercase tags included (`[A3857]`, `[Booklet]`) | unchanged |

An opening tag Discogs never closes ends with the field, and a closing tag with nothing to close is dropped. The cut (profiles only; notes and list descriptions are never cut) counts the converted text and never splits a ref. `--json --full` keeps these fields exactly as Discogs sent them, markup included.

### credits and identifiers

`credits` lists who did what on a release, grouped by role, each person with an artist ref. It includes credits Discogs files on individual tracks and sub-tracks: each person appears once per role with the tracks the credit covers, and no track list means the whole release.

`identifiers` lists barcodes, matrix/runout etchings, and other codes: the data that tells two pressings with the same catalog number apart.

### owned

`get owned @r... [@r...]` tells whether the authenticated user owns each release, and in which folders:

```
@r352665 owned ×2: Vinyl, Uncategorized
@r847868 not owned
```

It asks about one release at a time (`GET /users/{username}/collection/releases/{id}`), so it is the cheap way to check a few refs; for a page of rows, use `--owned` instead. Several refs work as with the other nouns: blocks separated by a blank line, a failing ref as its `✗` block, exit 1 if any failed.

- **Cost:** one request for the username (once per command), one per ref, and one for the folder list when any ref is owned. A copy in a folder Discogs does not name adds no folder name: with no names at all the text is `@r352665 owned` (no colon) and JSON has no `folders`.
- **`--json`:** `{"ref":"@r352665","owned":2,"folders":["Vinyl","Uncategorized"]}`, or `{"ref":"@r847868","owned":0}` when not owned; several refs give a list, with `{"ref","error"}` items for failures. `--json --full` is `{"instances":[...]}`, the collection records as the SDK normalizes them (each names its folder by id), and sends no folder-list request.
- **Needs a token or OAuth**, like `--owned` (`auth_required` before any request otherwise). Only `--json` and `--full` apply.

### collection

`get collection` lists your own Discogs collection, one row per copy (a release you own twice shows twice), five a page by default:

```
Collection: me (page 1, 5 of 250 records)

@r847868 "Nine Inch Nails - The Downward Spiral" 1994 · Nothing Records 92346-2 · CD (Album) · folder Vinyl · added 2024-01-04 · rating 4
...

Next page: agent-discogs get collection --page 2
```

Each row is the ref, `artist - title`, year, label and catalog number, format, then the copy's folder by name (`· folder 123` for one the folder list does not name), `· added YYYY-MM-DD` (the day it was added to the collection, in the timestamp's own offset, as JSON `added` gives it) and `· rating N` when you rated it. Use `--limit` (1-100, default: 5) and `--page` (from 1); the printed `Next page:` command repeats `--limit`, the filters and the sort.

- **Sorting.** `--sort label|artist|title|catno|format|rating|added|year` and `--desc` are sent to Discogs (`sort`, `sort_order`), so they order the whole collection, not just a page; Tab completes the keys. An unknown key, or `--desc` without `--sort`, fails with `invalid_argument` before any request.
- **Filters.** `--format`, `--genre`, `--style`, `--year` and `--label` keep the records with that value: a format name or description (`Vinyl`, `12"`, `LP`), a genre, a style, a label name (any of the record's labels, though the row shows the first), the year. A value matches one value of the record exactly, ignoring case: `--style Tech` matches nothing, `--style techno` matches `Techno`. Several filters must all match; an empty value fails with `invalid_argument`. Collection records carry no country, so there is no `--country`.
- **A filter makes it a client-side scan.** Discogs cannot filter a collection, so the CLI reads 100-record pages of it in the requested order and keeps the matches until `--limit` is filled or the collection ends, like the other [client-side scans](#client-side-scans): the header shows `≤N` totals, `--page` is rejected, the `Continue scan:` / `Next page:` command continues with `--after`, `--max-requests N` caps the sends, and a 429 after the first page keeps the rows read so far (a 429 on the first page is the usual `rate_limited` error). Without `--sort`, these are the same requests the `--owned` read sends, so a scan after an `--owned` listing within the hour reads its pages from the cache.
- **Cost.** One request for the username (shared with `--owned`, `get owned`, `get lists` and `get wantlist`), one for your folder names, then one per page (or per 100 records scanned). The username and the folder list are read before the listing, the way `get releases` reads its header first, so `--max-requests` counts them: `--max-requests 2` on a cold cache spends itself on them and returns an empty capped page, and a cap the username alone spends skips the folder list too. `--json --full` names folders by id (`folder_id`) and skips the folder list. Every response is cached for an hour, so a rerun costs nothing.
- **It takes no ref and needs a user.** `get collection @r847868` fails with `invalid_argument`, as do `--max-requests` and `--after` without a filter. A token or OAuth credentials name a user; a consumer key/secret or no credentials do not, and the command fails with `auth_required` before any request (see [Errors](errors.md#--owned---in-wantlist-get-owned-get-collection-get-lists-and-get-wantlist-need-a-user)). It reads only the authenticated user's own collection: there is no `--user` flag, no folder selection, and the CLI neither adds, moves, rates nor removes records.
- **`--cached`.** The username, the folder list or a page the cache does not hold fails the command with `cache_miss`.
- **`--json`:** `{"pagination":{...},"results":[{"ref","title","year","label","catno","format","folder","added","rating"}]}`, `added` an RFC 3339 timestamp and `rating` omitted when unrated. `--json --full` prints the records as the SDK normalizes them (`CollectionItem`: `id`, `instance_id`, `folder_id`, `date_added`, `rating`, `notes`, `basic_information`). See [JSON output](json-output.md#collection).

#### --value

`get collection --value` prints what your collection is worth, by Discogs' estimate from Marketplace sales, in place of the listing:

```
Collection value (EUR): min 75.50 · median 100.25 · max 250.00
```

- **Currency.** Discogs writes the three amounts as formatted strings (`"€1.250,00"`) and names no currency, and a symbol cannot name one (`$` is USD, CAD, AUD...). So the code comes from your profile's currency (`curr_abbr`), and each amount is printed as a bare number after it, like every price the CLI prints.
- **Reading the amounts.** Whatever stands before the first digit or after the last one (`$`, `€`, `kr`, `CHF `) is the currency symbol and is dropped, unless it holds a sign or parentheses (`-$5.00`, `($5.00)`) or ends in `.` or `,` right before the first digit (`.50`). The decimal separator is the last `.` or `,` when one or two digits follow it; every other `.`, `,`, `'` (or `’`), space, no-break space or narrow no-break space separates thousands, in groups of three after a first group that does not start with 0 (`1,250` and `1.250` both read 1250; `0.250` is rejected). An amount in any other shape, or a profile without a currency code, fails with `bad_response`, naming the field but never its value, and a hint saying the format is unknown: retrying will not help, so report it at https://github.com/jmfontaine/agent-discogs/issues. `--json --full` prints the amounts as Discogs sent them.
- **Cost.** One request for the username (shared with the rest of the command, as for every user noun), one for your profile, one for the value; a rerun within the hour costs nothing.
- **It takes no other flag.** `get collection --value --limit 5`, or any other `get collection` flag with it, fails with `invalid_argument` before any request. Like the listing, it needs a token or OAuth (`auth_required` before any request otherwise), and Discogs serves the value to the collection's owner only.
- **`--json`:** `{"currency":"EUR","minimum":75.5,"median":100.25,"maximum":250.0}`. `--json --full`: `{"currency":"EUR","value":{"maximum":"€250.00","median":"€100.25","minimum":"€75.50"}}`, the SDK's `CollectionValue` record as sent, unparsed. See [JSON output](json-output.md#collection).

### wantlist

`get wantlist` lists your own Discogs wantlist, five wants a page by default:

```
Wantlist: me (page 1, 5 of 150 wants)

@r847868 "Nine Inch Nails - The Downward Spiral" 1994 · Nothing Records 92346-2 · CD (Album) · notes: Sealed only
...

Next page: agent-discogs get wantlist --page 2
```

Each row is the ref, `artist - title`, year, label and catalog number, format, and `· notes: ...` when you wrote notes on the want. Use `--limit` (1-100, default: 5) and `--page` (from 1); the printed `Next page:` command repeats `--limit`.

- **It takes no ref and needs a user.** `get wantlist @r847868` fails with `invalid_argument`. A token or OAuth credentials name a user; a consumer key/secret or no credentials do not, and the command fails with `auth_required` before any request (see [Errors](errors.md#--owned---in-wantlist-get-owned-get-collection-get-lists-and-get-wantlist-need-a-user)). It reads only the authenticated user's own wantlist: there is no `--user` flag, and the CLI neither adds nor removes wants.
- **Cost.** One request for the username (shared with `--owned`, `get owned`, `get collection` and `get lists`, cached for an hour), then one per page; a rerun within the hour costs nothing.
- **`--json`:** `{"pagination":{...},"results":[{"ref","title","year","label","catno","format","notes","rating"}]}`, with `notes` and `rating` omitted when empty (`rating` `0` is unrated). `--json --full` prints the wants as the SDK normalizes them (`id`, `basic_information`, `notes`, `rating`, `resource_url`). See [JSON output](json-output.md#wantlist).

#### --for-sale

`--for-sale` keeps only the wants that have copies on the Marketplace right now, and adds the count and the lowest asking price to each row:

```bash
agent-discogs get wantlist --for-sale --max-requests 60
agent-discogs get wantlist --for-sale --max-price 20 --currency EUR --max-requests 60
```

```
Wantlist: me for sale max-price=20 currency=EUR (page 1, 2 of ≤10 wants)

@r847868 "Nine Inch Nails - The Downward Spiral" 1994 · Nothing Records 92346-2 · CD (Album) · notes: Sealed only · for sale: 3 from EUR 12.50
...
```

The row ends in `· for sale: 3 from EUR 12.50`, or `· for sale: 3` when Discogs gives no price. `--limit` is how many for-sale wants to collect (default: 5).

- **Filtering rules.** A want is kept when Discogs reports at least one copy for sale and has not blocked the release from sale (`blocked_from_sale`). With `--max-price AMOUNT`, its lowest price must also be at most `AMOUNT`: a want priced exactly `AMOUNT` is kept, and one with copies but no lowest price is dropped.
- **`--currency CODE`** is one of `USD`, `GBP`, `EUR`, `CAD`, `AUD`, `JPY`, `CHF`, `MXN`, `BRL`, `NZD`, `SEK`, `ZAR` (any case, sent uppercase; Tab completes it) and is sent with every stats request, so the prices shown and `--max-price` are in that currency. Without it Discogs prices in your account's currency, which it does not name. **`--max-price` needs `--currency`**, so a threshold is never read in a currency you did not choose.
- **Misplaced flags and bad values** fail with `invalid_argument` before any request: `--max-price`, `--currency` or `--max-requests` without `--for-sale`; an unknown `--currency` code; `--max-price` without `--currency`; a negative `--max-price`; `--max-requests` below 3 with `--for-sale` (the username, a wants page and one want's stats, which a cold cache or `--fresh` sends on every run); `--page` with `--for-sale` (it continues by cursor, see below); `--after` without `--for-sale`.
- **Cost: one request per want checked.** It is a client-side scan: wants are read 100 a request, and each one costs a request for its Marketplace stats (`GET /marketplace/stats/{id}`, cached for an hour). The scan runs until the page fills or the wantlist ends, with no default cap, so a 500-want list with few for sale costs about 500 requests, roughly nine minutes of the 60 a minute Discogs allows. **Pass `--max-requests`** (60 is one minute of budget).
- **`--max-requests N`** (3 or more) counts every send of the command (the username, the wants pages and the stats) and is checked before each want, so a scan can stop in the middle of a page. A wants page is asked for only while at least 2 sends remain after the sends so far (the page and one want's stats), so the cap never spends its last send on a page none of whose wants could be checked: when it runs out at a page boundary, the scan stops with the boundary cursor without reading the next page. Even under `--fresh` the minimum cap checks one want per run. It ends in `Continue scan: agent-discogs get wantlist --for-sale --max-requests N --after P:R`, and the header says `scan stopped at --max-requests N`. The cursor names the first want not yet checked, so the continuation starts there and never checks a want twice; cache hits are free, so a rerun within the hour re-reads checked wants at no cost.
- **A 429** on any stats request ends the scan with the rows matched so far (exit 0), a header saying `scan stopped: rate limited, retry in 30s`, and a `Continue scan:` from the want the 429 answered. A 429 on the username or the first wants page is the usual `rate_limited` error. A want whose stats were not read is never reported as not for sale. Any other error on a stats request fails the command.
- **`--cached`.** The username or a wants page the cache does not hold fails the command with `cache_miss`. A want whose stats are not cached becomes a row with its error, counts toward `--limit`, and the command exits 1, like the per-ref misses of `get` with several refs: `· ✗ Not cached: GET /marketplace/stats/5`, then `Rerun without --cached to check the wants marked ✗.` Under `--json` the row has `error.code == "cache_miss"`.
- **Freshness.** Stats are cached for an hour, so a copy listed or sold in the last hour may not show; add `--fresh` when current Marketplace numbers matter (it re-reads every want, at one request each).
- **`--json`:** each kept row adds `market`, and the pagination object is the filtered-scan envelope. See [JSON output](json-output.md#wantlist).

**What it cannot filter.** Condition, sleeve condition, seller location and shipping cost are not available: the Discogs API has no endpoint that lists the copies for sale of a release, only its Marketplace stats, which are the number of copies and one lowest price across all conditions and locations. To compare copies, open the release's Marketplace page on the Discogs website.

### inventory

`get inventory <username>` browses one seller's Marketplace listings (`GET /users/{username}/inventory`), five a page by default. It takes a Discogs username where the other nouns take a ref:

```bash
agent-discogs get inventory redscroll
agent-discogs get inventory redscroll --sort price --limit 20
agent-discogs get inventory redscroll --condition NM --max-price 30 --currency USD --max-requests 20
agent-discogs get inventory redscroll --in-wantlist --condition VG+ --max-requests 60
```

```
Inventory: redscroll condition≥NM (page 1, 5 of ≤300 listings)

@r847868 "Nine Inch Nails - The Downward Spiral" · CD, Album · NM/VG+ · USD 12.50 · ships from Germany · listed 2024-09-18
...

Next page: agent-discogs get inventory redscroll --condition NM --after 2:6
```

Each row is the release ref, `"Artist - Title"`, `· format`, the grades as `· MEDIA/SLEEVE` short codes (`NM/VG+`), the price with its ISO code (`· USD 12.50`), `· ships from Germany` and `· listed 2024-09-18` (the day as Discogs sends it, which is US Pacific time, not the seller's). A sleeve that is no grade (Generic, Not Graded, No Cover) prints as sent (`M/Not Graded`); with no sleeve sent only the media code shows, and a sleeve with no media grade reads `?/VG+`. Anything Discogs did not send is left out. A listing with no release id starts `listing 3254229818` where the ref would be. Without a filter the header reads `Inventory: redscroll (page 1, 5 of 300 listings)` and the footer is `Next page: ... --page 2`.

- **It takes exactly one username.** Two usernames fail with `invalid_argument` (`get inventory takes exactly one username (2 given).`), none is a usage error (exit 2), and a blank one is `invalid_argument`. A username starting with `-` goes after `--` (`get inventory -- -name`); the printed `Next page:` command puts it there itself.
- **No credentials needed.** Discogs shows anyone a seller's For Sale listings (an owner sees every status; the CLI sends no status filter), so `get inventory` alone works without credentials and sends no identity request. Only `--in-wantlist` and `--owned` need a user (see [Errors](errors.md#--owned---in-wantlist-get-owned-get-collection-get-lists-and-get-wantlist-need-a-user)).
- **Paging and sorting.** `--limit` (1-100, default: 5) and `--page` (from 1) page server-side; `--sort listed|price|item|artist|label|catno` and `--desc` are sent to Discogs (`sort`, `sort_order`), so they order the whole inventory, not just a page; Tab completes the keys. An unknown key, or `--desc` without `--sort`, fails with `invalid_argument` before any request.
- **`--owned`** marks each row with your copies of the listing's release (see [Marking rows with --owned](#marking-rows-with---owned)): its cost is the collection read, after the inventory page.
- **Cost.** One request per page; with a filter, one per 100 listings scanned. `--in-wantlist` adds one for the username and one per 100 wants, and `--owned` adds the collection read. A seller with 50,000 listings costs 500 requests to scan, over eight minutes of the 60 a minute budget (20 minutes at the 25 a minute of an unauthenticated caller), so **pass `--max-requests` with any filter** (60 is one minute of budget). Every response is cached for an hour.
- **`--max-requests N`** (1 or more) is accepted only with a filter or `--owned` (`get inventory` alone pages server-side and sends one request per page; otherwise `invalid_argument`). It counts every send of the command: the username, the wants pages, the inventory pages and the collection pages; cache hits are free.
- **`--cached`.** The username, a wants page or an inventory page the cache does not hold fails the command with `cache_miss`, as for any other scan.
- **`--json`:** `{"pagination":{...},"results":[{"listing","ref","title","format","condition","sleeve","price","currency","ships_from","listed","url"}]}`; `listing` is the listing id, `ref` the release's, `price` a number with its `currency` code, `listed` an RFC 3339 timestamp, `url` the listing's page, and empty fields are omitted. `--json --full` prints the SDK's `Listing` records. See [JSON output](json-output.md#inventory).

#### Filters

`--condition`, `--sleeve`, `--ships-from`, `--max-price` and `--in-wantlist` keep the listings that match every filter given. Discogs cannot filter a seller's inventory, so any of them makes it a client-side scan, like the other [client-side scans](#client-side-scans): the CLI reads 100-listing pages in the requested order and keeps the matches until `--limit` is filled or the inventory ends. The header shows `≤N` totals and the filters (`Inventory: redscroll in wantlist condition≥NM sleeve≥G ships-from=France max-price=45.5 currency=EUR`, each grade shown as the minimum it is), `--page` is rejected, the `Continue scan:` / `Next page:` command continues with `--after` and repeats the filters, `--sort`, `--desc` and `--in-wantlist`, `--max-requests N` caps the sends, and a 429 after the first page keeps the rows matched so far (`stop_reason: "rate_limited"`, `retry_after`; a 429 on the first page is the usual `rate_limited` error).

- **`--condition GRADE`** is the minimum media grade, on the Discogs scale from Mint to Poor: `M` > `NM` > `VG+` > `VG` > `G+` > `G` > `F` > `P`. Give the short code or the full name (`Very Good Plus (VG+)`), in any case; Tab completes the codes. Any other value fails with `invalid_argument` before any request (`--condition must be a Discogs grade: M, NM, VG+, VG, G+, G, F or P, or its full name such as 'Very Good Plus (VG+)' (any case; 'mint-' given).`).
- **`--sleeve GRADE`** is the minimum sleeve grade, same scale and spelling. A Generic, Not Graded or No Cover sleeve fails any `--sleeve`.
- **`--ships-from COUNTRY`** matches the listing's ships-from country exactly, in any case: `germany` matches `Germany`, `Germ` does not. A blank value fails with `invalid_argument`.
- **`--max-price AMOUNT --currency CODE`** keeps the listings priced in `CODE` at `AMOUNT` or less (exactly `AMOUNT` is kept; a listing with no price is dropped). `CODE` is one of `USD`, `GBP`, `EUR`, `CAD`, `AUD`, `JPY`, `CHF`, `MXN`, `BRL`, `NZD`, `SEK`, `ZAR` (any case; Tab completes it), and here it is matched client-side against each listing's price currency and **never sent to Discogs**, unlike `get wantlist --for-sale`, where it is sent as `curr_abbr` and prices the stats. A listing priced in another currency is dropped and counted: the header says `N listings in other currencies skipped` (`1 listing in another currency skipped`) and `--json` has `pagination.skipped_currency` (present whenever `--max-price` is given, 0 included). The count covers the listings that passed every other filter, in the part scanned by this run. Each flag needs the other on `get inventory` (`invalid_argument` otherwise), and an unknown code or a negative or non-finite amount fails too (a value that is not a number, such as `abc`, is click's usage error, exit 2). Discogs reports a seller's `price` in the seller's own currency (USD for one seller, EUR for another, with or without a token), so pick the `--currency` of the seller, and expect skipped counts for a seller who prices in another currency.

#### --in-wantlist

`--in-wantlist` keeps the listings whose release is on your wantlist. Before scanning it reads your whole wantlist once: one request for the username (shared with `--owned`, `get wantlist` and the other user nouns, cached for an hour), then one per 100 wants, through the same scan machinery, so `--max-requests` counts those sends and a 429 stops the read.

- **It needs a user.** A token or OAuth credentials name one; a consumer key/secret or no credentials do not, and the command fails with `auth_required` before any request (the message names `--in-wantlist`).
- **A listing is kept only against the complete wantlist.** When the read stops short (the cap or a 429, a 429 on the username included) no inventory page is scanned, and the command prints an empty capped page, exit 0. The header reads `Inventory: redscroll in wantlist (--max-requests 2 ran out before the scan started)` or `(rate limited before the scan started, retry in 30s)`, then `Wantlist: the read stopped at --max-requests 2 (100 of 150 wants read), so no listing was scanned: a listing is kept only against the whole wantlist.` (under `--fresh` it adds that `--fresh` reads the wantlist again on every run, so the continuation needs a higher `--max-requests`), and a `Continue scan:` command. Pasting it works: the wants already read are cached and free.
- **`--json`:** a top-level `wantlist` object, `{"complete":true,"wants_read":150,"total_wants":150}` after a full read, or `{"complete":false,"wants_read":100,"total_wants":150,"stop_reason":"max_requests"}` (plus `retry_after` on a 429) when it stopped, in which case `results` is `[]` and `pagination` is `{page, total_items: null, total_pages: null, filtered: true, capped: true, next_cursor: "1:0", stop_reason}`, the cursor being the one asked for. With `--owned` and a stopped read there is no collection read, since there are no rows.

**What it cannot do.** No command finds every seller of one release: the Discogs API has no such endpoint, so `get inventory` browses a seller you already know, and `price @r...` and `get wantlist --for-sale` give a count and a lowest price per release only. It cannot block, favorite or rate sellers, work with carts, offers or purchases, or edit your own listings. A listing's shipping cost is not shown: Discogs sends an empty shipping price on listings.

### lists and list

`get lists` lists your own Discogs lists, private ones included, five a page by default, each row ending in the command that opens the list:

```
Lists: me (page 1, 5 of 7 lists)

123 "Pressings" · private · changed 2016-06-02 → get list 123
...

Next page: agent-discogs get lists --page 2
```

Each row is the list id, its name, `public` or `private`, and `changed` with the UTC day it last changed. Discogs sends no item count with a list here. Use `--limit` (1-100, default: 5) and `--page` (from 1); the printed `Next page:` command repeats `--limit`.

- **It takes no ref and needs a user.** `get lists 123` fails with `invalid_argument`. Under the `consumer` or `none` auth modes the command fails with `auth_required` before any request (see [Errors](errors.md#--owned---in-wantlist-get-owned-get-collection-get-lists-and-get-wantlist-need-a-user)). It reads only the authenticated user's own lists: there is no `--user` flag.
- **Cost.** One request for the username (shared with `--owned`, `get owned`, `get collection` and `get wantlist`, cached for an hour), then one per page.
- **`--json`:** `{"pagination":{...},"results":[{"id","name","description","public","added","changed","url"}]}`; `public` is kept when `false`, `added` and `changed` are RFC 3339 timestamps, and `description` has its markup converted. `--json --full` prints the lists as the SDK normalizes them. See [JSON output](json-output.md#lists).

`get list <id> [<id>...]` prints a list: its name, `public` or `private`, its whole description (markup converted, never cut, its line breaks kept), then one row per item, the item's ref and title, with its comment when set:

```
List: 123 "Pressings" · private
Description: The best pressings of @a3857.

@r847868 "Nine Inch Nails - The Downward Spiral" · comment: check the matrix
@m3719 "Nine Inch Nails - The Downward Spiral"
@a3857 "Nine Inch Nails" · comment: the band
@l647 "Nothing Records"
```

- **A list id is a plain number.** Lists have no ref prefix (`@l` is a label), so `get list @r123` fails with `invalid_argument` before any request. Take the id from `get lists` or from the list's address on discogs.com.
- **Items.** An item's ref follows its type: `@r` release, `@m` master, `@a` artist, `@l` label. An item of any other type keeps its title and comment, with `id 123 [type]` in place of a ref, as a search row does. A comment prints on its row as written, Discogs markup included (only the description is converted): line breaks become spaces and control characters print as escapes; `--json` keeps it exactly as sent.
- **No user needed.** A public list reads without credentials. A private list reads only for its owner; someone else's fails with the error its status maps to (`not_found`, `auth_required` or `forbidden`).
- **Cost.** One request per list, whatever its length: Discogs sends every item in one response.
- **Several ids** run in sequence like the refs of any `get` noun (at most 10, one block each, exit 1 if any failed; see [Several refs](#several-refs)).
- **`--json`:** `{"id","name","description","public","url","items":[{"ref","title","comment"}]}`; an item of another type has `id` and `type` instead of `ref`. `--json --full` prints the list as the SDK normalizes it (`items` with `display_title`, `uri`, `resource_url`, `image_url`). See [JSON output](json-output.md#lists).

"What is on my list X?" is `get lists` to find its id, then `get list <id>`.

### releases and versions

Paginated nouns (`releases`, `versions`) support `--limit` (1-100, default: 5) and `--page` (from 1); `releases --role` and `versions --release-type official` filter client-side and continue via the printed `--after` cursor instead.

`releases` takes an artist ref (discography, with `--role` and `--sort year|title|format`) or a label ref (catalogue; the API offers no filters or sorting there, so `--year` on a label is a client-side scan that continues via the printed `--after` cursor).

`versions` accepts `--country`, `--format`, `--label`, `--year`, and `--sort released|title|format|label|catno|country`; add `--desc` to any `--sort`. All of them are sent to Discogs.

#### --release-type

`get versions @m... --release-type official|unofficial|all` filters on the format description "Unofficial Release", which Discogs gives bootlegs, counterfeits and pirate copies alike, the same rule `search --release-type` uses. A version row's format lists its descriptions (`LP, Album, Unofficial Release`), and the match is on the whole description, so a "Partially Unofficial" version counts as official.

- **`all` is the default**, unlike `search`, whose default is `official`: without the flag, `get versions` lists every version, unofficial pressings included, with server-side pages. The bootleg and counterfeit checks need to see them.
- **`unofficial`** is filtered by Discogs itself (`format=Unofficial Release`), so it shows an exact total and pages with `--page`. With `--format` it is a client-side scan instead, as on `search`, because the Unofficial Release filter is a `format` filter too.
- **`official`** drops the unofficial versions in a client-side scan, like `get releases --role`: it reads 100-row pages until `--limit` versions are kept or the list ends, shows the total as an upper bound (`≤N`), continues with the printed `--after` cursor (`--page` fails with `invalid_argument`), takes `--max-requests N`, and keeps the rows read so far when Discogs answers 429 after the first page (see [Client-side scans](#client-side-scans)). As text, the header's master title is read before the scan, so `--max-requests` counts it.

```bash
agent-discogs get versions @m3719 --release-type official --limit 5 --json
agent-discogs get versions @m3719 --release-type unofficial --country UK
```

`releases` and `versions` also take `--owned`, which marks each release row with your own copies: see [Marking rows with --owned](#marking-rows-with---owned).

### artist and label

Every navigable name in `artist` and `label` output carries a ref: members and former members, parent label, sub-labels. Their `URLs:` line lists each linked site by domain, omitting empty entries. `Former:` lists only members Discogs marks inactive; a member with no recorded status stays under `Members:`.

### Flags per noun

Each flag applies only to the nouns below. Any other flag you pass (even `--limit 5`, the default value) fails with `invalid_argument` before a request is made, and so do `--after` and `--max-requests` with more than one ref, since a cursor continues one listing and a cap is spent on one.

| Noun | Flags besides `--json` / `--full` |
|---|---|
| `release` | `-v, --verbose`, `-c, --compact` |
| `releases @a...` | `--limit`, `--page`, `--role`, `--sort year\|title\|format`, `--desc`, `--owned`, `--after` and `--max-requests` (with `--role` or `--owned`) |
| `releases @l...` | `--limit`, `--page`, `--year`, `--owned`, `--after` and `--max-requests` (with `--year` or `--owned`) |
| `versions` | `--limit`, `--page`, `--country`, `--format`, `--label`, `--year`, `--sort released\|title\|format\|label\|catno\|country`, `--desc`, `--release-type official\|unofficial\|all`, `--owned`, `--after` (with a `--release-type` scan) and `--max-requests` (with a `--release-type` scan or `--owned`) |
| `collection` | no ref; `--limit`, `--page`, `--sort label\|artist\|title\|catno\|format\|rating\|added\|year`, `--desc`, `--format`, `--genre`, `--style`, `--year`, `--label`, `--after` and `--max-requests` (with a filter); or `--value` alone |
| `lists` | no ref; `--limit`, `--page` |
| `wantlist` | no ref; `--limit`, `--page`, `--for-sale`, `--max-price` and `--currency` (both with `--for-sale`), `--after` and `--max-requests` (with `--for-sale`) |
| `inventory` | one username, not a ref; `--limit`, `--page`, `--sort listed\|price\|item\|artist\|label\|catno`, `--desc`, `--owned`, `--condition`, `--sleeve`, `--ships-from`, `--in-wantlist`, `--max-price` and `--currency` (each needs the other), `--after` (with a filter) and `--max-requests` (with a filter or `--owned`) |
| `artist`, `credits`, `identifiers`, `label`, `list`, `master`, `owned`, `price`, `tracklist` | none |

## tracks / price

Shortcuts for `get tracklist` and `get price`. Both support `--json`.

```bash
agent-discogs tracks @r352665
agent-discogs price @r352665
```

`price` prints each amount with the ISO currency code Discogs returns, never a symbol: `Mint (M): EUR 20.00`, `Lowest: GBP 9.99`. Under `--json` every price carries its `currency`: `"suggestions":{"Mint (M)":{"value":20.0,"currency":"EUR"}}`, `"market":{"for_sale":9,"lowest":9.99,"currency":"GBP"}`.

The `Market: 9 for sale from 9.36` line of `get release` and `get master` is a bare amount: Discogs prices it in the token account's buyer currency (USD when unauthenticated) without naming it, so there it has no code and no `currency` key. Use `price` when the currency matters.

## Several refs

`get`, `tracks`, and `price` accept up to 10 refs and run them in sequence, so comparing pressings is one command: `agent-discogs get release @r352665 @r847868 -c`. Text blocks are separated by a blank line; a ref that fails reports inline and the rest still run (exit code 1 if any failed). With `--json` and several refs the output is a list, with `{"ref": ..., "error": {...}}` items for failures. `get list` takes list ids the same way; `get collection`, `get lists` and `get wantlist` take no ref, and `get inventory` takes exactly one username.

## status

Show version, authentication mode, and cache location. The `Auth:` line names the credentials requests will carry: `token (authenticated)`, `oauth (authenticated)`, `consumer (authenticated app, not a user: no price suggestions)`, or `none (unauthenticated, 25 req/min)`. It makes no API call and never opens or creates the cache.

```bash
agent-discogs status
```

## cache

Manage the HTTP response cache:

```bash
agent-discogs cache clear          # Cache cleared.
agent-discogs cache clear --json   # {"cleared":true}
```

`cache clear` reports success only when the purge succeeded. If the cache cannot be opened, or the purge fails (the database is locked by another process, read-only, or damaged), it exits 1 with a `cache_error` (the JSON envelope on stdout under `--json`) and leaves the database exactly as it was.

The hint says whether to retry, fix permissions, or delete the file by hand; the CLI never deletes it.

## skills

Print the bundled agent guide. The content ships in the package, so it always matches the installed version. It is split into four skills: `core`, which every task needs, and three that a task loads when it needs them:

```bash
agent-discogs skills                     # list bundled skills with the installed version (same as skills list)
agent-discogs skills get core            # setup, refs, search and navigation, output, budget, troubleshooting
agent-discogs skills get core --full     # plus references: command reference, errors, JSON output, search patterns, Discogs data model
agent-discogs skills get collection      # your collection, lists and ownership marks (--owned, get owned, get collection, get lists, get list)
agent-discogs skills get marketplace     # price, get wantlist and --for-sale, get inventory
agent-discogs skills get pressings       # telling pressings apart: versions, identifiers, unofficial releases
agent-discogs skills path [name]         # directory holding the bundled skill files
```

`--full` appends only that skill's own references. An unknown name exits 1 and lists the available ones.

## Shell completion

`agent-discogs completion bash|zsh|fish` prints click's completion script for that shell, for a person typing in an interactive shell (agents get the same lists from `--help` and the bundled skills). It makes no API call and never opens the cache. Any other shell is a usage error (exit 2) naming the supported ones. Set it up once:

```bash
eval "$(agent-discogs completion bash)"   # in ~/.bashrc (bash 4.4 or later)
eval "$(agent-discogs completion zsh)"    # in ~/.zshrc, after compinit
agent-discogs completion fish > ~/.config/fish/completions/agent-discogs.fish
```

The script asks agent-discogs for the candidates on each Tab, so it follows the installed version without being regenerated.

Tab completes every closed set of values:

- commands, each under one name: the aliases (`find`, `query`, `fetch`, `show`) are not offered, but typed in full they still complete what follows;
- option names (`--<Tab>`);
- `get` nouns, `ids` included, the `--currency` codes of `get wantlist` and `get inventory`, and the grades of `--condition` and `--sleeve` (`M NM VG+ VG G+ G F P`);
- the `search` type word (`release`, `master`, `artist`, `label`), only as the first positional after `search` (options or `--` may come before it); the query after it gets no suggestions;
- `search --release-type`, `search --sort`, the global `--color`, `cache clear`, the `skills` subcommands and skill names, and the `completion` shells;
- `get --sort`: the keys of the noun typed before it (`get versions --sort <Tab>` offers `released title format label catno country`, `get collection --sort <Tab>` offers `label artist title catno format rating added year`, `get inventory --sort <Tab>` offers `listed price item artist label catno`), or every key of `releases`, `versions`, `collection` and `inventory` before a noun, and none after a noun that takes no `--sort`. A key the noun does not take is still rejected when the command runs.

Refs, queries, Discogs names and free-text filters (`--artist`, `--format`, `--role`, ...) are not completed: refs are stateless, so there is no list to offer.
