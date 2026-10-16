---
name: marketplace
description: Discogs Marketplace data and the user's wantlist. Load it when a task asks what a record is worth or sells for, which pressing is for sale and for how little, how many copies are for sale and how many people have or want several pressings, whether anything on the user's wantlist is for sale and at what price, which rows of a listing the user wants, or what a seller has - price, get stats, get versions --market, get wantlist, --for-sale, --wanted, get inventory and its filters, --in-wantlist.
allowed-tools: Bash(agent-discogs:*)
---

# agent-discogs: the Marketplace

This skill assumes you have read `agent-discogs skills get core` (setup, refs, budget, errors). Amounts never carry a symbol; why the `Market:` line of `get release` and `get master` names no currency is under Output Conventions there.

## What each command answers

- **What is this release worth?** `price @r...` (or `get price @r...`): Discogs' suggested price per condition, and the copies for sale now with the lowest price, each amount with its ISO code. Needs a token or OAuth *and* seller settings on that account.
- **Which of these pressings is cheapest, and which do people own or want?** `get stats @r... @r...` (1-10 release refs): one line per release, `have N · want N · rating 4.43/5 (602 votes) · for sale: 45 from EUR 0.80`, the price with the currency Discogs priced it in (`--currency CODE` for another). 2 requests per ref; no seller settings or user needed.
- **Which pressing of an album is for sale, and for how little?** `get versions @m... --market` ends each version row with `· for sale: 6 from EUR 427.50` (`· for sale: 0` for none), after any `--owned`/`--wanted` mark; `--currency CODE` prices every row in it. 1 request per row on top of the listing, so narrow with `--format`/`--country` and `--limit`, and pass `--max-requests N`: the rows past the cap stay unchecked, with a `Market:` note. No user needed.
- **What is on my wantlist?** `get wantlist`, 5 wants a page (`--limit` 1-100, `--page`). Needs a token or OAuth.
- **Which of these rows are on my wantlist?** Add `--wanted` to a listing (`search`, `get versions`, `get releases @a...|@l...`): each release row ends `· wanted`, `· not wanted` or `· wanted ?` (undecided), after `owned` with `--owned`. `get versions` and `get releases` rows carry the answer (no extra request); `search` reads your whole wantlist once (1 request per 100 wants). Needs a token or OAuth. Not on `get inventory`: use `--in-wantlist` there.
- **Is anything I want for sale, and under what price?** `get wantlist --for-sale`, optionally `--max-price X --currency EUR`: the wants with copies for sale now, each with the count and the lowest price. It costs 1 request per want checked: always pass `--max-requests N` and paste the printed `Continue scan:` for the rest.
- **What does this seller have?** `get inventory <username>`: one seller's listings, with grades, price and origin; no credentials needed. `--condition` and `--sleeve` (minimum grades), `--ships-from`, `--max-price X --currency` (in the seller's currency) and `--in-wantlist` (needs a token or OAuth) keep the matching listings, scanning 100 listings a request: pass `--max-requests N`.

The output of each, line by line and in JSON, is in [references/commands.md](references/commands.md) (`agent-discogs skills get marketplace --full`); the grades, in [references/discogs-domain.md](references/discogs-domain.md).

**"Price data requires seller settings"** (`seller_settings_required`) from `price`: not a bad ref. The release exists, but Discogs only serves price suggestions to accounts with seller settings filled out. Nothing to retry: use `get stats @r...` for the count for sale and the lowest price, with its currency, instead.

## What the API cannot filter or find

**What `get wantlist --for-sale` cannot filter:** condition, sleeve condition, seller location and shipping. The Discogs API has no endpoint listing a release's copies for sale, only per-release stats (the count and one lowest price across every condition and location), so for those, send the user to the release's Marketplace page on the Discogs website. `get inventory` filters on condition, sleeve and ships-from, but only within one seller you already know.

**No command finds every seller of one release** (the API has no such endpoint): `get inventory` browses a seller you already know, and `price @r...`, `get stats @r...`, `get versions --market` and `get wantlist --for-sale` give a count and a lowest price only. Nothing blocks or rates sellers, handles carts, offers or purchases, or edits your own listings, and the shipping cost is not shown (Discogs sends none on listings).

## Common Patterns

| Goal | Commands |
|------|----------|
| Check price | `search release "<title>"` → `price @r...` |
| Compare the market of several pressings | `get versions @m...` → `get stats @r... @r... --currency EUR` (up to 10 refs, 2 requests each) |
| Cheapest pressing of an album for sale | `get versions @m... --format Vinyl --market --currency EUR --limit 20 --max-requests 30` (1 request per row on top of the page); compare the `for sale:` tails yourself: Discogs cannot sort versions by price |
| Is anything on my wantlist for sale under X? | `get wantlist --for-sale --max-price X --currency EUR --max-requests 60` (1 request per want checked; paste the printed `Continue scan:` for the rest). Count and lowest price only: condition, sleeve condition, seller location and shipping cannot be filtered (the API lists no copies), so point the user at the release's Marketplace page on discogs.com for those. Needs a token or OAuth |
| What does seller X have from my wantlist? | `get inventory <seller> --in-wantlist --condition VG+ --max-requests 60` (1 request per 100 wants, then 1 per 100 listings scanned; paste the printed `Continue scan:` for the rest). Other filters: `--sleeve`, `--ships-from`, `--max-price X --currency EUR`; for the seller's VG+-or-better stock, drop `--in-wantlist` (no credentials needed), and for everything, `get inventory <seller>` (server-side pages). `--in-wantlist` needs a token or OAuth |
| What is on my wantlist? | `get wantlist` (5 a page; `--limit 100`, `--page N`) → `get release @r...` |
| Which of these pressings do I want? | `get versions @m... --wanted` (no extra request), or `search release ... --wanted` (reads the wantlist once) |

## Budget

What these commands cost; the footer, `--cached` and the per-minute budget are in `agent-discogs skills get core`, section Budget.

| Command | Requests |
|---|---|
| `price` | 3 per ref (`--json --full`: 2) |
| `get stats` | 2 per ref (the release, then its Marketplace stats); a release that does not exist costs 1. Another `--currency` is another stats request |
| `get versions --market` | on top of the listing (1 with `--json`, 2 as text): 1 per row shown, so `--limit 100` is 101 requests with `--json`. Rows read within the hour are free on a rerun; another `--currency` is another request per row. Pass `--max-requests` |
| `get wantlist` | 1 for the username + 1 per page of up to 100 wants (`--limit`) |
| `get wantlist --for-sale` | on top of that: 1 per want checked (its Marketplace stats), wants read 100 per request; ~1 request per want when few are for sale. Pass `--max-requests` |
| `get inventory <username>` | 1 per page of up to 100 listings (`--limit`); no username request |
| `get inventory` with `--condition`, `--sleeve`, `--ships-from` or `--max-price` | 1 per 100 listings scanned (50,000 listings = 500 requests). Pass `--max-requests` |
| `get inventory --in-wantlist` | on top of that: 1 for the username + 1 per 100 wants, read before any listing; a cap or 429 that stops it scans nothing |
| `--wanted` on `get versions`, `get releases` | 0: each row carries your want count |
| `--wanted` on `search` | on top of the listing: 1 for the username (shared with `--owned`) + 1 per 100 wants, then free for an hour; nothing when the page has no release row |

`get wantlist --for-sale` is the expensive one: 1 request per want, so cap it (`--max-requests 60` is a minute of budget) and continue with the printed `Continue scan:`. A `get inventory` filter over a big seller is nearly as bad (500 requests for 50,000 listings): cap it the same way.

## Tasks

Recipes for Marketplace and wantlist requests, by stable ID. The legend and the other skills' tasks are under Tasks in `agent-discogs skills get core`.

### C4 What is a fair price for a VG+ copy?

Status: works

```bash
agent-discogs price @r847868
```

Read the `Very Good Plus (VG+):` line. Needs a token or OAuth and seller settings on that account; without them, `seller_settings_required`, and `get stats @r...` gives only the count for sale and the lowest price.

Cost: 3.

### C5 How many copies are for sale, and from what price?

Status: works

```bash
agent-discogs get stats @r847868
```

`for sale: 45 from EUR 0.80` (`--currency CODE` for another currency); no seller settings or user needed.

Cost: 2 per release (up to 10 refs a command).

### C6 Which pressing of an album is cheapest right now?

Status: works, costly

```bash
agent-discogs get versions @m3719 --format Vinyl --market --currency EUR --limit 20 --max-requests 30
```

Compare the `for sale:` tails yourself; narrow with `--format` and `--country` first.

Cost: 2 (the master's title and the page; 1 with `--json`) + 1 per row.

Would need: a price sort or prices on version rows (Discogs API limit).

### C7 What has this sold for?

Status: not possible

Why not: the API has no sales history for a release. `price @r...` gives the suggested price per condition, `get stats @r...` the lowest asking price; the sales history is on the release's discogs.com page (`url` in `get release @r... --json`).

Would need: a sales history endpoint (Discogs API limit).

### C8 How many people want this against how many have it, for one release or my collection?

Status: works (one release); works, costly (my collection)

```bash
agent-discogs get stats @r847868
agent-discogs get collection --json --limit 100
agent-discogs get stats @r847868 @r352665
```

`have N · want N` (also on every search, versions and releases row, at no extra cost). For the collection, list its releases, then pass them to `get stats` 10 at a time.

Cost: 2 per release; the collection list adds 2 (the username and the folder list) + 1 per 100 copies.

Would need: community counts on collection rows (Discogs API limit).

### D1 What is on my wantlist, filtered by format, year or label?

Status: works (unfiltered); works, costly (filtered)

```bash
agent-discogs get wantlist --limit 100
agent-discogs get wantlist --json --full --limit 100
```

`get wantlist` has no filter: read every page (`--page 2`, ... up to `pagination.pages`) with `--json`, whose rows have `year`, `label`, `catno` and `format`, and keep the matches yourself; `--json --full` rows also carry `basic_information.genres` and `styles`.

Cost: 1 (the username) + 1 per 100 wants.

Would need: filters on the wantlist (Discogs API limit).

### D2 Is anything on my wantlist under a price? Alert me, by condition and country

Status: works, costly (a price check now); not possible (alerts, condition, country)

```bash
agent-discogs get wantlist --for-sale --max-price 25 --currency EUR --max-requests 60
```

Keeps the wants with copies for sale whose lowest price is at most 25 EUR; paste the printed `Next page:` or `Continue scan:` for more.

Why not alerts, condition, country: the API has no alerts, and for a release only a count for sale and one lowest price over every condition and country. Rerun the check later; for conditions and countries, send the user to the release's Marketplace page.

Cost: 1 (the username) + 1 per 100 wants read + 1 per want checked.

Would need: alerts and a list of a release's copies (Discogs API limit).

### D3 Who sells my wants, and in what condition?

Status: not possible

Why not: no endpoint lists a release's copies or sellers. `get wantlist --for-sale` gives a count and one lowest price per want, and `get inventory <seller> --in-wantlist` checks one seller you name (D4).

Would need: a list of a release's copies (Discogs API limit).

### D4 Which of my wants does one seller have?

Status: works, costly (a seller you name); not possible (finding such sellers)

```bash
agent-discogs get inventory <seller> --in-wantlist --max-requests 60
```

Reads your whole wantlist, then the seller's listings, 100 a request; add `--condition VG+` or `--ships-from France` to narrow.

Why not finding sellers: no endpoint lists or searches sellers, so none finds those holding several of your wants.

Cost: 1 (the username) + 1 per 100 wants + 1 per 100 listings scanned.

Would need: a seller search (Discogs API limit).

### D5 Is this barcode on my wantlist?

Status: works

```bash
agent-discogs search release --barcode "606949235024" --release-type all --wanted
```

Each row ends `· wanted`, `· not wanted` or `· wanted ?` (undecided).

Cost: 1 (the search) + 1 (the username) + 1 per 100 wants.

### E1 Reprice my inventory from price suggestions

Status: works, costly (reading the suggestions, with seller settings); not possible (changing the prices)

```bash
agent-discogs get inventory <your username> --json --limit 100
agent-discogs price @r847868 @r352665
```

Compare each listing's `price` and `condition` with the suggestion for that condition.

Why not changing the prices: agent-discogs sends no write request, by design; the user changes them on discogs.com.

Cost: 1 per 100 listings + 3 per release priced.

Would need: price suggestions for several releases a request (Discogs API limit); changing prices, nothing: read-only by design.

### E2 Export my inventory with images

Status: works (a thumbnail per listing); works, costly (full-size images)

```bash
agent-discogs get inventory <your username> --json --full --limit 100
agent-discogs get release @r847868 --json --full
```

Each `--full` listing carries `release.thumbnail`; full-size images are the release's `images`, one `get release --json --full` per release.

Cost: 1 per 100 listings; full-size images 1 per release.

Would need: full-size images on listings (Discogs API limit).

### E3 Which orders do I have to ship?

Status: not possible

Why not: not implemented: agent-discogs has no orders command, though the API serves a seller's own orders.

Would need: an orders command; none is planned.

### E5 Are there duplicate listings in my inventory?

Status: works, costly

```bash
agent-discogs get inventory <your username> --json --limit 100
```

Read every page (`--page 2`, ...) and group the rows by `ref`: two listings of one release in one `condition` are likely duplicates.

Cost: 1 per 100 listings.

Would need: more than 100 listings a request (Discogs API limit).

### H2 How have prices changed over the years?

Status: not possible

Why not: the API has no price or sales history; `price` and `get stats` give today's numbers only.

Would need: a price history endpoint (Discogs API limit).
