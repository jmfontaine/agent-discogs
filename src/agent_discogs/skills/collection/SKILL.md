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
