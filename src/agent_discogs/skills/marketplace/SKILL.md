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
