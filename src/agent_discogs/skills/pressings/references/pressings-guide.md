# Pressings Guide

## The Master/Release/Version Model

Discogs organizes music data in a three-level hierarchy:

### Master Release
A **master** represents the canonical album — one per album regardless of how many times it has been pressed, reissued, or reformatted.

- Example: "The Downward Spiral" by Nine Inch Nails (master ref `@m3719`)
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
| Find all pressings of an album | `search master` → `get versions @m3719` |
| Find a specific pressing | `search release` with filters |
| Compare pressings across countries | `get versions @m3719 --country US` vs `--country UK` |
| Get details of one pressing | `get release @r847868` |
| See the canonical tracklist | `get master @m3719` |

## Finding the Original Pressing

1. Search for the master: `search master "The Downward Spiral"`
2. List versions: `get versions @m3719`
3. Look for the earliest year and original country
4. Get details: `get release @r847868`

## Catalog Number, Barcode and Runout Searches

When you have a catalog number, a barcode or a runout etching, these are the most precise search methods:

```bash
# Catalog number — often printed on the spine/label of a record
agent-discogs search release --catno "INT-92346"

# Barcode — digits from the packaging, without spaces
agent-discogs search release --barcode "606949235024"

# Runout — text etched in the vinyl's matrix/runout area
agent-discogs search release --artist "Human League" --barcode "A3 TOWNHOUSE" --country UK --format LP --release-type all
```

`--barcode` is matched against a release's identifier text, not only as a UPC/EAN: it finds pressings by their matrix/runout etching too, the one search that does. Other identifier types (label codes, for instance) have not been checked.

- **Type a barcode without spaces** (`602527826486`): `"6 02527 82648 6"` also matched an unrelated release. The value is matched as text, so the barcode without its check digit found the same releases.
- **Try each spelling of an etching**: Discogs writes some in more than one way (`TOWNHOUSE` and `TOWN HOUSE`), and one spelling can miss releases the other finds.

A catalog number or barcode usually returns very few results; a runout can match several pressings, so confirm with `get identifiers @r...`.

## Common Pressing Attributes

- **Country**: The distribution/sales market (not the manufacturing location)
- **Catalog Number (catno)**: Label's identifier for this pressing
- **Format**: Physical format and descriptions (Vinyl, LP, CD, 180g, Reissue, etc.). `get release` prints one entry per physical format, separated by `;`: the quantity when above one, the name, then descriptions and free text in parentheses, e.g. `2×Vinyl (LP, Album, 180g, Gatefold); CD (Album)`. Search, releases and versions rows keep Discogs' flat list (`Vinyl, LP, Album`).
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
