# Discogs Domain Knowledge

How Discogs structures and categorizes music data. Use this to interpret search results, choose filters, and understand what fields mean.

## Contents

- [Formats](#formats)
- [Genre and Style](#genre-and-style)
- [Country](#country)
- [Artists](#artists)
- [Titles](#titles)
- [Labels and Catalog Numbers](#labels-and-catalog-numbers)
- [Identifiers](#identifiers)
- [Track Positions](#track-positions)
- [Release Dates](#release-dates)
- [Condition Grading](#condition-grading)
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
| Unofficial Release | Made without copyright holder consent. Covers bootlegs, counterfeits, pirates. |

**Reissue vs Repress**: A reissue can cross formats (CD reissue of an LP). A repress must use the original master disc on the same pressed format. Both tags can coexist.

### Multi-format releases

Quantity field shows count per format: "2 x LP" or "1 x LP, 1 x 7"". Box Set and All Media are always combined with other format types.

### Format string structure

A format display string is composed of: `Qty x Format, Description, Description, (Free Text)`.

- **Descriptions** qualify the format: Album, Reissue, Promo, Limited Edition, etc.
- **Free text** carries additional details: color (Red), packaging (Gatefold, Digipak), weight (180g), bitrate (320 kbps), cassette info (C60, Dolby B).

Example: `2xLP, Album, Reissue, (180g, Gatefold)` means 2 vinyl LPs, album-length, reissued, pressed at 180g in a gatefold sleeve.

## Genre and Style

Discogs uses a two-level system: **genre** (broad category) and **style** (sub-genre).

### All 15 genres

Blues, Brass & Military, Children's, Classical, Electronic, Folk World & Country, Funk / Soul, Hip-Hop, Jazz, Latin, Non-Music, Pop, Reggae, Rock, Stage & Screen.

### Key rules

- Genres can be combined (e.g., Electronic + Rock).
- **Electronic requires at least one style** (e.g., "Drum n Bass", "House", "Techno"). All other genres treat style as optional.
- Style is essentially a sub-genre. The available styles are intentionally limited.

Use `--genre` for broad filtering and `--style` to narrow within a genre.

## Country

Country means the **distribution/sales market**, not the manufacturing location.

- A US label can manufacture in Austria but distribute in Germany. Country = Germany.
- "Worldwide" is used for releases specifically marketed globally (common for digital and post-2010 major label releases).
- For imports, country = where it was imported from (the original market).
- Small labels usually release in the country they are based in.

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

## Artists

### Primary Artist Name (PAN)

The canonical name on Discogs. For performers, this is their stage name. For non-performers (engineers, etc.), it is the name used on the majority of releases.

### Artist Name Variation (ANV)

How the PAN appears on a specific release. ANVs cover nicknames, abbreviations, initials, translations, and misspellings. The ANV must be recognizably derived from the PAN.

Example: PAN "David Bowie" might have ANVs "D. Bowie", "Bowie", "David Robert Jones".

### Aliases

Completely different names used by the same person. Unlike ANVs, aliases are not derived from the PAN. They get their own artist page, linked via the alias system.

### Disambiguation suffixes

Artists sharing a name get numeric suffixes: "John B" and "John B (2)". The number is arbitrary (not popularity or chronology). Suffixes are never swapped once assigned.

### Special artists

| Name | When used |
|------|-----------|
| Various | Multiple artists, none billed as main. |
| Unknown Artist | Artist not credited and cannot be determined. |
| No Artist | No artist performing (sound effects, field recordings, etc.). |

### Artist roles on releases

A release has one **main artist** credit (the performer on the cover). All other contributors appear in **credits** with specific roles. When listing an artist's discography, each release shows the artist's role:

| Role | Meaning |
|------|---------|
| Main | The credited performing artist. |
| Remix | Remixed a track on the release. |
| Producer | Produced the recording. |
| Appearance | Featured or guest appearance. |
| TrackAppearance | Credited on specific tracks only. |
| Written-By | Songwriter or composer credit. |
| DJ Mix | Mixed the release (common for compilations). |

Searching by artist returns releases where they are the main artist. To find releases where an artist contributed as producer, remixer, or writer, use `get releases` which includes all roles.

## Titles

- **Self-titled**: When a release has no obvious title, the artist's name is used (assumed eponymous). Example: "The Beatles" by The Beatles.
- **Double A-side**: Two featured tracks separated by a slash. Example: "Track A / Track B".
- **Subtitles**: Entered in parentheses when the release lacks its own separator. Example: "Title (Subtitle)".
- **Untitled**: Used when the cover is blank and no external title exists.
- **Singles without a sleeve**: Release title is taken from the A-side track title.

## Labels and Catalog Numbers

### Labels

A label is a brand or imprint used to identify releases. The main label is usually the largest logo on the release. Labels with the same name are disambiguated with numeric suffixes, same as artists.

### Not On Label

Self-released, white labels, bootlegs, and other releases with no discernible label use "Not On Label". Common patterns:
- `Not On Label (Artist Name Self-released)` for artist self-releases.
- `Not On Label (Artist Name)` for unofficial releases of that artist's music.

### Catalog numbers

The most prominent number on the release (spine, back cover, label). Entered exactly as printed.
- When no catalog number exists, "none" (lowercase) is used.
- Amazon ASINs (B000... prefix) are not catalog numbers. They go in identifiers.
- Label codes (LC-xxxxx) are not catalog numbers. They go in identifiers.

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

## Track Positions

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

## Release Dates

Three date formats are used:

| Format | Meaning |
|--------|---------|
| YYYY-MM-DD | Exact release date (e.g., 1997-09-02). |
| YYYY-MM-00 | Known year and month, unknown day. |
| YYYY | Known year only. |

### Copyright vs release date

Releases carry two copyright symbols:
- **(c)** Copyright: covers packaging, artwork, layout. Usually updated with each release.
- **(p)** Phonographic copyright: covers the sound recording. Usually does not change across reissues.

A CD with "(p) 1985" and "(c) 2003" was likely originally recorded in 1985 and reissued in 2003. Be suspicious of CDs with copyright dates before 1984 (the CD format launched in 1982).

## Condition Grading

Discogs uses the Goldmine Standard. The `price` command returns suggested values for all 8 grades.

| Grade | Abbrev | ~% of NM value | Vinyl condition |
|-------|--------|----------------|-----------------|
| Mint | M | — | Unplayed, possibly sealed. Rarely used. |
| Near Mint | NM or M- | 100% (baseline) | No signs of wear. Plays perfectly. |
| Very Good Plus | VG+ | ~50% | Light cosmetic wear. No audible impact. |
| Very Good | VG | ~25% | Surface noise audible in quiet passages. Light scratches felt with fingernail. |
| Good Plus | G+ | ~15% | Plays through without skipping. Significant surface noise and visible wear. |
| Good | G | ~10% | Plays through without skipping. Heavy wear, seam splits, writing on cover. |
| Fair | F | ~5% | May skip or repeat. Badly worn. |
| Poor | P | ~0% | Cracked or unplayable. Sleeve barely intact. |

- **Standard jewel cases** are not graded (replaceable).
- **Generic** sleeve means a plain or company sleeve not specific to the release. Needs no condition grade.

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
