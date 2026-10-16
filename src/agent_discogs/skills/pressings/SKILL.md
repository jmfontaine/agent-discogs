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

- **Versions** — `get versions @m...` lists one row per pressing; `--sort released` puts the earliest first, and `--country`, `--format`, `--label` and `--year` narrow it. Unlike `search`, its `--release-type` defaults to `all`, so unofficial versions are listed too: pass `--release-type official` to drop them. `--owned` and `--wanted` mark each row with the user's own copies and wantlist at no extra request (`· owned ×2 · not wanted`; needs a token or OAuth): see `agent-discogs skills get collection` and `skills get marketplace`.
- **Catalog number and barcode** — `search release --catno ...` and `search release --barcode ...` are the most precise searches. Several rows with the same catalog number are variants of one pressing: compare them with `get release` and `get identifiers`.
- **Identifiers** — `get identifiers @r...` prints barcodes, matrix/runout etchings, label codes and rights societies. The matrix and runout are what usually tells apart pressings that share a catalog number.
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
| Do I own any pressing of it? | `get owned @m...`: only the releases you own, from one read of your collection, however many versions (needs a token or OAuth; `agent-discogs skills get collection`) |
| Get original pressing | `search master "<title>"` → `get versions @m... --sort released` → `get release @r...` |
| Pick among look-alike rows | Compare catalog number and `have N` in the row itself; `have` is how many Discogs users own it (popularity, not yours, not identification). Same catno on several rows means variants: see `get release @r...` |
| Identify by catalog number | `search release --catno "INT-92346"` → `get release @r...` |
| Find by barcode | `search release --barcode "606949235024"` |
| Compare several pressings cheaply | `get release @r... @r... @r... -c` in one command; `tracks @r...` only when the tracklist matters |
| Identify the disc in hand (same catno, several pressings) | `get identifiers @r... @r...` and compare Matrix / Runout |
| Bootlegs only (not counterfeits) | `search release --artist "<name>" --release-type unofficial`, then drop rows whose `→ @m...` master has official versions (`get versions @m... --release-type official --limit 5 --json`: any row whose format does not list "Partially Unofficial"; if every row shown lists it, continue with `--after <next_cursor>`). Keep the rest (no master, or a master with no official versions), but check each against the traps in [Unofficial Releases](references/discogs-domain.md#unofficial-releases-bootlegs-counterfeits-pirates): a counterfeit or pirate can lack a master, and a compilation of officially released tracks is a pirate. Not a flag: a judgement call |

`agent-discogs skills get pressings --full` adds the pressings guide and the Discogs data on formats, unofficial releases and identifiers.
