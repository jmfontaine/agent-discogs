# Collection Command Reference

## Nouns

### `--owned`

`--owned` (on `search`, `get versions`, `get releases @a...|@l...`, `get inventory`) marks each *release* row with your own copies: `· owned` (one copy), `· owned ×2` (more), `· not owned`, or `· owned ?` (JSON `owned`: the count, `0`, or `null`; on `search` and `get inventory` plus a `collection` object `{"complete","records_read","total_records"}`, and `stop_reason`/`retry_after` when the read stopped early). `have N` is everyone's count, `owned` is yours. Master, artist and label rows get no mark (a collection holds releases), so `search artist|label|master ... --owned` and `--owned --added-since` are rejected before a request. **`get versions` and `get releases`** rows carry your own count from Discogs (`stats.user.in_collection`), so `--owned` sends nothing more there (no username, no collection page, no `collection` object; `get versions @m... --owned --json` is 1 request cold, 2 as text); a row without the count reads `owned ?` (JSON `null`), with no note. `--max-requests` there needs a scan (`--release-type official`, `--role`, a label's `--year`) and caps it alone; with `--owned` alone it is `invalid_argument`. Under `--cached` it needs nothing beyond the listing's own cached requests; the marks are as old as the cached page (1h, `--fresh` re-reads it). **`search` and `get inventory`** read your whole collection once per command (1 request for the username, then 1 per 100 records; cached an hour, so a rerun is free), and a page with no release row reads nothing. `--max-requests N` counts every send of the command (listing, username, collection pages) but only stops scans and the collection read: a server-side listing page is always sent, then the username and collection pages while the cap allows. A read cut short by the cap, or by a 429 after its first page, leaves `owned ?` rows (never a false `not owned`; a count found in the part read may be short) and a note `Owned: the collection read stopped at --max-requests 3 (100 of 250 records read): ...` before the footer. A 429 on the username or the first collection page, or after the listing's own scan stopped on a 429, never fails the command: no collection is read, every release row is `owned ?` (`collection.stop_reason: "rate_limited"`, `records_read: 0`). The printed `Next page:` / `Continue scan:` repeats `--owned`. A record added in the last hour may show as not owned; `--fresh` re-reads the collection, and `--cached` fails with `cache_miss` on a collection read it does not hold.

### `get owned @r... [@r...]`

Your copies of each release, one request per ref: `@r352665 owned ×2: Vinyl, Uncategorized` (one copy: `owned: Vinyl`) or `@r847868 not owned` (JSON `{"ref":"@r352665","owned":2,"folders":["Vinyl","Uncategorized"]}`, `{"ref":"@r847868","owned":0}`; `--json --full` is `{"instances":[...]}`, the SDK's collection records with folders by id, and sends no folder-list request). Cost: 1 for the username, 1 per ref, 1 for the folder list when any ref is owned. A copy in a folder Discogs does not name adds no folder name (no names at all: `owned` without a colon, JSON without `folders`). Needs a token or OAuth. Takes only `--json` / `--full`; several refs behave as for the other nouns. Use it for a few refs; use `--owned` for a page of rows.

### `get owned @m... [@m...]`

Whether you own any release of a master, and which: `@m3719 owned ×3 in 2 releases` (the copies, counted from two as for a release; `owned in 1 release` for one copy), then one indented line per release you own, exactly as `get owned @r...` prints it (`  @r352665 owned ×2: Vinyl, Uncategorized`), in the order of each release's first copy in the collection; or `@m3719 not owned`. Discogs has no ownership endpoint keyed by master, so the first master ref reads your whole collection (the username, then the "All" folder at 100 records a request: the pages `--owned` and a filtered `get collection` cache) and every master ref of the command answers from it, matched on each record's `basic_information.master_id` (a record with no master matches none). Cost: 1 for the username + 1 per 100 records, once per command whatever the number of master refs or versions, + 1 for the folder list when any copy is found; release refs mixed in add 1 each and share the username and folder list. No master request is sent: a master that does not exist reads `not owned`. A raw id is a release; another typed ref (`@a`, `@l`) is `invalid_argument`. JSON: `{"ref":"@m3719","owned":3,"releases":[{"ref","owned","folders"}],"collection":{"complete":true,"records_read":250,"total_records":250}}`, or `{"ref":"@m3719","owned":0,"collection":{...}}` (no `releases`); every master document carries `collection`. `--json --full` is `{"instances":[...],"collection":{...}}`, the matching records as the SDK normalizes them, folders by id, with no folder-list request. A 429 during the read is no error (exit 0, no `--max-requests` here): a master answers from the part read, one with no copy there reads `@m3719 owned ?` (`owned: null`, `collection.stop_reason: "rate_limited"`), and the text ends with the `Owned:` note saying how much was read; a 429 on the folder list names the folders by id (`folder 5`), and the list is not asked for again in that command. To compare with `get versions @m... --owned`: that costs a request per 100 versions and none for the collection; this costs a request per 100 collection records and none per version.

### `get collection`

Your own collection, one row per copy (a release owned twice is two rows), 5 a page (`--limit` 1-100, `--page`), no ref (a ref is `invalid_argument`): header `Collection: me (page 1, 5 of 250 records)`, rows like `@r847868 "Nine Inch Nails - The Downward Spiral" 1994 · Nothing Records 92346-2 · CD (Album) · folder Vinyl · added 2024-01-04 · rating 4` (folder name, the day it was added, the rating when set). JSON rows: `ref`, `title`, `year`, `label`, `catno`, `format`, `folder`, `added` (RFC 3339), `rating` when set; `--json --full` is the SDK's `CollectionItem` records (`id`, `instance_id`, `folder_id`, `date_added`, `rating`, `notes`, `basic_information`; folders by `folder_id`). `--sort label|artist|title|catno|format|rating|added|year` and `--desc` go to Discogs (newest additions: `--sort added --desc`). **Filters** `--format` (a format name or description: `Vinyl`, `12"`, `LP`), `--genre`, `--style`, `--year`, `--label` (a label name) keep records where one value equals yours, ignoring case (`--style Tech` matches nothing); several must all match; an empty value is `invalid_argument`; no `--country` (records carry none). A filter makes it a client-side scan of 100-record pages: `≤N` totals, `--page` rejected, continue with the printed `Continue scan:` / `Next page:` (`--after`), cap with `--max-requests N`; `--max-requests` and `--after` need a filter. Cost: 1 for the username, 1 for the folder list (none with `--json --full`), then 1 per page or per 100 records scanned; the username and folder list come first and count against `--max-requests` (on a cold cache `--max-requests 2` returns an empty capped page). Without `--sort`, a scan reads the same pages as the `--owned` collection read, so it is free after `search` or `get inventory` with `--owned` within the hour. Needs a token or OAuth. Only your own collection: no `--user`, no folder selection, nothing adds, moves, rates or removes records.

### `get collection --value`

What your collection is worth, Discogs' estimate from Marketplace sales, in place of the listing: `Collection value (EUR): min 75.50 · median 100.25 · max 250.00` (JSON `{"currency":"EUR","minimum":75.5,"median":100.25,"maximum":250.0}`; `--json --full` is `{"currency":"EUR","value":{...}}`, the amounts as Discogs sent them). The currency is your profile's (`curr_abbr`), never a symbol. Cost: 3 (username, profile, value). Takes no other `get collection` flag (`invalid_argument` before any request). Needs a token or OAuth; only your own collection.

### `get lists`

Your own lists, private ones included, 5 a page (`--limit` 1-100, `--page`), no ref: header `Lists: me (page 1, 5 of 7 lists)`, rows like `123 "Pressings" · private · changed 2016-06-02 → get list 123` (the list id, its name, visibility and the UTC day it last changed; Discogs sends no item count). JSON rows carry `id`, `name`, `description` (markup converted), `public`, `added`, `changed` (RFC 3339), `url`; `--json --full` is the SDK's list summaries. Needs a token or OAuth. Cost: 1 for the username (cached an hour) + 1 per page of up to 100 lists. Only your own lists: no `--user`, no editing.

### `get list <id> [<id>...]`

One of your lists, or anyone's public list, by its id, a plain number (a typed ref such as `@r123` is `invalid_argument`: lists have no ref prefix): `List: 123 "Pressings" · private`, `Description:` with the whole description (markup converted, never cut, line breaks kept), then one row per item: `@r847868 "Nine Inch Nails - The Downward Spiral" · comment: check the matrix`. The ref follows the item's type (`@r`, `@m`, `@a`, `@l`); an item of another type reads `id 9 [type] "..."` with no ref. A comment is printed as written, Discogs markup such as `[b]` included. JSON: `{"id","name","description","public","url","items":[{"ref","title","comment"}]}` (`id` and `type` instead of `ref` for another type; `--json --full` is the SDK's list record). Cost: 1 per list, whatever its length. Takes only `--json` / `--full`. Works without credentials on a public list; someone else's private list fails with `not_found`, `auth_required` or `forbidden`. Several ids work like several refs.

## JSON

Under `--owned`, each release row also has `owned` (copies; `0` when none; `null` when undecided). On `search` and `get inventory` the document has `collection` next to `pagination` (no release row on the page means neither); `get versions` and `get releases` read no collection and have none.

`get collection --json` is `{"pagination":{...},"results":[{"ref","title","year","label","catno","format","folder","added","rating"}]}`, one row per copy; with a filter `pagination` is the filtered-scan envelope.

`get lists --json` is `{"pagination":{...},"results":[{"id","name","description","public","added","changed","url"}]}`; `get list <id> --json` is `{"id","name","description","public","url","items":[{"ref","title","comment"}]}`, each comment exactly as Discogs sent it.

## The `--owned` collection read (`search`, `get inventory`)

On `search` and `get inventory`, whose rows carry no reliable count of your own, `--owned` asks for the username after the listing page (1 request), then reads your whole collection, the "All" folder, at 100 records per request, once per command (every response cached 1h, so a rerun is free). `--max-requests N` counts every send of the command (the listing page, the username and the collection pages) but only stops scans and the collection read: a server-side listing page is always sent, then the username and collection pages are asked only while the cap allows. A cold `search release ... --release-type all --owned --max-requests 1` therefore sends the listing and reads no collection; with the cap already spent before the username, nothing is asked (`records_read: 0`, `total_records: null`). Rows the read did not reach say `· owned ?` (JSON `owned: null`) and never `not owned`; a count found in the part read may be short. A note line before the footer gives the cause: `Owned: the collection read stopped at --max-requests 2 (100 of 250 records read): "owned ?" rows are not in the part read, and a count may be short.` or `... the collection read was rate limited, retry in 30s (100 of 250 records read): ...`. A 429 on a later collection page keeps the records read. A 429 on the username or the first collection page never fails the command: no collection is read and every release row is `owned ?`, with `collection` = `{"complete":false,"records_read":0,"total_records":null,"stop_reason":"rate_limited","retry_after":N}`. Likewise, when the listing's own client-side scan stopped on a 429 (`pagination.stop_reason: "rate_limited"`), the collection is not read at all (no request) and the rows are `owned ?` with the listing's `retry_after`. On `get inventory` a row is looked up by its listing's release id (never the listing's own id), and a listing with no release id is `owned ?`.

On `search`, `--owned` takes an untyped search or `search release`, whose rows can be releases. Master, artist and label rows get no mark: a collection holds releases, and a master is not something you own (for any pressing of a master, use `get owned @m...`). The document's `collection` object reads like `{"complete": true, "records_read": 250, "total_records": 250}`, and `--json --full` adds `owned` to the SDK record.

## `--cached`

Under `--cached`, an `--owned` collection read (`search`, `get inventory`) fails with `cache_miss` on the username or a collection page the cache does not hold, `get owned @m...` fails so on each master ref, and `get collection` on the username, the folder list or a page. On `get versions` and `get releases`, `--owned` needs nothing beyond the listing's own cached requests (the page, and in text the header).

## Flags for `get collection`

| Flag | Description |
|------|-------------|
| `--sort` | `label`, `artist`, `title`, `catno`, `format`, `rating`, `added`, or `year` (server-side, over the whole collection; Tab completes them) |
| `--desc` | With `--sort`: descending (`--sort added --desc`: newest additions first) |
| `--format` | Keep records with this format name or description (`Vinyl`, `12"`, `LP`) |
| `--genre` | Keep records with this genre |
| `--style` | Keep records with this style |
| `--year` | Keep records from this year |
| `--label` | Keep records with this label name |
| `--value` | Instead of the listing, print the collection's value: see `get collection --value` under Nouns above. Amounts are parsed out of Discogs' strings: what precedes the first digit or follows the last is the symbol (a sign, parentheses, or a `.`/`,` right before the first digit is rejected); the last `.` or `,` followed by one or two digits is the decimal point; other `.`, `,`, spaces (no-break included) and `'` separate thousands in groups of three (`1,250` = 1250). Takes no other flag. |

A filter scan reads 100-record pages in the requested order, and a 429 after the first page keeps the rows read. On a cold cache `--max-requests 2` returns an empty capped page whose header says `--max-requests 2 ran out before the scan started`. Without `--sort`, within the hour of `search` or `get inventory` with `--owned`, the scan costs only the folder list. An unknown `--sort` key, `--desc` without `--sort`, and `--max-requests` or `--after` without a filter fail with `invalid_argument` before any request.

## Examples

```bash
agent-discogs search release --artist "Nine Inch Nails" --year 1994 --release-type unofficial --limit 100 --owned
agent-discogs get versions @m3719 --country US --owned
agent-discogs get releases @a3857 --role Main --owned --max-requests 6
agent-discogs get owned @r847868 @r352665
agent-discogs get owned @m3719                                                    # any pressing of a master
agent-discogs get collection
agent-discogs get collection --sort added --desc --limit 20                       # newest additions first
agent-discogs get collection --style Techno --format 12\" --max-requests 20      # client-side scan; continue with the printed --after
agent-discogs get collection --value                                              # what it is worth, in your currency
agent-discogs get lists
agent-discogs get lists --limit 100 --page 2
agent-discogs get list 123
agent-discogs get list 123 456 --json
```
