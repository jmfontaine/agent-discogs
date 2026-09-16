# Pressings Guide

## The Master/Release/Version Model

Discogs organizes music data in a three-level hierarchy:

### Master Release
A **master** represents the canonical album — one per album regardless of how many times it has been pressed, reissued, or reformatted.

- Example: "The Downward Spiral" by Nine Inch Nails (master ID 4917)
- Contains: title, artists, genres, styles, tracklist
- Links to all its versions (releases)

### Release
A **release** represents a specific physical or digital product — a particular pressing with a specific label, catalog number, country, format, and year.

- Example: "The Downward Spiral" — 1994 US Nothing Records INT-92346 Vinyl, LP
- Example: "The Downward Spiral" — 2017 US The Bicycle Music Company 180g 2xLP
- Contains: full details including tracklist, label credits, identifiers, community data, marketplace info

### Version
A **version** is simply a release that belongs to a master. When you list versions of a master, you're seeing all the different pressings of that album.

## When to Use Master vs Release

| You want to... | Use |
|----------------|-----|
| Find all pressings of an album | `search master` → `get versions @m4917` |
| Find a specific pressing | `search release` with filters |
| Compare pressings across countries | `get versions @m4917 --country US` vs `--country UK` |
| Get details of one pressing | `get release @r367113` |
| See the canonical tracklist | `get master @m4917` |

## Finding the Original Pressing

1. Search for the master: `agent-discogs search master "The Downward Spiral"`
2. List versions: `agent-discogs get versions @m4917`
3. Look for the earliest year and original country
4. Get details: `agent-discogs get release @r367113`

## Common Pressing Attributes

- **Country**: The distribution/sales market (not the manufacturing location)
- **Catalog Number (catno)**: Label's identifier for this pressing
- **Format**: Physical format and descriptions (Vinyl, LP, CD, 180g, Reissue, etc.)
- **Label**: The label that released this pressing

## Master Grouping Rules

What belongs under one master vs separate masters determines whether to search by master or release.

### Same master

Releases group together when they share artwork, tracklist, and title (including translations). This includes:
- Reissues, represses, promos, colored vinyl variants, special editions
- Instrumental versions, remixes, multilingual versions
- Format changes (CD version of an LP album)

Format descriptions must also align: Album versions together, EP versions together, Single/Maxi-Single versions together (including remix singles).

### Separate masters

- A compilation combining two albums cannot share a master with either individual album
- Double A-side singles with different track pairings get separate masters (e.g., "White Horse / So Wie So" vs "White Horse / Sunshine Reggae")
- A release can only belong to one master

### Practical implication

When searching for "all versions of an album," use `search master` then `get versions`. When searching for a specific single that may have variant B-sides, use `search release` with filters instead.

## Tips

- The same album can have hundreds of releases across different countries, formats, and years
- Original pressings (first year, original country) are typically the most valued
- Reissues and remasters may have different audio quality
- Catalog numbers are the most reliable way to identify a specific pressing
- Use `get versions` with `--country` and `--format` filters to narrow down large version lists
