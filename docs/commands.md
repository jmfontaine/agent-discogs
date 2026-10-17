# Commands

Each command, its own flags and its text output. The global flags (`--cached`, `--fresh`, `--debug`, `--color`), the output modes (`--json`, `--full`), the `--max-requests` cap and errors are described on the pages that cover them:

- `--json` and `--full`: [JSON output](json-output.md) and its [--full](json-output.md#--full) section;
- `--cached` and `--fresh`: [--cached and --fresh](caching-and-budget.md#--cached-and---fresh);
- `--max-requests`: [Capping requests with --max-requests](caching-and-budget.md#capping-requests-with---max-requests), next to what each command costs;
- `--debug`: [The --debug panel](debugging.md#the---debug-panel);
- `--color`: [Color](debugging.md#color);
- error messages: [Errors](errors.md);
- questions no command can answer, because the Discogs API has no endpoint for them or the CLI only reads: [What the Discogs API cannot answer](api-limits.md).

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

A release or master row shows its title, year, country, first label and catalog number, formats, Discogs' community counts and, for a release, its master: `@r847868 "Nine Inch Nails - The Downward Spiral" 1994 US · Nothing Records 92346-2 · CD, Album · have 7,544 · want 1,113 → @m3719`. `have` is how many Discogs users own the release and `want` how many want it, both from the search page itself, so they cost no request (`--json`: `have`, `want`). A count Discogs did not send is left out; `0` is shown.

Discogs occasionally returns a result whose type is not one of those four, or has none. Such a row has no ref, because nothing says what its ID refers to: it reads `id 9001 [playlist] "..."` in text (`[no type]` when Discogs sends none) and carries `id` instead of `ref` under `--json`. The rest of the page prints as usual.

To continue, paste the `Next page:` command printed under the results: the default `--release-type official` filter is applied client-side, so continuation is a cursor (`--after`), not a page number, and the total is an upper bound (`≤N`).

`--release-type unofficial` is filtered by Discogs itself (`format=Unofficial Release`), so it shows an exact total and pages with `--page`; combined with `--format` it falls back to the client-side scan, because Discogs cannot reliably combine two format filters. `--page` (from 1) works with those server-side pages, `--release-type all`, and artist/label searches.

Discogs' "Unofficial Release" covers bootlegs, counterfeits and pirate copies alike, and no field tells them apart, so `--release-type unofficial` returns all three. To keep bootlegs only, check each row's master: the bundled pressings guide (`agent-discogs skills get pressings --full`, section "Unofficial Releases: Bootlegs, Counterfeits, Pirates") describes how.

A search needs a query or at least one non-blank filter; an out-of-range `--limit`/`--page` or an empty search fails with `invalid_argument` before any request.

A query starting with `-` goes after `--` (`agent-discogs search --release-type all -- -ambient`); the printed `Next page:` command always puts the type and query there, so it pastes back unchanged.

### Filters

Filters: `--anv`, `--artist`, `--barcode`, `--catno`, `--country`, `--credit`, `--format`, `--genre`, `--label`, `--release-type {official,unofficial,all}` (default: official), `--style`, `--title`, `--track`, `--year`. Use `--json` for JSON output (see [JSON output](json-output.md)).

Four filters search fields the query alone mixes together. Each works with or without a query and with every other filter, and costs what the same search costs without it:

| Flag | Matches |
|---|---|
| `--credit` | a person credited on a release or master (an engineer, producer, mixer, ...) |
| `--track` | track titles |
| `--title` | release titles; on `search artist` and `search label`, the artist's or label's own name. Sent to Discogs as `release_title`; the header and the `Next page:` command keep `title` |
| `--anv` | artist name variations (the name as credited on a release) |

```bash
agent-discogs search release --artist "Horace Silver" --credit "Rudy Van Gelder" --format Vinyl
agent-discogs search release --track "So What" --artist "Miles Davis"
agent-discogs search release --title "Kind of Blue"
agent-discogs search artist --anv "Trane"
```

Discogs answers a filter it cannot match on a search type with an empty page, which reads as "nothing found", so the CLI refuses these before any request with `invalid_argument`: `--credit` and `--track` on `search artist` or `search label`, and `--anv` on `search release`, `search master` or `search label`. An untyped search takes all four.

`--credit` is a broad match: `"Van Gelder"` finds about as many releases as `"Rudy Van Gelder"`, quoting the name does not make it an exact-phrase match, and the first rows of `--credit "Rudy Van Gelder"` alone were a Debussy and a Bach album. Narrow it with `--artist`, `--genre`, `--format` or `--year`. Adding a role word (`"Rudy Van Gelder Engineer"`) returns nothing. `get releases @a... --role` cannot replace it: for one engineer, 300 discography rows held only the roles Appearance, Mixed by, Remix and Producer, so `--role Engineer` matched nothing. To see what a person did on a given release, use `get credits @r...`.

`--barcode` is matched against a release's identifier text, not only as a UPC/EAN: it also finds pressings by their matrix/runout etching, the one search that does. Other identifier types (label codes, for instance) have not been checked, so do not rely on them. For example, this finds the UK LP pressings whose runout contains `A3 TOWNHOUSE`:

```bash
agent-discogs search release --artist "Human League" --barcode "A3 TOWNHOUSE" --country UK --format LP --release-type all
```

Type a barcode as digits without spaces (`--barcode 602527826486`): `"6 02527 82648 6"` also matched an unrelated release. The value is matched as text, not as a number, so the barcode without its check digit (`60252782648`) found the same releases. Discogs writes some etchings in more than one way, so try each spelling (`TOWNHOUSE` and `TOWN HOUSE`): one can miss releases the other finds.

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

- It costs one request per release the search matches in the window, before `--release-type` filtering, plus one per 100 search results and one for the release that ends the scan; the lookups already made are free while their responses are cached (one hour from storage), so a repeat of a run that finished costs nothing and one that stopped at `--max-requests` or on a 429 continues past them.
- There is no cap by default: `--max-requests N` stops it after N sends, and the result then ends with `Stopped at --max-requests N: the result may be incomplete` (JSON: `complete: false`; `total` counts the rows found). Pass `--max-requests` if your tool calls have a time limit.
- `--json` is `{"complete","total","results"}`, with no `pagination` and nothing to continue: see [JSON output](json-output.md#releases-added-since-a-date).
- The CLI never waits out the rate limit: a 429 partway through fails the command with `rate_limited`, and a rerun after `retry_after` resends none of the lookups already made.
- The whole window comes back unless you pass `--limit`, which cuts the rows, not the requests.
- `--release-type official` (the default) and `unofficial` with `--format` filter on the looked-up formats at no extra request; `unofficial` alone is filtered by Discogs (`format=Unofficial Release`), which saves the lookups of official releases.
- It needs an explicit `search release ...` and rejects `--sort`, `--desc`, `--page` and `--after` (`invalid_argument`, before any request).
- Under `--cached`, any lookup the cache does not hold fails the command with `cache_miss`.

### Marking rows with --owned

`--owned` marks every release row with the copies in your own Discogs collection, on `search`, `get versions`, `get releases` (artist and label) and `get inventory`. A row ends in `· owned` (one copy), `· owned ×2` (two or more), `· not owned`, or `· owned ?` when it is undecided (below). In `--json`, each row gets `owned`, the count, and on `search` and `get inventory` the document gets a `collection` object (see [JSON output](json-output.md#owned-rows)).

It is not the `have 1,234 · want 56` on a row, which are Discogs' community counts: how many Discogs users own that release and how many want it. `owned` is the authenticated user's own copies.

```bash
agent-discogs search release --artist "Nine Inch Nails" --year 1994 --release-type unofficial --limit 100 --owned
agent-discogs get versions @m3719 --country US --owned
agent-discogs get releases @a3857 --role Main --owned --max-requests 6
```

- **Release rows only.** A collection holds releases, and a master is not something you own, so master, artist and label rows (an untyped search, or an artist's discography, lists some) get no mark; to ask whether you own any pressing of a master, use [`get owned @m...`](#owned) (each collection record carries its release's master id, which [`get collection --json`](#collection) rows show as `master`). `search artist|label|master ... --owned` and `--owned` with `--added-since` fail with `invalid_argument` before any request, as does `--owned` on any `get` noun but `releases`, `versions` and `inventory`. A page with no release row reads nothing. On `get inventory` a row is looked up by its listing's release id (never the listing's own id), and a listing with no release id reads `owned ?` (JSON `owned: null`).
- **It needs a user.** A token or OAuth credentials name one; a consumer key/secret or no credentials do not, and the command fails with `auth_required` before any request (see [Errors](errors.md#--owned---in-wantlist-get-owned-get-collection-get-lists-and-get-wantlist-need-a-user)). It reads only the authenticated user's own collection: there is no `--user` flag.
- **Where the count comes from.** On `get versions` and `get releases`, Discogs sends the authenticated user's own count on every row (`stats.user.in_collection` in `--json --full`), so `--owned` reads it from the row and sends nothing more: no username, no collection page, no `collection` object, whatever the number of refs. A release row that carries no count reads `· owned ?` (JSON `owned: null`), with no note and no request to decide it. On `search` and `get inventory`, whose rows carry no reliable count, `--owned` reads your whole collection (below).
- **Cost.** Nothing on top of the listing on `get versions` and `get releases`: `get versions @m3719 --owned --json` is 1 request on a cold cache, 2 in text (the page and the master's title for the header). On `search` and `get inventory`, after the listing page, one request for the username, then your whole collection (the "All" folder) at 100 records per request, read once per command. Every response is cached for an hour, so a rerun costs nothing once the read completed and its responses are still cached; after a read cut short (`--max-requests` or a 429) it sends the pages not read. See [What commands cost](caching-and-budget.md#what-commands-cost).
- **`--max-requests N`** on `search` and `get inventory` counts every send of the command (the listing page, the username, the collection pages and, with `--wanted` on `search`, the wants pages; cache hits are free), but it only stops a scan, the collection read and the wantlist read (see [Marking rows with --wanted](#marking-rows-with---wanted)). A server-side listing page is always sent; the username and the collection pages are then asked only while the cap allows. So a cold server-side `search release ... --release-type all --owned --max-requests 3` is the listing, the username and one collection page. It is accepted on `search` with `--owned` or `--wanted` (otherwise rejected on a server-side search) and on `get inventory` with `--owned` (otherwise rejected unless a filter is given), with one ref only. On `get versions` and `get releases`, `--owned` and `--wanted` read no collection or wantlist, so they leave nothing to cap: `--max-requests` is accepted only where something else is left to cap, a client-side scan (`get versions --release-type official`, or `unofficial` with `--format`; `get releases --role`; `get releases @l... --year`) or, on `get versions`, the [`--market`](#--market) lookups (it caps the scan and the lookups together), and is otherwise `invalid_argument` before any request.
- **A collection read that stops early** (`search` and `get inventory`; the cap, or a 429) never claims a release is not owned: a row found in the part read shows the count found so far (it may be short), and any other row reads `· owned ?` (JSON `owned: null`). A note before the footer says why: `Owned: the collection read stopped at --max-requests 2 (100 of 250 records read): "owned ?" rows are not in the part read, and a count may be short.` or `... the collection read was rate limited, retry in 30s (100 of 250 records read): ...`. Rerun with a higher `--max-requests`, or after the wait. A 429 on a later collection page keeps the records read. A 429 on the username or the first collection page, or a listing scan that already stopped on one (`pagination.stop_reason: "rate_limited"`; the collection is then not read at all, with no request), leaves every release row `owned ?`; the command still succeeds, and JSON `collection` is `{"complete":false,"records_read":0,"total_records":null,"stop_reason":"rate_limited","retry_after":N}` (the listing's `retry_after` in the second case).
- **Paging.** The printed `Next page:` / `Continue scan:` command repeats `--owned`, so the next page is marked too; within the hour the collection comes from the cache.
- **Freshness.** A mark is as old as what it was read from, cached for an hour: the listing page on `get versions` and `get releases`, the collection on `search` and `get inventory`. A record added to your collection within the last hour may still show as not owned; `--fresh` re-reads the listing, or the collection. Under `--cached`, `get versions` and `get releases` need nothing for `--owned` beyond the listing's own cached requests (the page, and in text the header) and send nothing; on `search` and `get inventory` a collection page the cache does not hold fails the command with `cache_miss`.

### Marking rows with --wanted

`--wanted` marks every release row with whether it is on your own Discogs wantlist, on `search`, `get versions` and `get releases` (artist and label). A row ends in `· wanted`, `· not wanted`, or `· wanted ?` when it is undecided (below), after the `--owned` mark when both flags are given: `@r847868 1994 US · Nothing Records 92346-2 · CD, Album · have 7,544 · owned · wanted`. In `--json`, each release row gets `wanted`: `true`, `false` or `null`, and on `search` the document gets a `wantlist` object (see [JSON output](json-output.md#wanted-rows)).

```bash
agent-discogs get versions @m3719 --country US --owned --wanted
agent-discogs search release --artist "Nine Inch Nails" --year 1994 --release-type all --wanted
```

- **Release rows only.** A wantlist holds releases, so master, artist and label rows get no mark. `search artist|label|master ... --wanted` and `--wanted` with `--added-since` fail with `invalid_argument` before any request, as does `--wanted` on any `get` noun but `releases` and `versions`; on `get inventory` the error points at `--in-wantlist`, which keeps only the listings whose release you want. A page with no release row reads nothing.
- **It needs a user**, like `--owned` (see [Errors](errors.md#--owned---in-wantlist-get-owned-get-collection-get-lists-and-get-wantlist-need-a-user)): `--wanted reads your own Discogs account, but no credentials are set.`
- **Where the answer comes from.** On `get versions` and `get releases`, every row carries the authenticated user's own want count (`stats.user.in_wantlist` in `--json --full`): 1 or more is wanted, 0 is not, and a row without it reads `· wanted ?` (JSON `null`). Nothing more is sent: no username, no wantlist page, no `wantlist` object. On `search`, whose rows carry no reliable flag, `--wanted` reads your whole wantlist once, after the listing page: one request for the username (shared with `--owned`), then one per 100 wants. Every response is cached for an hour, so a rerun costs nothing once the read completed and its responses are still cached; after a read cut short (`--max-requests` or a 429) it sends the pages not read.
- **`--max-requests N`** is accepted with `--wanted` on `search` and caps the wantlist read the way it caps the `--owned` collection read: it counts every send of the command, and with nothing left of it after the listing (and the collection read), not even the username is asked for. On `get versions` and `get releases`, `--wanted` reads nothing, so, as with `--owned`, `--max-requests` there needs a client-side scan (`get versions --release-type official`, or `unofficial` with `--format`; `get releases --role`; `get releases @l... --year`) or the `--market` lookups of `get versions` (capped together with the scan), and is otherwise `invalid_argument` before any request.
- **A wantlist read that stops early** never claims a release is not wanted: a release found in the part read is `wanted`, any other row reads `· wanted ?` (JSON `null`), and a note before the footer says why: `Wantlist: the read stopped at --max-requests 3 (100 of 250 wants read): "wanted ?" rows are not in the part read.` (`was rate limited, retry in 30s` for a 429). A 429 on a later wants page keeps the wants read. A 429 on the username or the first wants page leaves every release row `wanted ?`, and after a listing scan or an `--owned` collection read that already stopped on a 429 the wantlist is not read at all (no request; JSON `wantlist.stop_reason: "rate_limited"`, `wants_read: 0`). The command still succeeds.
- **Paging and freshness.** The printed `Next page:` / `Continue scan:` command repeats `--wanted`. A mark is as old as what it was read from, cached for an hour: the listing page on `get versions` and `get releases`, the wantlist on `search`; `--fresh` re-reads it. Under `--cached`, `search ... --wanted` fails with `cache_miss` when the username or a wants page is not cached, and nothing is sent; `get versions` and `get releases` need nothing beyond the listing's own cached requests.

## get

Get entity details by noun and ref. Aliases: `fetch`, `show`.

```bash
agent-discogs get release @r352665
agent-discogs get release @r352665 --master-year
agent-discogs get artist @a3857
agent-discogs get master @m3719
agent-discogs get label @l647
agent-discogs get tracklist @r352665
agent-discogs get releases @a3857 --limit 20
agent-discogs get versions @m3719 --country US --format Vinyl
agent-discogs get price @r352665
agent-discogs get stats @r847868 @r352665 --currency EUR
agent-discogs get owned @r352665 @r847868
agent-discogs get owned @m3719
agent-discogs get versions @m3719 --country US --owned
agent-discogs get collection --sort added --desc
agent-discogs get collection --style Techno --format 12\" --max-requests 20
agent-discogs get collection --value
agent-discogs get collection --count-by genre --top 10
agent-discogs get collection --duplicates master --format Vinyl
agent-discogs get collection --random 3 --genre Jazz
agent-discogs get wantlist
agent-discogs get wantlist --for-sale --max-price 20 --currency EUR --max-requests 60
agent-discogs get inventory redscroll --sort price
agent-discogs get inventory redscroll --condition NM --ships-from Germany --max-requests 20
agent-discogs get inventory redscroll --in-wantlist --condition VG+ --max-requests 60
agent-discogs get lists
agent-discogs get list 123
```

Nouns: `artist`, `collection` (takes no ref), `credits`, `identifiers` (alias `ids`), `inventory` (takes a seller's username), `label`, `list` (takes a list id), `lists` (takes no ref), `master`, `owned`, `price`, `release`, `releases`, `stats`, `tracklist`, `versions`, `wantlist` (takes no ref). Use `--json` for JSON output.

### release

`release` output carries inline refs for its artists and label (`[@a...]`, `[@l...]`), plus country, release date, and the day the release was added to Discogs (`Country: US · Released: 1994-03-08 · Added: 2007-02-23`; the date is the one in the timestamp's own offset, and the field is omitted when Discogs has none).

Under `--json` that date is `added`, a full RFC 3339 timestamp such as `2007-02-23T01:13:39-08:00`. It answers "is this a new entry or an old one?" at no extra request.

Unknown date parts are left out, in text and `--json` alike: Discogs' year `0` is omitted, and a release date drops a zero month or day (`1994-05-00` shows as `1994-05`, `1994-00-00` as `1994`, and the text view prints no `Released:` when that equals the year).

Its format lists one entry per physical format, separated by `;`: the quantity when above one, the name, then descriptions and free text in parentheses (`Format: 2×Vinyl (LP, Album, 180g, Gatefold); CD (Album)`; `--json` carries the same string).

`Genres:` and `Styles:` are `;`-separated too (`Genres: Rock; Folk, World, & Country`), on `get release` and `get master` alike, because the genre `Folk, World, & Country` has commas; `--json` carries them as arrays (`"genres":["Rock","Folk, World, & Country"]`).

`-v, --verbose` on `release` appends notes, credits, and identifiers in one call; `-c, --compact` replaces the tracklist with a one-line `Tracks: 14 (65:01)` summary; the total is omitted when any track lacks a duration or has one that is not a number of minutes and seconds (use `tracks` when the tracklist is what you want).

`--master-year` ends the `Master:` line with the master's year, the year of the work rather than of this pressing: `get release @r352665 --master-year` prints `Master: @m3719 (1994)` for a 2004 release (`--json`: `master_year` next to `master`; `--json --full`: a top-level `master_year` beside the record's keys). It costs one more request per release that has a master, sent after the release is read: `GET /masters/{id}`, the request `get master` sends, so the two share the cache and releases of one master share one request (except under `--fresh`). A release with no master has no `Master:` line and sends nothing more; a master year Discogs does not know (`0`) prints no year. Two failures of that request leave the release whole, with exit code 0: a `--cached` miss and a deleted master (404) print the reason in place of the year (`Master: @m3719 · ✗ Not cached: GET /masters/3719`), and `--json` carries `master_year_error` (`code` `cache_miss` or `not_found`, and `message`) instead of `master_year`. Any other failure of it (a 429, a 5xx, a connection error, a response that does not parse) fails the ref as usual: its `✗` block, exit 1, the release not printed; the release is cached by then (not under `--fresh`), so a rerun costs only the master request.

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
| anything else in brackets, uppercase tags included (`[A3857]`, `[Booklet]`), and a ref tag whose id has more than 18 digits | unchanged |

An opening tag Discogs never closes ends with the field, and a closing tag with nothing to close is dropped. The cut (profiles only; notes and list descriptions are never cut) counts the converted text and never splits a ref. `--json --full` keeps these fields exactly as Discogs sent them, markup included.

### credits and identifiers

`credits` lists who did what on a release, grouped by role, each person with an artist ref. It includes credits Discogs files on individual tracks and sub-tracks: each person appears once per role with the tracks the credit covers, and no track list means the whole release.

`identifiers` lists barcodes, matrix/runout etchings, and other codes: the data that tells two pressings with the same catalog number apart.

### stats

`get stats @r... [@r...]` prints, for 1 to 10 releases (typed refs or raw ids), the numbers that compare pressings: how many Discogs users have each release and want it, its community rating, how many copies are for sale and the lowest asking price, with the currency named. One line per release, blocks separated by a blank line:

```
$ agent-discogs get stats @r847868 @r352665
@r847868 "The Downward Spiral" by Nine Inch Nails 1994 · have 7,618 · want 1,355 · rating 4.43/5 (602 votes) · for sale: 45 from EUR 0.80

@r352665 "The Downward Spiral" by Nine Inch Nails 2004 · have 1,978 · want 734 · rating 4.76/5 (251 votes) · for sale: 9 from EUR 72.00
```

- **Where the numbers come from.** `have`, `want` and the rating come from the release record (`GET /releases/{id}`, the request `get release` and `get price` send); the count for sale and the lowest price come from the release's Marketplace stats (`GET /marketplace/stats/{id}`, the request `get price` and `get wantlist --for-sale` send), which name the currency the price is in. A part Discogs did not send is left out: no rating when nobody voted, no `have`/`want` without community data.
- **The market line** is `· for sale: N from CUR 12.50`, the amount with the ISO code Discogs sent, never a symbol, as on `get wantlist --for-sale`. Discogs sends no count and no price (`null`) when nothing is for sale, and also when the release is blocked from sale: both print `· for sale: 0`. A count with no price prints `· for sale: 3`, with no `from`.
- **`--currency CODE`** (`USD`, `GBP`, `EUR`, `CAD`, `AUD`, `JPY`, `CHF`, `MXN`, `BRL`, `NZD`, `SEK`, `ZAR`, any case; Tab completes it) is sent as `curr_abbr` on the stats request only, so the price is in that currency. Without it Discogs prices in the account's buyer currency, or USD without credentials, and the line names it either way. An unknown code fails with `invalid_argument` before any request. The release record cannot be asked for another currency: Discogs ignores `curr_abbr` there.
- **Cost: 2 requests per ref** on a cold cache, the release then its stats, both cached for an hour: 20 for 10 refs. The release is read first, so a release that does not exist costs 1 request and no stats request. No username, profile or seller settings are needed, and no credentials beyond what `get release` needs. See [Caching and request budget](caching-and-budget.md#what-commands-cost).
- **Errors are per ref**, as for every several-ref `get`: a ref that is not a release (`@m3719 is a master, not a release. Use a release ref or ID with 'get stats'.`), a 404, a 429 or a `--cached` miss on either request makes that ref its `✗` block (JSON: a `{"ref","error"}` item), never a line with half the numbers; the other refs still run, and the command exits 1. `get stats` takes `--currency`, `--json` and `--full`; any other flag fails with `invalid_argument` before any request.
- **`--json`**: `{"ref","title","artists","year","community":{"have","want","rating","votes"},"market":{"for_sale","lowest","currency"}}`, with `"blocked": true` in `market` for a release blocked from sale; `--json --full`: `{"release": <the release record>, "marketplace_stats": <the stats record>}`. See [JSON output](json-output.md#stats).

### owned

`get owned @r... [@r...]` tells whether the authenticated user owns each release, and in which folders:

```
@r352665 owned ×2: Vinyl, Uncategorized
@r847868 not owned
```

It asks about one release at a time (`GET /users/{username}/collection/releases/{id}`), so it is the cheap way to check a few refs; for a page of rows, use `--owned` instead. Several refs work as with the other nouns: blocks separated by a blank line, a failing ref as its `✗` block, exit 1 if any failed. A raw id (`get owned 847868`) is a release; a ref of another type than a release or a master fails with `invalid_argument`.

- **Cost:** one request for the username (once per command), one per ref, and one for the folder list when any ref is owned. A copy in a folder Discogs does not name adds no folder name: with no names at all the text is `@r352665 owned` (no colon) and JSON has no `folders`.
- **`--json`:** `{"ref":"@r352665","owned":2,"folders":["Vinyl","Uncategorized"]}`, or `{"ref":"@r847868","owned":0}` when not owned; several refs give a list, with `{"ref","error"}` items for failures. `--json --full` is `{"instances":[...]}`, the collection records as the SDK normalizes them (each names its folder by id), and sends no folder-list request.
- **Needs a token or OAuth**, like `--owned` (`auth_required` before any request otherwise). Only `--json` and `--full` apply.

#### A master: any pressing

`get owned @m...` tells whether you own any release of a master, and which:

```
@m3719 owned ×3 in 2 releases
  @r847868 owned: Vinyl
  @r352665 owned ×2: Vinyl, Uncategorized
```

The header counts the copies (from two, as for a release) and the releases (`owned in 1 release` for one copy); each indented line is what `get owned @r...` prints for that release. Releases come in the order of their first copy in the collection, and each one's folder names in the order its copies list them. Nothing owned reads `@m3719 not owned`.

- **One read of the whole collection.** Discogs has no endpoint keyed by master, so the first master ref reads your whole collection (the "All" folder, 100 records a request, the pages `--owned` and a filtered `get collection` read) and every master ref of the command is answered from it, matched on each record's `basic_information.master_id` (a record with no master matches none). The read happens once per command, `--fresh` included. Release refs keep their own request.
- **Cost:** one request for the username, then one per 100 records in your collection (1 when it is empty), whatever the number of master refs or the master's versions, plus one for the folder list when any copy is found (none under `--json --full`). Every response is cached for an hour, so a repeat, another master or a later `--owned` read of the same pages costs nothing once the read completed and its pages are still cached; a read cut short by a 429 (`owned ?`, `owned: null`) sends the pages not read on the next run.
- **No master check.** No master request is sent: a master that does not exist reads `not owned`, as one you own nothing of.
- **A 429 stops the read.** The answer is what the part read holds, and a note says how much was read: `Owned: the collection read was rate limited, retry in 30s (100 of 250 records read): "owned ?" rows are not in the part read, and a count may be short.` A master with no copy in that part reads `@m3719 owned ?` (JSON `owned: null`), never `not owned`. A 429 on the folder list keeps the answer too, its folders named by id (`folder 5`), and the list is not asked for again in that command: every later ref names its folders by id. The exit code stays 0. `get owned` takes no `--max-requests`.
- **`--cached`:** the username or a collection page the cache does not hold fails each master ref with `cache_miss`, with nothing sent.
- **`--json`:** `{"ref":"@m3719","owned":3,"releases":[{"ref":"@r847868","owned":1,"folders":["Vinyl"]},{"ref":"@r352665","owned":2,"folders":["Vinyl","Uncategorized"]}],"collection":{"complete":true,"records_read":250,"total_records":250}}`, or `{"ref":"@m3719","owned":0,"collection":{...}}` when not owned (no `releases`). Every master document carries the read's `collection` object, as `--owned` documents do. `--json --full` is `{"instances":[...],"collection":{...}}`: the matching collection records as the SDK normalizes them, in collection order, folders by id, with no folder-list request. See [JSON output](json-output.md#owned-rows).

### collection

`get collection` lists your own Discogs collection, one row per copy (a release you own twice shows twice), five a page by default:

```
Collection: me (page 1, 5 of 250 records)

@r847868 "Nine Inch Nails - The Downward Spiral" 1994 · Nothing Records 92346-2 · CD (Album) · folder Vinyl · added 2024-01-04 · rating 4
...

Next page: agent-discogs get collection --page 2
```

Each row is the ref, `artist - title` (just the artist when Discogs sent no title), year, label and catalog number, format, then the copy's folder by name (`· folder 123` for one the folder list does not name), `· added YYYY-MM-DD` (the day it was added to the collection, in the timestamp's own offset, as JSON `added` gives it) and `· rating N` when you rated it; with `--fields`, one `· Name: value` per custom field the copy has a value for follows (below). Use `--limit` (1-100, default: 5) and `--page` (from 1); the printed `Next page:` command repeats `--folder`, `--fields`, `--limit`, the filters and the sort.

- **Sorting.** `--sort label|artist|title|catno|format|rating|added|year` and `--desc` are sent to Discogs (`sort`, `sort_order`), so they order the whole collection, not just a page; Tab completes the keys. An unknown key, or `--desc` without `--sort`, fails with `invalid_argument` before any request.
- **Filters.** `--format`, `--genre`, `--style`, `--year`, `--label`, `--artist` and `--title` keep the records with a matching value: a format name or description (`Vinyl`, `12"`, `LP`), a genre, a style, a label name (any of the record's labels, though the row shows the first), the year, a credited artist's name (a split release matches any of its artists), the release title (without the artist). A value is a pattern that matches one whole value of the record, ignoring case: `--style Tech` matches nothing, `--style techno` matches `Techno`, `--artist miles` needs the full name. `*` matches any run of characters, none included (`--title '*live*'`, `--style '*bop'`, `--year '197*'`); `\` makes the next character literal (`--artist '\*NSYNC'` matches only `*NSYNC`, `\\` is a backslash). `?` and `[` are literal, and a match is not accent-insensitive (`bjork` does not match `Björk`; `bj*rk` does). Single-quote a pattern: unquoted, the shell expands `*` and removes `\`. Several filters must all match; an empty value fails with `invalid_argument`. Collection records carry no country, so there is no `--country`.
- **`--folder NAME`** lists only that folder, named as the rows print it (`Jazz` for `· folder Jazz`), any case, matched against the folder list (`All` is every record, `Uncategorized` the default folder). It is not a filter and takes no pattern: Discogs lists a folder itself, so alone it pages server-side (`--page` works; `--after` and `--max-requests` fail with `invalid_argument` before any request) and the header counts the folder's own records (`Collection: me folder=Jazz (page 1, 5 of 7 records)`). With a filter it is the scan below over that folder's pages only. `--sort` and `--desc` are sent to the folder listed. A name that matches no folder fails with `invalid_argument` and lists the folders, as does one that matches two (`Jazz` and `jazz`), after the username and the folder list were read.
- **`--fields`** shows each copy's notes and custom fields by name after the rating: `@r847868 "Nine Inch Nails - The Downward Spiral" 1994 · Nothing Records 92346-2 · CD (Album) · folder Vinyl · added 2024-01-04 · rating 4 · Media Condition: VG+ · Sleeve Condition: VG+ · Notes: Shelf: A2, Position: 3`. Discogs sends a copy's values by field id; the CLI reads your field names (`GET /users/{username}/collection/fields`) once per command, and only with this flag. A field shows only when the copy has a non-empty value, in the order of the field's position in Discogs' list (ties and fields without one by id), not the order of the record's `notes`. A value prints as Discogs sent it, except that a value of a dropdown field that is a Discogs grade name prints as its short code (`Very Good Plus (VG+)` → `VG+`, as in `get inventory` rows); `Generic` and any other dropdown value print as sent, and a free-text value (Notes) is never rewritten, a line break in it reading as a space in the text row. A field id the list does not name prints as `field 9`, and two fields with one name as `Name (field N)`. It is not a filter: no row is dropped for a missing value. It combines with every filter, `--sort`, `--limit`, `--page`, `--after` and `--max-requests`, and fails with `invalid_argument` before any request with `--json --full` (which prints the records as sent, `notes` by field id, so it stays unchanged) and with `--value`.
- **A filter makes it a client-side scan.** Discogs cannot filter a collection, so the CLI reads 100-record pages of it (of the `--folder`, when given) in the requested order and keeps the matches until `--limit` is filled or the collection ends, like the other [client-side scans](#client-side-scans): the header shows `≤N` totals, `--page` is rejected, the `Continue scan:` / `Next page:` command continues with `--after`, `--max-requests N` caps the sends, and a 429 after the first page keeps the rows read so far (a 429 on the first page is the usual `rate_limited` error). Without `--sort` or `--folder`, these are the same requests the `--owned` collection read sends, so a scan after `search` or `get inventory` with `--owned` within the hour reads its pages from the cache.
- **Cost.** One request for the username (shared with `--owned`, `get owned`, `get lists` and `get wantlist`), one for your folder names, one more for your field names with `--fields`, then one per page (or per 100 records scanned). The username, the folder list and the fields list are read before the listing, in that order, the way `get releases` reads its header first, so `--max-requests` counts them: `--max-requests 2` on a cold cache spends itself on the username and the folder list and returns an empty capped page (with `--fields` it takes `--max-requests 3`, and `4` reads one page), and a cap the username alone spends skips the folder list and the fields list too (with `--folder`, no page is read and the continuation, which repeats `--folder` and `--fields`, resolves it on the next run). `--json --full` names folders by id (`folder_id`) and skips the folder list, unless `--folder` needs it to resolve the name. A 429 on the fields list fails the command with `rate_limited` before any listing request, as one on the folder list does; an unknown or ambiguous `--folder` fails before the fields list is read. Every response is cached for an hour, so a rerun costs nothing once the read completed and its responses are still cached; a scan cut short by `--max-requests` or a 429 sends the pages it did not reach.
- **It takes no ref and needs a user.** `get collection @r847868` fails with `invalid_argument`, as do `--after` without a filter and `--max-requests` without a filter, [`--count-by`](#--count-by) or [`--duplicates`](#--duplicates) (`--folder` alone is not one). A token or OAuth credentials name a user; a consumer key/secret or no credentials do not, and the command fails with `auth_required` before any request (see [Errors](errors.md#--owned---in-wantlist-get-owned-get-collection-get-lists-and-get-wantlist-need-a-user)). It reads only the authenticated user's own collection: there is no `--user` flag, and the CLI neither adds, moves, rates nor removes records.
- **`--cached`.** The username, the folder list, the fields list (with `--fields`) or a page the cache does not hold fails the command with `cache_miss`.
- **`--json`:** `{"pagination":{...},"results":[{"ref","title","artists","year","label","catno","format","genres","styles","master","folder","added","rating","fields"}]}`, `added` an RFC 3339 timestamp and `rating` omitted when unrated. `artists` (`[{"ref","name"}]`, in credit order), `genres`, `styles` and `master` (the release's master ref, omitted when Discogs sends none or `0`) are release data the text row does not show, read from the same response; each is omitted when empty. `fields` is there only with `--fields`: an object from field name to value (`{"Media Condition":"VG+","Notes":"Shelf A3"}`, the order and the grade codes of the text row, line breaks kept), omitted when the copy has no non-empty value. `--json --full` prints the records as the SDK normalizes them (`CollectionItem`: `id`, `instance_id`, `folder_id`, `date_added`, `rating`, `notes`, `basic_information`). See [JSON output](json-output.md#collection).

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

#### --count-by

`get collection --count-by KEY` prints how many records you own per value of `KEY`, in place of the listing. `KEY` is `label`, `year`, `decade`, `format`, `genre`, `style` or `artist` (Tab completes it; any other is a usage error, exit 2):

```bash
agent-discogs get collection --count-by artist --top 10
agent-discogs get collection --style Industrial --count-by label
```

```
Collection: me style=Industrial by label (16 of 250 records)

Nothing Records [@l647]: 12
Self-released: 3

1 record has no label
```

- **What is counted.** One count per record, so a release you own twice counts twice, as in the listing. A record counts once for each distinct value it has: a record with two genres counts in both, so the counts can add up to more than the records. `genre` and `style` count each genre and style; `label` and `artist` each distinct label or credited artist, grouped by its id (by name when Discogs sent no id), with its ref after the name (a split release counts for both artists); `format` each format **name** (`Vinyl`, `CD`, `Cassette`; 2×Vinyl counts once, Vinyl plus CD in both; `--format "12\""` still filters by a description); `year` the release's year (the pressing's, as `--year` uses, not the master's); `decade` its decade (`1990s`).
- **Grouping and order.** Values that differ only by case are one group, spelled as in the first record that has it. Counts are ordered largest first, ties by value. A record with no value for the key (no genre, a year of 0 or none) counts nowhere and is reported on a line of its own after the counts: `1 record has no genre`, `3 records have no year` (for `year` and `decade`).
- **`--top N`** (1 or more) keeps the N largest counts and says how many values it left out: `… 38 more values`. Without it every value prints. `--top` needs `--count-by`; `--top 0` fails with `invalid_argument`.
- **Filters** (`--format`, `--genre`, `--style`, `--year`, `--label`, `--artist`, `--title`) narrow what is counted, at no extra cost: `--style Techno --count-by label` is the labels of your Techno records, and the header says how many records were counted: `Collection: me style=Techno by label (12 of 250 records)`. A kept record counts all of its values: `--artist Miles --count-by artist` also counts the other artists credited on the kept records.
- **`--folder NAME`** counts only that folder: the header names it (`folder=Jazz`) and its counts and total are the folder's. The folder list is read to resolve the name (an unknown or ambiguous one fails with `invalid_argument` before any page), then the folder's own pages.
- **Cost.** It reads the whole collection: one request for the username, then one per 100 records, unsorted, so on a cold cache `1 + max(1, ceil(N/100))` for N records (`2 + max(1, ceil(F/100))` with `--folder`, for its F records). These are the pages `--owned` reads, and a filtered `get collection` without `--sort` reads as far as its scan went, so a count within the hour costs only the pages not cached yet: nothing after a completed `--owned` read or a rerun of a completed count, while the pages are still cached; an `--owned` read that was capped or hit a 429 leaves pages to send. No folder list is read without `--folder`: nothing printed names a folder. `--fresh` sends it all again; `--cached` fails with `cache_miss` when the username or a page is not cached, with nothing sent.
- **`--max-requests N`** caps the sends of the command, the username first (then the folder list with `--folder`), and is accepted with or without a filter. A read it stops gives the counts of the records read, a lower bound, and the header says so: `Collection: me by style (200 of 250 records; scan stopped at --max-requests 3 after 200 records)`, or `(--max-requests 1 ran out before the scan started)` with no count when no page was read. There is no `Continue scan:` line: rerun with a higher cap, and the pages already read come from the cache (with the same cap, a rerun reads the next pages).
- **A 429** on the username or the first page is the usual `rate_limited` error. On a later page, the pages read so far are counted, exit 0, and the header says `scan stopped: rate limited, retry in 30s, after 100 records`.
- **What it takes.** No `--limit`, `--page`, `--after`, `--sort` or `--desc` (the read has no page and no order), no `--fields` and no `--json --full` (there are no rows): each fails with `invalid_argument` before any request, as `--value` with it does.
- **`--json`:** `{"count_by":"label","records":12,"without_value":0,"distinct":2,"counts":[{"value":"Nothing Records","ref":"@l647","count":9},{"value":"Self-released","count":3}],"collection":{"complete":true,"records_read":250,"total_records":250}}`. See [JSON output](json-output.md#collection).

#### --duplicates

`get collection --duplicates release|master` lists the releases, or the masters, you own more than once, in place of the listing (any other value is a usage error, exit 2):

```bash
agent-discogs get collection --duplicates release
agent-discogs get collection --duplicates master --format Vinyl
```

```
Duplicates: me by master (1 master owned more than once, 3 copies, in 250 records)

@m3719 owned ×3 in 2 releases
  @r847868 "Nine Inch Nails - The Downward Spiral" 1994 · Nothing Records 92346-2 · CD (Album) · owned ×2 · folders Vinyl, To Sell
  @r352665 "Nine Inch Nails - The Downward Spiral" 1994 · … · CD (Album) · owned · folder Vinyl

4 records have no master and are not checked (--duplicates release looks at their copies).
```

- **Groups.** The collection holds one record per copy. `release` groups the records of one release id, `master` the records whose release has the same master (Discogs' `master_id`, `0` or none being no master): two copies of one pressing, or the LP and the CD of an album. A group has at least 2 records. Groups are ordered by copies, most first, then by the position of their first copy in the collection; within a master, releases follow the position of their first copy. A record with no master is in no master group: the last line counts them, and `--duplicates release` finds their duplicates.
- **Rows.** A release is its `get collection` row, from its first copy, without the copy's folder, day added and rating, then `· owned ×N` and the folders its copies are in, each once, in the order of the copies (`· folder Vinyl` for one, `· folders Vinyl, To Sell` for more; `folder 123` for a folder the folder list does not name). A master is `@m3719 owned ×3 in 2 releases`, then one indented row per release. The header counts the groups, their copies and the records checked: `(1 release owned more than once, 2 copies, in 250 records)`, `in 12 of 250 records` under a filter, or `(none in 250 records)`.
- **Filters** (`--format`, `--genre`, `--style`, `--year`, `--label`, `--artist`, `--title`) choose the records checked, at no extra cost: `--format Vinyl --duplicates master` is the albums you own on vinyl more than once, and a pair of which only one copy passes the filter is no duplicate.
- **`--folder NAME`** checks only that folder's copies: a copy in another folder is no partner. Its folder list is read to resolve the name, also under `--json --full`, then the folder's own pages.
- **`--fields`** adds the first copy's notes and custom fields to its row, as in the listing (`· Media Condition: VG+`), for one more request, the fields list, read after the folder list. Not with `--json --full`.
- **Cost.** It reads the whole collection like [`--count-by`](#--count-by): one request for the username, one for the folder list (to name folders; `--json --full` names them by id and skips it, unless `--folder`), then one per 100 records, so on a cold cache `2 + max(1, ceil(N/100))` for N records. These are the pages `--owned` reads, shared within the hour. `--fresh` sends it all again; `--cached` fails with `cache_miss` when anything is not cached, with nothing sent.
- **`--max-requests N`** caps the sends of the command, the username and the folder list first (as in the listing), and is accepted with or without a filter. A read it stops, or a 429 after the first page, gives the groups found in the part read: a group shown is real, but it may have more copies, and a release or master not shown may still be owned twice. The header says where it stopped (`(none found so far in 100 of 250 records; scan stopped at --max-requests 3 after 100 records)`, or `(--max-requests 2 ran out before the scan started)`) and the last line is `Copies and groups may be missing: only part of the collection was read.` There is no `Continue scan:` line: rerun, and the pages already read come from the cache. A 429 on the username, the folder list or the first page is the usual `rate_limited` error.
- **What it takes.** No `--limit`, `--page`, `--after`, `--sort`, `--desc`, `--count-by` or `--value`: each fails with `invalid_argument` before any request.
- **`--json`:** `{"duplicates":"release","groups":[{"ref":"@r847868","title":"Nine Inch Nails - The Downward Spiral",...,"copies":2,"folders":["Vinyl","To Sell"]}],"records":250,"collection":{"complete":true,"records_read":250,"total_records":250}}`. A release entry is the `get collection --json` row of its first copy without `folder`, `added` and `rating`, plus `copies` and `folders`. A master group is `{"ref":"@m3719","copies":3,"releases":[...]}`, one release entry each, and the document adds `without_master`. `--json --full` gives each group as `{"ref","instances":[...]}`, its collection records as the SDK normalizes them. See [JSON output](json-output.md#collection).

#### --random

`get collection --random [N]` prints N records drawn at random from your collection, in place of the listing. `N` is 1 to 100 and optional (`--random` alone is 1; `--random --json` is one record as JSON):

```bash
agent-discogs get collection --random
agent-discogs get collection --random 3 --genre Jazz --format Vinyl
```

```
Collection: me random 3 (3 of 250 records)

@r847868 "Nine Inch Nails - The Downward Spiral" 1994 · Nothing Records 92346-2 · CD (Album) · folder Vinyl · added 2024-01-04 · rating 4
...
```

- **The draw.** N distinct records, one per copy as in the listing (a release owned twice has twice the chance), drawn uniformly and printed in collection order, as `get collection` rows. When fewer than N records qualify, all of them print (`(10 of 10 records)`). Nothing is stored between runs, so a rerun draws again; there is no seed.
- **Unfiltered**, it reads only the pages it needs: the username, the folder list (not under `--json --full`), page 1 at 100 records (which gives the total), then each other page that holds a pick, once, in ascending order. On a cold cache that is `3 + d` requests (`2 + d` under `--json --full`), `d` being the pages other than page 1 that hold a pick: at most `3 + min(N, P - 1)` for a collection of P pages. These are the pages `--owned` and the filtered scans read, so a draw after them costs only the folder list. A 429 on any page fails the command with `rate_limited` and prints nothing: a draw from part of the collection would not be uniform. Under `--cached`, a page the draw needs and the cache lacks fails with `cache_miss`.
- **Filtered** (`--format`, `--genre`, `--style`, `--year`, `--label`, `--artist`, `--title`): the matches are known only after the whole collection is read, so it reads it all, as [`--count-by`](#--count-by) does (`2 + max(1, ceil(N/100))` cold for N records), and draws from the records kept: `Collection: me style=Techno random 2 (2 of 12 matching records)`, or `(0 of 0 matching records)` and no row when nothing matches.
- **`--max-requests N`** is accepted with a filter only (an unfiltered draw has nothing to cap). It counts the username and the folder list first. A scan it stops, or a 429 after the first page, draws from the records kept so far, and the header says so: `(1 of 4 matching records; scan stopped at --max-requests 3 after 100 records)`. A cap spent before the first page draws nothing: `(--max-requests 2 ran out before the scan started)`.
- **`--folder NAME`** draws from that folder only, reading the folder list to resolve the name (also under `--json --full`) and then the folder's own pages; alone it is the unfiltered draw. **`--fields`** adds each row's notes and custom fields, for one more request after the folder list.
- **What it takes.** No `--limit` (N is the count), `--page`, `--after`, `--sort`, `--desc`, `--count-by`, `--duplicates` or `--value`: each fails with `invalid_argument` before any request, as does N outside 1-100 (`--random must be between 1 and 100 (0 given).`). A ref after `--random` is read as N: `get collection --random @r847868` is a usage error (exit 2).
- **`--json`:** `{"random":{"requested":3,"population":250},"results":[...]}`, `results` holding the `get collection --json` rows (`--json --full`: the records as the SDK normalizes them) and `population` the records drawn from (the collection's size, or the matches). A filtered draw adds the `collection` object of `--count-by`. There is no `pagination`. See [JSON output](json-output.md#collection).

### wantlist

`get wantlist` lists your own Discogs wantlist, five wants a page by default:

```
Wantlist: me (page 1, 5 of 150 wants)

@r847868 "Nine Inch Nails - The Downward Spiral" 1994 · Nothing Records 92346-2 · CD (Album) · notes: Sealed only
...

Next page: agent-discogs get wantlist --page 2
```

Each row is the ref, `artist - title` (just the artist when Discogs sent no title), year, label and catalog number, format, and `· notes: ...` when you wrote notes on the want. Use `--limit` (1-100, default: 5) and `--page` (from 1); the printed `Next page:` command repeats `--limit`.

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
- **`--currency CODE`** is one of `USD`, `GBP`, `EUR`, `CAD`, `AUD`, `JPY`, `CHF`, `MXN`, `BRL`, `NZD`, `SEK`, `ZAR` (any case, sent uppercase; Tab completes it) and is sent with every stats request, so the prices shown and `--max-price` are in that currency. Without it Discogs prices in your account's buyer currency, and every price names it (`USD 12.50` in text, `market.currency` in JSON). **`--max-price` needs `--currency`**: the threshold is a bare number, so the CLI makes you name its currency instead of assuming the account's.
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

A blank value (empty, or only spaces) of `--role`, `--year`, `--country`, `--format`, `--label` or `--sort` is `invalid_argument` before any request, rather than dropped: `--year needs a year, such as 1999 ('' given).`; a blank `--sort` gets the unknown-key message (`--sort '' is not valid for 'get releases'. Keys: year, title, format.`).

Each row of `versions` and of both `releases` listings ends in Discogs' community counts, `· have N · want N` (how many Discogs users own the release and how many want it), before the `--owned` mark. They come with the listing page, so they cost no request and need no credentials (`--json`: `have`, `want`). A count Discogs did not send is left out; `0` is shown.

```
@r847868 1994 US · Nothing Records 92346-2 · CD, Album · have 7,544 · want 1,113
```

On an artist's discography a master row shows the counts Discogs sends for the master; Discogs does not document how it derives them, and the CLI shows them as sent.

#### Repeated rows in a label catalogue

Discogs sends some rows of a label catalogue twice (or three times) in a row, identical in every field: on Blue Note, about one row in five. `get releases @l...` drops a row equal to the row Discogs sent just before it, so a run of equal rows shows once, and says how many it dropped in the header:

```
Releases on @l281 "Blue Note" (page 1, 2 of 40,221 results; 1 repeated row dropped)
```

- **Server-side pages** (`--page`, the default): the rule applies to the one page Discogs returned, so a page can show fewer rows than `--limit`. The total is Discogs' own and still counts the repeats; `Next page:` is `--page N+1`, and dropping a row costs no request.
- **The `--year` scan** drops a repeat as it reads, comparing each row with the row before it in the listing, whatever its year, so the page still fills with `--limit` distinct matches. The note counts the dropped repeats of matching rows; the cursor names the next row to scan and the cost is the scan's (1 request per 100 rows), as without repeats.
- A release listed twice with a different catalogue number or format is two rows, and both stay. Artist discographies are not affected: a release on several rows there carries a different `role` on each.
- Only a repeat within one page is seen: when the two rows of a pair fall on either side of a page break, the next page starts with the last row of the page before (about one page in five at the default `--limit 5`; `--limit 100` nearly always avoids it). A `--year` page that fills on the first of two equal rows likewise leaves the second to start the next page.

Under `--json`, `pagination` adds `duplicates_dropped` when the page dropped any row (see [Pagination](json-output.md#pagination)). `--owned` marks the rows that remain.

#### --release-type

`get versions @m... --release-type official|unofficial|all` filters on the format description "Unofficial Release", which Discogs gives bootlegs, counterfeits and pirate copies alike, the same rule `search --release-type` uses. A version row's format lists its descriptions (`LP, Album, Unofficial Release`), and the match is on the whole description, so a "Partially Unofficial" version counts as official.

- **`all` is the default**, unlike `search`, whose default is `official`: without the flag, `get versions` lists every version, unofficial pressings included, with server-side pages. The bootleg and counterfeit checks need to see them.
- **`unofficial`** is filtered by Discogs itself (`format=Unofficial Release`), so it shows an exact total and pages with `--page`. With `--format` it is a client-side scan instead, as on `search`, because the Unofficial Release filter is a `format` filter too.
- **`official`** drops the unofficial versions in a client-side scan, like `get releases --role`: it reads 100-row pages until `--limit` versions are kept or the list ends, shows the total as an upper bound (`≤N`), continues with the printed `--after` cursor (`--page` fails with `invalid_argument`), takes `--max-requests N`, and keeps the rows read so far when Discogs answers 429 after the first page (see [Client-side scans](#client-side-scans)). As text, the header's master title is read before the scan, so `--max-requests` counts it.

```bash
agent-discogs get versions @m3719 --release-type official --limit 5 --json
agent-discogs get versions @m3719 --release-type unofficial --country UK
```

`releases` and `versions` also take `--owned`, which marks each release row with your own copies, and `--wanted`, which marks it with your wantlist: see [Marking rows with --owned](#marking-rows-with---owned) and [Marking rows with --wanted](#marking-rows-with---wanted).

#### --market

`get versions @m... --market` ends each pressing's row with its Marketplace numbers, the count for sale and the lowest asking price, as `get stats` and `get wantlist --for-sale` print them, after the `--owned` and `--wanted` marks:

```
Versions of @m3719 "The Downward Spiral" (page 1, 2 of 182 versions)

@r847868 1994 US · Nothing Records 92346-2 · CD, Album · have 7,621 · want 1,354 · for sale: 45 from EUR 0.79
@r352665 2004 US · Interscope Records B0003739-36 · Album, Deluxe Edition, Remastered · have 1,978 · want 735 · for sale: 9 from EUR 71.20
```

A version row carries no market data, so the CLI asks Discogs for each row's stats (`GET /marketplace/stats/{id}`, cached for an hour) after the listing: **one request per row shown**, on top of the listing's own cost. For a server-side page that is 1 + N for N rows with `--json`, 2 + N as text (the master title of the header, read first), and 2 + N with a release ref in either mode (the release lookup that finds the master and gives the header its title); `--limit 100` is 101 requests with `--json`. A `--release-type official` (or `unofficial` with `--format`) listing is a scan whose S pages come first: S + N with `--json`, 1 + S + N as text, and 1 + S + N with a release ref. Under `--max-requests` fewer rows are looked up. `--limit 20 --market` prices the first 20; a rerun costs nothing once every row's lookup has run and is still cached, and after a partial read (`--max-requests`, a 429) it sends only the rows not read yet. `--fresh` sends them all again. No user is needed: the stats endpoint answers anonymous callers, at 25 requests a minute.

- **Currency**: each price carries the code Discogs priced it in, the account's buyer currency (USD without credentials) unless `--currency CODE` asks for another; `--currency` goes on every stats request and is accepted on `get versions` only with `--market` (`--currency needs --market with get versions.`). A cache entry is per URL, so rows read in one currency are not reused for another.
- **Nothing for sale**: Discogs sends `null` counts for a release with nothing for sale and for one blocked from sale; both print `· for sale: 0`. A count with no price prints `· for sale: 3`, with no `from`.
- **`--max-requests N`** is accepted with `--market` and caps the whole command: the text header and the listing page are always sent, then each row's lookup only while the command has sent fewer than N requests (cache hits are free). The rows not reached are left unchecked, and a note before the footer says so: `Market: the lookups stopped at --max-requests 4 (2 of 5 rows checked): rows without "for sale:" were not checked.` Under a `--release-type` scan the cap is shared: the lookups get what the scan leaves. It takes one ref.
- **429**: a 429 on a lookup stops the lookups and keeps the rows read, with the note `Market: a lookup was rate limited, retry in 30s (2 of 5 rows checked): ...`, exit 0; a rerun after the wait resumes from the cache. When a `--release-type` scan was itself stopped by a 429 after its first page, no lookup is sent: its rows print unchecked under `Market: the listing was rate limited, ...`. A 429 on the first listing page, or any other failure of a lookup (a 5xx, a 404 for a release Discogs removed), fails the command.
- **`--cached`**: a row whose stats are not cached ends `· ✗ Not cached: GET /marketplace/stats/847868`, the text adds `Rerun without --cached to check the rows marked ✗.`, and the command exits 1. As text the header must be cached too, or the command fails with `cache_miss` before any row.
- `Next page:` repeats `--market` and `--currency`.

```bash
agent-discogs get versions @m3719 --format Vinyl --market --currency EUR --limit 20
agent-discogs get versions @m3719 --market --limit 100 --max-requests 60 --json
```

### artist and label

Every navigable name in `artist` and `label` output carries a ref: members and former members, parent label, sub-labels. Their `URLs:` line lists each linked site by domain, omitting empty entries. `Former:` lists only members Discogs marks inactive; a member with no recorded status stays under `Members:`.

### Flags per noun

Each flag applies only to the nouns below. Any other flag you pass (even `--limit 5`, the default value) fails with `invalid_argument` before a request is made, and so do `--after` and `--max-requests` with more than one ref, since a cursor continues one listing and a cap is spent on one.

| Noun | Flags besides `--json` / `--full` |
|---|---|
| `release` | `-v, --verbose`, `-c, --compact`, `--master-year` |
| `releases @a...` | `--limit`, `--page`, `--role`, `--sort year\|title\|format`, `--desc`, `--owned`, `--wanted`, `--after` and `--max-requests` (with `--role`) |
| `releases @l...` | `--limit`, `--page`, `--year`, `--owned`, `--wanted`, `--after` and `--max-requests` (with `--year`) |
| `versions` | `--limit`, `--page`, `--country`, `--format`, `--label`, `--year`, `--sort released\|title\|format\|label\|catno\|country`, `--desc`, `--release-type official\|unofficial\|all`, `--owned`, `--wanted`, `--market`, `--currency` (with `--market`), `--after` (with a `--release-type` scan) and `--max-requests` (with a `--release-type` scan or `--market`) |
| `collection` | no ref; `--limit`, `--page`, `--sort label\|artist\|title\|catno\|format\|rating\|added\|year`, `--desc`, `--format`, `--genre`, `--style`, `--year`, `--label`, `--artist`, `--title`, `--folder`, `--fields`, `--after` and `--max-requests` (with a filter, not `--folder` alone); or `--count-by label\|year\|decade\|format\|genre\|style\|artist` with `--top`, the filters, `--folder` and `--max-requests`; or `--duplicates release\|master` with the filters, `--folder`, `--fields` and `--max-requests`; or `--random [N]` with the filters, `--folder`, `--fields` and `--max-requests` (with a filter); or `--value` alone |
| `lists` | no ref; `--limit`, `--page` |
| `wantlist` | no ref; `--limit`, `--page`, `--for-sale`, `--max-price` and `--currency` (both with `--for-sale`), `--after` and `--max-requests` (with `--for-sale`) |
| `inventory` | one username, not a ref; `--limit`, `--page`, `--sort listed\|price\|item\|artist\|label\|catno`, `--desc`, `--owned`, `--condition`, `--sleeve`, `--ships-from`, `--in-wantlist`, `--max-price` and `--currency` (each needs the other), `--after` (with a filter) and `--max-requests` (with a filter or `--owned`) |
| `stats` | `--currency` |
| `artist`, `credits`, `identifiers`, `label`, `list`, `master`, `owned`, `price`, `tracklist` | none |

## tracks / price

Shortcuts for `get tracklist` and `get price`. Both support `--json`.

```bash
agent-discogs tracks @r352665
agent-discogs price @r352665
```

`price` prints each amount with the ISO currency code Discogs returns, never a symbol: `Mint (M): EUR 20.00`, `Lowest: GBP 9.99`. Under `--json` every price carries its `currency`: `"suggestions":{"Mint (M)":{"value":20.0,"currency":"EUR"}}`, `"market":{"for_sale":9,"lowest":9.99,"currency":"GBP"}`.

The `Market: 9 for sale from 9.36` line of `get release` and `get master` is a bare amount: Discogs prices it in the token account's buyer currency (USD when unauthenticated) without naming it, so there it has no code and no `currency` key. Use [`get stats`](#stats) (the count, the lowest price and the community numbers, no seller settings needed) or `price` when the currency matters.

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
agent-discogs skills list --json         # [{"name":...,"description":...,"version":...}], one object per skill
agent-discogs skills get core            # setup, refs, search and navigation, output, budget, troubleshooting
agent-discogs skills get core --full     # plus references: command reference, errors, JSON output, search patterns, Discogs data model, tasks
agent-discogs skills get collection      # your collection, lists and ownership marks (--owned, get owned, get collection, get lists, get list)
agent-discogs skills get marketplace     # price, get wantlist and --for-sale, get inventory
agent-discogs skills get pressings       # telling pressings apart: versions, identifiers, unofficial releases
agent-discogs skills path [name]         # directory holding the bundled skill files
```

`--full` appends only that skill's own references. An unknown name exits 1 and lists the available ones.

Each skill also carries Tasks: recipes for recurring requests ("do I own any pressing of this album?", "is anything on my wantlist under €25?"), each under a stable ID with its status (`works`, `works, costly` or `not possible`), the commands, their request cost and, when it is costly or not possible, what would change that. `skills get core` lists every task ID under the skill that holds it; the core tasks themselves are in its `--full`.

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
- `get` nouns, `ids` included, the `--currency` codes of `get wantlist`, `get inventory` and `get stats`, and the grades of `--condition` and `--sleeve` (`M NM VG+ VG G+ G F P`);
- the `search` type word (`release`, `master`, `artist`, `label`), only as the first positional after `search` (options or `--` may come before it); the query after it gets no suggestions;
- `search --release-type`, `search --sort`, the global `--color`, `cache clear`, the `skills` subcommands and skill names, and the `completion` shells;
- `get --sort`: the keys of the noun typed before it (`get versions --sort <Tab>` offers `released title format label catno country`, `get collection --sort <Tab>` offers `label artist title catno format rating added year`, `get inventory --sort <Tab>` offers `listed price item artist label catno`), or every key of `releases`, `versions`, `collection` and `inventory` before a noun, and none after a noun that takes no `--sort`. A key the noun does not take is still rejected when the command runs.

Refs, queries, Discogs names and free-text filters (`--artist`, `--format`, `--role`, ...) are not completed: refs are stateless, so there is no list to offer.
