---
name: pressings
description: Telling Discogs releases apart. Load it when a task asks which pressing, version or edition a record is, which one is the original, how two pressings differ, or whether a release is a bootleg, counterfeit or pirate - versions and --release-type, identifiers (matrix, barcode), credits, catalog numbers.
allowed-tools: Bash(agent-discogs:*)
---

# agent-discogs: pressings

This skill assumes you have read `agent-discogs skills get core` (setup, refs, budget, errors). It names the commands for pressing research; their flags and output are in `agent-discogs skills get core --full`.

## Masters, releases, versions

A master groups every version of one album; each version is a release, one product with its own label, catalog number, country, format and year. To list the pressings of an album, find its master (a release row's `→ @m...`, or `search master`) and run `get versions @m...`; to pin one pressing, search releases with filters. [references/pressings-guide.md](references/pressings-guide.md) has the model and the rules that decide what shares a master.

## Telling releases apart

- **Versions** — `get versions @m...` lists one row per pressing, with Discogs' community counts (`have N · want N`); `--sort released` puts the earliest first, and `--country`, `--format`, `--label` and `--year` narrow it. Unlike `search`, its `--release-type` defaults to `all`, so unofficial versions are listed too: pass `--release-type official` to drop them. `--owned` and `--wanted` mark each row with the user's own copies and wantlist at no extra request (`· owned ×2 · not wanted`; needs a token or OAuth): see `agent-discogs skills get collection` and `skills get marketplace`. `--market` ends each row with its Marketplace numbers (`· for sale: 6 from EUR 427.50`, `· for sale: 0`; `--currency CODE` for another currency), at 1 request per row: keep `--limit` small or pass `--max-requests` (details: `agent-discogs skills get marketplace`).
- **Catalog number and barcode** — `search release --catno ...` and `search release --barcode ...` are the most precise searches. Several rows with the same catalog number are variants of one pressing: compare them with `get release` and `get identifiers`.
- **Identifiers** — `get identifiers @r...` prints barcodes, matrix/runout etchings, label codes and rights societies. The matrix and runout are what usually tells apart pressings that share a catalog number. To find a pressing from its runout etching, see "Find by barcode or runout" below.
- **Several at once** — `get release @r... @r... -c` compares formats, labels, countries and dates without the tracklists; add `-v` for notes, credits and identifiers.
- **Credits** — `get credits @r...` groups the people by role (Producer, Engineer, Mastered By, ...), with the tracks each worked on.
- **What makes a release unique**, and how formats, descriptions and identifiers are written on Discogs: [references/discogs-domain.md](references/discogs-domain.md).

## Unofficial releases

Bootlegs, counterfeits and pirate copies all carry the format description "Unofficial Release", so `--release-type unofficial` returns the three kinds together and no field tells them apart. Deciding which kind a release is takes the master check and the traps in [Unofficial Releases](references/discogs-domain.md#unofficial-releases-bootlegs-counterfeits-pirates).

## Common Patterns

| Goal | Commands |
|------|----------|
| Compare pressings | `search master "<title>"` → `get versions @m...` |
| Release → all its pressings | Copy `→ @m...` from any release row → `get versions @m...` |
| Which pressings do I own or want? | `get versions @m... --owned --wanted` (each row carries the user's counts; no extra request) |
| Which pressing is for sale, and cheapest? | `get versions @m... --format Vinyl --market --currency EUR --limit 20` (1 request per row on top of the page; no user needed); compare the `for sale:` tails. Discogs cannot sort versions by price |
| Do I own any pressing of it? | `get owned @m...`: only the releases you own, from one read of your collection, however many versions (needs a token or OAuth; `agent-discogs skills get collection`) |
| Get original pressing | `search master "<title>"` → `get versions @m... --sort released` → `get release @r...`. From a release, `get release @r... --master-year` first gives the work's year (`Master: @m3719 (1994)`, 1 more request) |
| Pick among look-alike rows | Compare catalog number, `have N` and `want N` in the row itself; `have` is how many Discogs users own it and `want` how many want it (the community's, not yours, not identification). Many wants against few haves is a scarce pressing; few wants against many haves, a common one. Same catno on several rows means variants: see `get release @r...` |
| Identify by catalog number | `search release --catno "INT-92346"` → `get release @r...` |
| Find by barcode or runout | `search release --barcode "606949235024"` (digits, no spaces); `search release --artist "Human League" --barcode "A3 TOWNHOUSE" --country UK --format LP --release-type all` (runout etching; try `TOWN HOUSE` too) → `get identifiers @r...` |
| Compare several pressings cheaply | `get release @r... @r... @r... -c` in one command; `tracks @r...` only when the tracklist matters |
| Identify the disc in hand (same catno, several pressings) | `get identifiers @r... @r...` and compare Matrix / Runout |
| Bootlegs only (not counterfeits) | `search release --artist "<name>" --release-type unofficial`, then drop rows whose `→ @m...` master has official versions (`get versions @m... --release-type official --limit 5 --json`: any row whose format does not list "Partially Unofficial"; if every row shown lists it, continue with `--after <next_cursor>`). Keep the rest (no master, or a master with no official versions), but check each against the traps in [Unofficial Releases](references/discogs-domain.md#unofficial-releases-bootlegs-counterfeits-pirates): a counterfeit or pirate can lack a master, and a compilation of officially released tracks is a pirate. Not a flag: a judgement call |

`agent-discogs skills get pressings --full` adds the pressings guide and the Discogs data on formats, unofficial releases and identifiers.

## Tasks

Recipes for requests about pressings, by stable ID. The legend and the other skills' tasks are under Tasks in `agent-discogs skills get core`.

### A1 Which pressing is this barcode, and do I own or want it?

Status: works

Search the barcode with every release type (a counterfeit copies it), marking each row with your copies and your wantlist (needs a token or OAuth):

```bash
agent-discogs search release --barcode "606949235024" --release-type all --owned --wanted
```

Each row ends `· owned ×2 · not wanted` (`owned ?` or `wanted ?` when undecided) and names its album as `→ @m...`. Several rows: tell them apart with A2.

Cost: 1 (the search) + 1 (the username) + 1 per 100 collection records + 1 per 100 wants; free for an hour after.

### A2 Which pressings carry this runout etching?

Status: works, costly

Search the etching as identifier text, narrowed by artist, then read each candidate's Matrix / Runout lines: the search matches loosely.

```bash
agent-discogs search release --artist "Nine Inch Nails" --barcode "<etching>" --release-type all
agent-discogs get identifiers @r847868 @r352665
```

Type the etching as Discogs writes it, and try each spelling (`TOWN HOUSE`, `TOWNHOUSE`) when one finds nothing.

Cost: 1 (the search) + 1 per candidate checked (up to 10 refs a command).

Would need: an exact identifier search (Discogs API limit).

### A3 Which is the original or the best-sounding pressing, with its mastering credits and runouts?

Status: works, costly

List the versions earliest first, then read the candidates' notes, credits (Mastered By, Lacquer Cut By) and runouts side by side:

```bash
agent-discogs get versions @m3719 --sort released --limit 20
agent-discogs get release @r847868 @r352665 -c -v
```

The best sound is a judgement: Discogs holds no ratings of it, only the credits, identifiers and notes to base one on.

Cost: 2 (the master's title and the versions page; 1 with `--json`) + 1 per release compared.

Would need: credits and identifiers on version rows (Discogs API limit).

### A5 What is catalog number X on label Y?

Status: works

```bash
agent-discogs search release --catno "INT-92346" --label "Nothing Records"
```

Several rows with one catalog number are variants of one pressing: compare them as in A3.

Cost: 1 per 100 rows scanned (the default `--release-type official` filters client-side): 1 for a catalog number.

### A6 What are the identifiers and notes of this release?

Status: works

```bash
agent-discogs get release @r847868 -v
```

`-v` adds `Notes:`, the credits and the identifiers (barcodes, matrix/runout, label codes); `get identifiers @r...` prints the identifiers alone.

Cost: 1.

### E4 Resolve a stack of barcodes to releases

Status: works, costly

One search per barcode, digits only:

```bash
agent-discogs search release --barcode "606949235024" --release-type all
```

A barcode can match several pressings (reissues reuse it, counterfeits copy it): keep every row and confirm with A2 when the runouts are at hand. After an interruption, rerun the batch under `--cached` first: the barcodes already searched cost nothing.

Cost: 1 per barcode, so 60 barcodes a minute.

Would need: a search taking several barcodes (Discogs API limit).

### F1 Which release matches this folder of audio files?

Status: works, costly

Find the candidates, compare their track counts and running times, then the durations of the closest:

```bash
agent-discogs search release "The Downward Spiral" --artist "Nine Inch Nails" --release-type all
agent-discogs get release @r847868 @r352665 -c
agent-discogs tracks @r847868
```

`-c` prints each release's `Tracks: 14 (65:01)`: match it against the files' count and total time.

Cost: 1 (the search) + 1 per candidate; the tracklist is then cached.

Would need: a lookup by disc ID or durations (Discogs API limit).

### F2 What is the original year of this reissue?

Status: works

```bash
agent-discogs get release @r352665 --master-year
```

`Master: @m3719 (1994)` is the master's year, which Discogs sets to the year the work was first released.

Cost: 2 (the release and its master).
