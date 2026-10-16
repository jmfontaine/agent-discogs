# Discogs Domain Knowledge

How Discogs structures and categorizes music data. Use this to interpret search results, choose filters, and understand what fields mean. Formats, track positions, unofficial releases, identifiers (rights society hints included) and what makes releases unique are in the pressings skill (`agent-discogs skills get pressings --full`), condition grading in the marketplace skill.

## Contents

- [Genre and Style](#genre-and-style)
- [Country](#country)
- [Artists](#artists)
- [Titles](#titles)
- [Labels and Catalog Numbers](#labels-and-catalog-numbers)
- [Release Dates](#release-dates)

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

Rights society codes among a release's identifiers can hint at the country: see Identifiers in the pressings skill.

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

Self-released, white labels, unofficial releases (bootlegs, counterfeits, pirates), and other releases with no discernible label use "Not On Label". Common patterns:
- `Not On Label (Artist Name Self-released)` for artist self-releases.
- `Not On Label (Artist Name)` for unofficial releases of that artist's music, of any of the three kinds (see Unofficial Releases in the pressings skill, `agent-discogs skills get pressings --full`).

### Catalog numbers

The most prominent number on the release (spine, back cover, label). Entered exactly as printed.
- When no catalog number exists, "none" (lowercase) is used.
- Amazon ASINs (B000... prefix) are not catalog numbers. They go in identifiers.
- Label codes (LC-xxxxx) are not catalog numbers. They go in identifiers.

## Release Dates

Three date formats are used:

| Format | Meaning |
|--------|---------|
| YYYY-MM-DD | Exact release date (e.g., 1997-09-02). |
| YYYY-MM-00 | Known year and month, unknown day. |
| YYYY or YYYY-00-00 | Known year only. |

How agent-discogs prints them (unknown parts and a year `0` left out): Dates, in the Output Conventions of SKILL.md.

### Copyright vs release date

Releases carry two copyright symbols:
- **(c)** Copyright: covers packaging, artwork, layout. Usually updated with each release.
- **(p)** Phonographic copyright: covers the sound recording. Usually does not change across reissues.

A CD with "(p) 1985" and "(c) 2003" was likely originally recorded in 1985 and reissued in 2003. Be suspicious of CDs with copyright dates before 1984 (the CD format launched in 1982).
