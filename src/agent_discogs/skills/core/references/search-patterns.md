# Search Patterns

## Type-Specific Strategies

### Finding a specific album pressing
```bash
# Start broad, narrow with filters
agent-discogs search release "The Downward Spiral" --year 1994 --country US
agent-discogs search release "The Downward Spiral" --format "Vinyl" --label "Nothing Records"
```

### Finding an artist
```bash
# Use artist type to avoid release/label noise
agent-discogs search artist "Nine Inch Nails"
```

### Finding a label
```bash
agent-discogs search label "Nothing Records"
```

### Finding all pressings of an album
```bash
# Search for the master, then explore versions
agent-discogs search master "The Downward Spiral"
agent-discogs get versions @m3719
```

### Finding the newest additions
```bash
# Release searches only; newest-added first
agent-discogs search release --artist "Nine Inch Nails" --sort date_added --desc
```

`date_added` is undocumented (Discogs lists no sort keys for search): the `--sort` row under [search](commands.md#search) says which searches take it and what happens if Discogs drops it. Check the dates before trusting the order.

### Releases (or unofficial releases) added since a date
```bash
# Everything added in the last 3 months, newest first
agent-discogs search release --artist "Nine Inch Nails" --release-type all --added-since 2026-07-08 --max-requests 60
# Unofficial releases (bootlegs, counterfeits, pirates) added in the last 12 months: Discogs filters them, so only unofficial releases are looked up
agent-discogs search release --artist "Nine Inch Nails" --release-type unofficial --added-since 2025-10-09
```

`--added-since` is experimental; its cost, the `--max-requests` cap and its failures are under [`--added-since`](commands.md#search). It is complete only while Discogs keeps the order it walks, and rerunning with a higher cap, or after a `rate_limited` failure, reuses every cached lookup. It misses releases where the artist has only track-level or secondary credits (`artist=` does not match them) and, under `--release-type unofficial`, unofficial releases not tagged "Unofficial Release".

`--release-type unofficial` returns every unofficial release: bootlegs, counterfeits and pirates alike, since Discogs tags all three the same way. For bootlegs only, check each row's `→ @m...` master as Unofficial Releases in the pressings skill describes (`agent-discogs skills get pressings --full`), and drop the rows whose master has official versions.

## Filter Combinations

Effective filter combinations for narrowing results:

| Goal | Filters |
|------|---------|
| Original pressing | `--year <original-year> --country <origin-country>` |
| Specific format | `--format "Vinyl"` or `--format "CD"` |
| Label + catalog | `--label "Nothing Records" --catno "INT-92346"` |
| Region-specific | `--country US` or `--country UK` or `--country Japan` |
| Genre exploration | `--genre "Electronic" --style "Industrial"` (Electronic requires a style) |

## Catalog Number, Barcode and Runout Searches

`search release --catno "INT-92346"` and `search release --barcode "606949235024"` are the most precise searches. `--barcode` matches identifier text, runout etchings included: how to type it and confirm a match is in `agent-discogs skills get pressings --full`, section Catalog Number, Barcode and Runout Searches.

## Handling Ambiguous Results

When a search returns many similar results (common for popular albums with hundreds of pressings):

1. **Add year filter** to find the original or a specific reissue
2. **Add country filter** to narrow to a specific market
3. **Add format filter** to distinguish Vinyl vs CD vs digital
4. **Switch to master search** and use `get versions` with filters for systematic comparison

## Pagination Strategy

- Default: 5 results per page (token-efficient for quick lookups); `--limit` accepts 1-100
- Use `--limit 10` or `--limit 20` when browsing larger result sets
- Always prefer adding filters over paginating through hundreds of results
- Discogs does not paginate beyond 10,000 results. If a query is that broad, add filters to narrow it.
- Paste the `Next page:` / `Continue scan:` command shown under the results to continue; do not compute `--page` values for the default filtered search. `--page N` works with `--release-type all`, `--release-type unofficial` (unless combined with `--format`), and artist/label searches. The printed command keeps `--cached`/`--fresh` and `--max-requests`, and puts the type and query after `--`, so a query such as `-ambient` survives.
- A `≤N` total is the unfiltered upper bound of a client-side scan (`--release-type official`, or `unofficial` with `--format`); `--release-type unofficial` on its own is filtered by Discogs, so its total is exact and it never shows `Continue scan:`. How a scan reads 100-row pages, stops at `--max-requests N` (cap it under a tool-call time limit) or on a 429, and continues: [Client-side scans](commands.md#client-side-scans). A rare filter over a broad query can take tens of requests; narrowing the query beats capping it.
