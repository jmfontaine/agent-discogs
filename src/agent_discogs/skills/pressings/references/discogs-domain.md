# Discogs Domain Knowledge: Pressings

How Discogs describes formats, track positions, unofficial releases and identifiers, and what makes two releases different. Genres, country, artists, labels and dates are in `agent-discogs skills get core --full`; condition grades in the marketplace skill.

## Contents

- [Formats](#formats)
- [Unofficial Releases: Bootlegs, Counterfeits, Pirates](#unofficial-releases-bootlegs-counterfeits-pirates)
- [Identifiers](#identifiers)
- [What Makes Releases Unique](#what-makes-releases-unique)

## Formats

Format describes the sound carrier. Each distinct format of the same recording is a separate release.

### Physical formats

| Format | Notes |
|--------|-------|
| Vinyl | Generic vinyl record. Size specified separately (7", 10", 12"). |
| LP | Long Player. Implies 12", 33 RPM, narrow grooves (up to 30 min/side). |
| 12" | Just the size. Wider grooves, under 15 min/side. No 12" singles before 1976. |
| CD | Pressed disc. Silver/gray or golden data side. Has IFPI SID codes (post-1994). |
| CDr | Burnt disc. Light golden/green/blue data side. No pressing plant codes. |
| Cassette | Includes details like C60, Dolby B in free text. |
| File | Digital. Qty = number of audio files. Bitrate in free text ("320 kbps"). |

**LP vs 12"**: LP means narrow grooves for longer play time. 12" means wide grooves for louder, shorter cuts. When in doubt, check catalog number patterns on the label's other releases.

**CD vs CDr**: Check the inner ring (CD matrix). Pressed CDs have pressing numbers, manufacturer names, and IFPI SID codes. Burnt CDrs have only a type number (often containing "74" or "80").

### Format descriptions

Descriptions qualify a format. Common ones and what they mean:

| Description | Meaning |
|-------------|---------|
| Album | Full-length release. Must be declared on the release or by official sources. |
| EP | Extended play. Must be labeled as such. |
| Single | Must be labeled as such. |
| Maxi-Single | Must be labeled as such. |
| LP (description) | Long-playing record. Does not imply "album" on Discogs. |
| Compilation | Tracks gathered from multiple previously released sources (best-of, themed). |
| Reissue | Content previously released, often on a different format or after a gap (>~18 months). |
| Repress | Re-pressed from the original master disc. Only applies to stamped/pressed formats. |
| Promo | Explicitly marked as promotional (DJ copy, Demonstration, etc.). |
| Test Pressing | Limited run to verify sound quality. Must be marked as such. |
| White Label | Center labels are blank (any color). No mechanically applied print. |
| Limited Edition | Only when "limited" or "limited edition" appears on the release. |
| Numbered | Edition is numbered (e.g., "142 of 500"). |
| Unofficial Release | Made without copyright holder consent. Covers bootlegs, counterfeits, pirates alike; see [Unofficial Releases](#unofficial-releases-bootlegs-counterfeits-pirates) to tell them apart. |

**Reissue vs Repress**: A reissue can cross formats (CD reissue of an LP). A repress must use the original master disc on the same pressed format. Both tags can coexist.

### Multi-format releases

Quantity field shows count per format: "2 x LP" or "1 x LP, 1 x 7"". Box Set and All Media are always combined with other format types.

### Format string structure

A format display string is composed of: `Qty x Format, Description, Description, (Free Text)`.

- **Descriptions** qualify the format: Album, Reissue, Promo, Limited Edition, etc.
- **Free text** carries additional details: color (Red), packaging (Gatefold, Digipak), weight (180g), bitrate (320 kbps), cassette info (C60, Dolby B).

Example: `2xLP, Album, Reissue, (180g, Gatefold)` means 2 vinyl LPs, album-length, reissued, pressed at 180g in a gatefold sleeve.

`get release` prints each entry as `Qty×Format (Description, Description, Free Text)`, entries separated by `;`, and omits a quantity of 1: the example above appears as `2×Vinyl (LP, Album, Reissue, 180g, Gatefold)`.

### Track positions

Position format tells you the physical format:

| Pattern | Format |
|---------|--------|
| A1, A2, B1, B2 | Vinyl (sided). A/B = sides. |
| C1, D1 | Additional vinyl discs in a set. |
| 1, 2, 3 | CD or digital (no sides). |
| 1-1, 1-2, 2-1, 2-2 | Multi-disc CD set (disc-track). |
| 3.1, 3.2, 3.3 | Sub-tracks (e.g., songs within a DJ mix track). |
| A3a, A3b | Sub-tracks on vinyl. |
| Video 1, Video 2 | Enhanced CD bonus content. |

Side specification is mandatory for sided formats. Sides follow the physical order on the release.

## Unofficial Releases: Bootlegs, Counterfeits, Pirates

Discogs tags bootlegs, counterfeits and pirate copies alike with the format description "Unofficial Release", and `search --release-type unofficial` returns all three. No field tells them apart: which kind a release is remains a judgement call, made from the signals below. Discogs records none of it.

"Partially Unofficial" is a separate description: `--release-type unofficial` does not return those releases, and `official` (the default of `search`; `get versions` defaults to `all`) keeps them. List them with `--release-type all --format "Partially Unofficial"`.

| Kind | What it is | Signals on Discogs |
|------|------------|--------------------|
| Bootleg | Material never officially released: live recordings, demos, outtakes, unreleased films | Its own title, and either no master or a master with no official versions; often `Not On Label (Artist)` or an invented label |
| Counterfeit | A copy of an official release made to pass as genuine | Grouped under the official release's master (`→ @m...`); the official label name and catalog number copied (e.g. "Island Records" ILPSD 8012 LPs under @m3719) |
| Pirate | An unauthorised copy of official content that does not pretend to be genuine | Under the official master too, usually with a local label and catalog number (e.g. Russian or Indonesian cassettes) |

A question that says "bootleg" usually means the first kind only; `--release-type unofficial` alone is not the answer to it.

### How to check

1. **Find the master.** A search row ends in `→ @m...` (JSON `master`); `get release` prints `Master: @m...`. When neither shows one, the release has no master: a bootleg, unless it falls into one of the traps below.
2. **See whether that master has official versions.** `get versions @m... --release-type official --limit 5 --json` keeps the versions whose format does not list "Unofficial Release". It is a client-side scan: 1 request per 100 versions read (usually 1; as text, 1 more for the header title), until 5 rows are kept or the list ends. Skip the rows whose format lists "Partially Unofficial": `official` keeps them, but they are not evidence that the master has official versions, since Discogs tags releases that may not adhere to usual copyright law with either description (inspect such a release's notes, `get release @r... -v`, and tracks, `tracks @r...`, before classifying it). Any other row is official; promo, advance and test-pressing copies count too, since the label issued them. If every row shown lists "Partially Unofficial", continue with the `next_cursor` (`--after <next_cursor>`; as text, run the printed `Next page:` / `Continue scan:` command) until a row qualifies or `next_cursor` is `null`. One official row is enough: the unofficial releases under that master copy official content, so they are counterfeits or pirates, not bootlegs. No official row by the end of the list (`results` empty or only "Partially Unofficial", `next_cursor` `null`, not `capped`) means a bootleg master, unless its tracks are officially released recordings (see the compilation trap below): `get versions @m563536` ("Woodstock 94") lists dozens of bootlegs of Nine Inch Nails' 1994 Woodstock set, every one "Unofficial Release".
3. **Counterfeit or pirate**, when it matters: compare the release's label and catalog number with those of the official versions found in step 2 (for a release with no master, `get versions` of the official master found under Traps). The official label name and catalog number copied point to a counterfeit; a local label and catalog number to a pirate.

Several releases often share a master: check each master once (a repeat within the hour is cached anyway).

### Traps

- **A counterfeit with no master** slips past the master check and looks like a bootleg. Compare its title and tracklist (`tracks @r...`) with the official release's (`search master "<title>" --artist "<name>"`, then `get master @m...`): the same album under the same title is a counterfeit or a pirate.
- **An invented label name does not by itself make a counterfeit.** Bootlegs and pirates use invented labels too. A compilation whose title and tracklist match no official release is not a counterfeit, but that alone does not make it a bootleg either: check its recordings (`get release @r... --verbose` for the notes, which often say where the tracks come from; `tracks @r...` for live or demo versions, compared with the artist's official masters). Live sets, demos and outtakes make it a bootleg; officially released recordings in a new sequence make it a pirate.
- **`Not On Label (Artist)` does not by itself make a bootleg.** Counterfeits and pirates carry it too; the master decides.

## Identifiers

The Barcodes & Other Identifiers section captures various codes found on releases.

| Type | What it is |
|------|-----------|
| Barcode | UPC/EAN from packaging. No barcodes before 1979. |
| Matrix (Runout) | Stamped/etched in vinyl runout grooves or CD inner ring. |
| Matrix (Label) | Printed on the label of the record. |
| SID Code (Mould) | IFPI code on optical discs (post-1994). Pattern: "IFPI xxxx". |
| SID Code (Mastering) | IFPI code starting with "L". Pattern: "IFPI Lxxx". |
| Label Code | GVL-assigned code for rights holders. Format: "LC xxxxx". |
| ISRC | International Standard Recording Code. Per-track identifier. |
| Rights Society | Organization administering copyright (e.g., ASCAP, GEMA). |
| SPARS Code | Three-letter code on CDs indicating recording/mixing/mastering chain (AAD, ADD, DDD). |

Matrix and runout numbers are often the best way to distinguish otherwise identical-looking pressings.

### Rights society hints

Rights society codes on a release can hint at the country of origin, though they are not definitive:

| Code | Country hint |
|------|-------------|
| ASCAP, BMI, SESAC | USA |
| GEMA, GVL | Germany |
| PRS, MCPS | UK |
| SACEM | France |
| JASRAC | Japan |
| SOCAN | Canada |
| BUMA/STEMRA | Netherlands |
| SIAE | Italy |
| SGAE | Spain |
| SUISA | Switzerland |

These are clues, not proof. A BMI credit can appear on a non-US release.

## What Makes Releases Unique

Two copies are the same release unless they differ in a way that can be identified by examining the item. Differences that make a release unique:

- Different label, catalog number, or barcode
- Different country of distribution
- Different format (Vinyl vs CD, LP vs 12")
- Different tracklist or track order
- Different artwork or packaging (when identifiable)
- Color vinyl variants
- Promo vs retail versions (when marked)

Differences that do **not** make a release unique:

- Different stamper numbers from the same edition
- Manufacturing tolerance in label paper or ink shade
- Mislabeled sides (correct labels on wrong sides)
- Unintended vinyl color variation from stock
- Cut-out marks, price stickers, or post-manufacture defacement
