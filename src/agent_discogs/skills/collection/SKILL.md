---
name: collection
description: The user's own Discogs collection, lists and ownership marks. Load it when a task asks what the user owns, which of some releases they have, what they own twice, what to pick from it at random, what their collection holds or is worth, how many records they own per label, genre or year, or what is on a list - --owned, get owned, get collection (filters, sort, --count-by, --duplicates, --random, --value), get lists, get list.
allowed-tools: Bash(agent-discogs:*)
---

# agent-discogs: collection and lists

This skill assumes you have read `agent-discogs skills get core` (setup, refs, budget, errors). Every command here except `get list` reads the authenticated user's own account, so it needs a token or OAuth (`token` or `oauth` in `status`); under `consumer` or `none` it fails with `auth_required` before any request. Only your own account: no `--user`, and nothing adds, moves, rates or removes anything.

## What each command answers

- **Which of these rows do I own?** Add `--owned` to a listing (`search`, `get versions`, `get releases @a...|@l...`, `get inventory`): each release row ends `· owned`, `· owned ×2`, `· not owned`, or `· owned ?` when undecided. `get versions` and `get releases` rows carry your count from Discogs, so there it costs nothing; `search` and `get inventory` read the whole collection once per command, cached an hour. `have N` on a row is everyone's count; `owned` is yours.
- **Do I own these few releases, and in which folder?** `get owned @r... [@r...]`, one request per ref. For a page of rows, use `--owned`.
- **Do I own any pressing of this master?** `get owned @m...`: `@m3719 owned ×3 in 2 releases`, then each release owned with its folders, or `@m3719 not owned`. It reads your whole collection once per command (shared by every master ref), whatever the number of versions, where `get versions @m... --owned` costs a request per 100 versions.
- **What is in my collection?** `get collection`, one row per copy, 5 a page (`--limit` 1-100, `--page`). `--sort label|artist|title|catno|format|rating|added|year` and `--desc` go to Discogs (newest additions: `--sort added --desc`). The filters `--format`, `--genre`, `--style`, `--year`, `--label`, `--artist` and `--title` keep records matching a pattern (a whole value, any case; `*` is any run of characters; `\*` a literal `*`; single-quote it: `--artist '\*NSYNC'`) and make it a client-side scan of 100 records a request: pass `--max-requests N` and paste the printed `Continue scan:` for more. `--folder NAME` (a name as rows print it, any case) lists one folder: alone it pages server-side, with a filter it scans that folder only. `--fields` adds each copy's notes and custom fields by name to its row (`· Media Condition: VG+ · Notes: Shelf A3`; JSON `fields`), for condition or shelf questions: 1 more request, not with `--json --full`.
- **How many records do I own per label, year, decade, format, genre, style or artist?** `get collection --count-by KEY`: one `value: count` line per value, largest first (`--top N` for the N largest), from one read of the whole collection (1 request per 100 records, the pages `--owned` caches). The filters and `--folder` narrow what is counted. A record counts once per distinct value, so the counts can add up to more than the records.
- **What do I own more than once?** `get collection --duplicates release` (two copies of one pressing) or `--duplicates master` (any two pressings of one album, as `@m3719 owned ×3 in 2 releases` and a row per release): most copies first, each row with `owned ×N` and its copies' folders. Same read as `--count-by`, plus the folder list. The filters choose the records checked (`--format Vinyl --duplicates master`), `--folder` checks one folder. A stopped read (`--max-requests`, a 429) ends in `Copies and groups may be missing: ...`.
- **What should I listen to? Pick something at random.** `get collection --random` (one record) or `--random 3`: rows drawn uniformly, one per copy, in collection order. Unfiltered it reads only page 1 and the pages of its picks (3 or 4 requests cold for one); with a filter (`--random --genre Jazz --format Vinyl`) it reads the whole collection, then draws from the matches (`--max-requests` there draws from the part read). A 429 on an unfiltered draw is an error.
- **What is my collection worth?** `get collection --value`: Discogs' estimate of the whole collection (min, median, max) in your profile's currency. It takes no other flag.
- **What is on my lists?** `get lists` gives each list's id; `get list <id>` its items, with refs to pass to `get`. `get list` needs no credentials for a public list.

The output of each, line by line and in JSON, is in [references/commands.md](references/commands.md) (`agent-discogs skills get collection --full`).

## Common Patterns

| Goal | Commands |
|------|----------|
| Which of my records are ...? | `get collection --style Techno --format 12\" --max-requests 20` (filters: `--format`, `--genre`, `--style`, `--year`, `--label`, `--artist`, `--title`; whole value, any case, `*` for any run; 1 request per 100 records scanned, paste the printed `Continue scan:` for more). By artist: `get collection --artist 'Miles*' --max-requests 20`. What is in my folder X? `get collection --folder X` (alone: server-side pages, 3 requests cold). What did I add lately? `get collection --sort added --desc`. Needs a token or OAuth |
| How many records do I own per label / decade / genre? | `get collection --count-by label` (keys: `label`, `year`, `decade`, `format`, `genre`, `style`, `artist`; `--top 10` for the largest; add a filter to count a subset: `--style Techno --count-by label`). Reads the whole collection: 1 request + 1 per 100 records cold, free for an hour after, `--max-requests N` gives partial counts flagged in the header. Needs a token or OAuth |
| Do I own anything twice? | `get collection --duplicates release` (same pressing) or `--duplicates master` (any pressings of one album; records with no master are counted apart, not checked). Reads the whole collection: 2 requests + 1 per 100 records cold. Needs a token or OAuth |
| What should I listen to? | `get collection --random` (one record; `--random 3` for three; add a filter to draw from a subset: `--random --style Techno`). Unfiltered: 3 + 1 per other page holding a pick cold; filtered: the whole collection. Needs a token or OAuth |
| What is my collection worth? | `get collection --value` (min, median and max, in your profile's currency; 3 requests). Needs a token or OAuth |
| Which of these do I own? | Add `--owned` to the listing: `search release --artist "Nine Inch Nails" --year 1994 --release-type unofficial --limit 100 --owned` (also `get versions @m... --owned`, `get releases @a... --owned`, `get releases @l... --owned`); for a few refs, `get owned @r... @r...`. Needs a token or OAuth |
| Do I own any pressing of X? | `search master "<title>"` → `get owned @m...` (lists the releases you own). Needs a token or OAuth |
| What is on my list X? | `get lists` (find X's id) → `get list <id>` → follow an item's `@r...`/`@m...`/`@a...`/`@l...` into `get`. Needs a token or OAuth for `get lists` |

## Budget

What these commands cost; the footer, `--cached` and the per-minute budget are in `agent-discogs skills get core`, section Budget.

| Command | Requests |
|---|---|
| `--owned` on `get versions`, `get releases` | 0: each row carries your count |
| `--owned` on `search`, `get inventory` | on top of the listing: 1 for the username + 1 per 100 collection records, then free for an hour; nothing when the page has no release row |
| `get owned @r...` | 1 for the username + 1 per ref + 1 for the folder list when any ref is owned |
| `get owned @m...` | 1 for the username + 1 per 100 collection records (once per command, for every master ref) + 1 for the folder list when any copy is found (none with `--json --full`); then free for an hour |
| `get collection` | 1 for the username + 1 for the folder list (none with `--json --full`, unless `--folder`) + 1 for the field names with `--fields` + 1 per page of up to 100 records; with a filter, 1 per 100 records scanned (of `--folder` only, when given; else the same pages `--owned` caches, without `--sort`) |
| `get collection --value` | 3: the username, the profile (its currency) and the value |
| `get collection --count-by` | 1 for the username + 1 per 100 records (the pages `--owned` caches; no folder list); with `--folder`, 1 more for the folder list then that folder's pages |
| `get collection --duplicates` | 2 (the username, the folder list) + 1 per 100 records; `--fields` 1 more; `--json --full` without `--folder` reads no folder list |
| `get collection --random [N]` | Unfiltered: 2 (the username, the folder list) + page 1 + 1 per other page holding a pick; filtered: as `--duplicates` |
| `get lists` | 1 for the username + 1 per page of up to 100 lists (`--limit`) |
| `get list <id>...` | 1 per list, whatever its length |

## Tasks

Recipes for requests about the user's own collection, by stable ID. The legend and the other skills' tasks are under Tasks in `agent-discogs skills get core`. Each needs a token or OAuth.

### A4 Which pressing of this album do I own, and what is it worth?

Status: works (which pressing); works, costly (what it is worth)

```bash
agent-discogs get owned @m3719
agent-discogs price @r847868 @r352665
```

`get owned` prints `@m3719 owned ×3 in 2 releases`, then each release you own with its folders; price those. `price` needs seller settings on the account; without them, `get stats @r...` gives the count for sale and the lowest price (2 requests per ref).

Cost: 1 (the username) + 1 per 100 collection records + 1 (the folder list), then 3 per release priced.

Would need: a value per record (Discogs API limit).

### B1 Which of my records are jazz, techno, vinyl...?

Status: works

```bash
agent-discogs get collection --genre Jazz --format Vinyl --limit 100
```

Also `--style`, `--year`, `--label`, `--artist`, `--title`: whole value, any case, `*` for any run of characters. On a big collection add `--max-requests N` and paste the printed `Continue scan:`.

Cost: 2 (the username and the folder list) + 1 per 100 records scanned.

### B2 How many records do I own per label, decade, format or genre?

Status: works

```bash
agent-discogs get collection --count-by decade
```

Keys: `label`, `year`, `decade`, `format`, `genre`, `style`, `artist`; `--top N` for the largest, a filter to count a subset.

Cost: 1 (the username) + 1 per 100 records; the other keys are then free for an hour.

### B3 Pick a random record, optionally from a folder or a genre

Status: works (from the collection or a folder); works, costly (from a genre or another filter)

```bash
agent-discogs get collection --random
agent-discogs get collection --random --folder "<folder>"
agent-discogs get collection --random --genre Jazz
```

`--random 3` draws three. Unfiltered, it reads page 1 and the pages holding a pick; a filter reads the whole collection, then draws from the matches.

Cost: unfiltered: 2 (the username and the folder list) + the first page + 1 per other page holding a pick; filtered: 2 + 1 per 100 records.

Would need: server-side filters on collection pages (Discogs API limit).

### B4 Which records do I own more than once?

Status: works

```bash
agent-discogs get collection --duplicates release
agent-discogs get collection --duplicates master
```

`release`: copies of one pressing; `master`: any pressings of one album, as `@m3719 owned ×3 in 2 releases` and a row per release.

Cost: 2 (the username and the folder list) + 1 per 100 records; the second view is then free.

### B5 Do I own any pressing of this album?

Status: works

```bash
agent-discogs search master "The Downward Spiral" --artist "Nine Inch Nails"
agent-discogs get owned @m3719
```

`@m3719 owned ×3 in 2 releases`, each release with its folders, or `@m3719 not owned`, however many versions the album has.

Cost: 1 (the search), then 1 (the username) + 1 per 100 collection records + 1 (the folder list, when a copy is found).

### B6 Export my collection with genres, styles and year

Status: works, costly

```bash
agent-discogs get collection --json --limit 100
```

Read every page (`--page 2`, ... up to `pagination.pages`). Each row carries `ref`, `title`, `artists`, `year`, `label`, `catno`, `format`, `genres`, `styles`, `master`, `folder` and `added`; `--fields` adds your notes and custom fields.

Cost: 2 (the username and the folder list) + 1 per 100 copies.

Would need: more than 100 copies a request (Discogs API limit).

### B7 Back up my collection and wantlist

Status: works, costly

```bash
agent-discogs get collection --json --full --limit 100
agent-discogs get wantlist --json --full --limit 100
```

Read every page of each (`--page 2`, ... up to `pagination.pages`). `--full` keeps every field Discogs sends (`instance_id`, `folder_id`, `notes`, `date_added`). Restoring is by hand on discogs.com: agent-discogs sends no write request.

Cost: 1 (the username) + 1 per 100 copies + 1 per 100 wants.

Would need: more than 100 items a request (Discogs API limit).

### B8 Where is my copy shelved?

Status: works

```bash
agent-discogs get collection --title "The Downward Spiral" --fields
```

Each copy prints its folder, then your notes and custom fields by name (`Shelf: A3`). `get owned @r...` gives the folders alone.

Cost: 3 (the username, the folder list and the field names) + 1 per 100 records scanned.

### B9 What did I add recently?

Status: works

```bash
agent-discogs get collection --sort added --desc
```

Cost: 3 (the username, the folder list and the page).

### B10 Add, move or remove records

Status: not possible

Why not: agent-discogs only reads, by design; it sends no write request. The user does it on discogs.com, then `get owned @r...` or `get collection` reads the result.

Would need: nothing planned: read-only by design.

### B11 My collection A-Z by artist, ignoring "The"

Status: works

```bash
agent-discogs get collection --sort artist --limit 100
```

Discogs sorts on its side and ignores a leading "The" (seen on 2026-10-15); paste the printed `Next page:` for more.

Cost: 3 (the username, the folder list and the page) + 1 per further page.

### B12 Mirror my collection into another tool

Status: works, costly

```bash
agent-discogs get collection --json --full --limit 100
```

Read every page (`--page 2`, ...). Key each copy on `instance_id`: `id` is the release, shared by every copy of it. A rerun within the hour reads the cache; `--fresh` reads Discogs again.

Cost: 1 (the username) + 1 per 100 copies, every sync.

Would need: a changed-since query (Discogs API limit).

### C1 What is my collection worth?

Status: works

```bash
agent-discogs get collection --value
```

Discogs' minimum, median and maximum for the whole collection, in your profile's currency.

Cost: 3 (the username, the profile and the value).

### C2 What is one folder or subset worth?

Status: not possible (Discogs' value of a folder); works, costly (an estimate of your own)

Why not: Discogs values the whole collection only. For an estimate, list the folder's releases and add up their prices:

```bash
agent-discogs get collection --folder "<folder>" --json --limit 100
agent-discogs get stats @r847868 @r352665
```

`get stats` gives each release's count for sale and lowest price (an asking price, not a value); with seller settings, `price @r...` gives the suggestion per condition (3 requests per release).

Cost: 3 (the username, the folder list and the page) + 2 per release (up to 10 refs a command).

Would need: a value per folder (Discogs API limit).

### C3 Which of my records are worth over $50?

Status: works, costly

```bash
agent-discogs get collection --json --limit 100
agent-discogs get stats @r847868 @r352665 --currency USD
```

List your releases, then keep those whose lowest price is over 50: the only per-release number without seller settings. With them, `price @r...` gives the suggestion per condition (3 requests per release).

Cost: 2 + 1 per 100 copies, then 2 per release (up to 10 refs a command): 500 records take about 1,000 requests, 17 minutes of budget.

Would need: a value per record, or market numbers for several releases a request (Discogs API limit).

### D6 Which records of a label's catalogue am I missing?

Status: works, costly

```bash
agent-discogs get releases @l647 --owned --limit 100
```

Each row ends `· owned`, `· owned ×2` or `· not owned`, from the count on the row; paste the printed `Next page:` for more.

Cost: 1 (the label's name; none with `--json`) + 1 per 100 catalogue rows.

Would need: a catalogue filter on ownership (Discogs API limit).

### F5 How many different artists are in my collection?

Status: works

```bash
agent-discogs get collection --count-by artist --top 10 --json
```

`distinct` is the number of different artists; `counts` holds the 10 with the most records.

Cost: 1 (the username) + 1 per 100 records.

### G4 What should I look for next, from what I own?

Status: works, costly

Find the labels (or artists, styles) you own most, then list a catalogue with ownership marks and suggest its `· not owned` rows:

```bash
agent-discogs get collection --count-by label --top 5
agent-discogs get releases @l647 --owned --limit 100
```

Cost: 1 (the username) + 1 per 100 records, then 1 (the label's name; none with `--json`) + 1 per 100 catalogue rows.

Would need: recommendations (Discogs API limit).
