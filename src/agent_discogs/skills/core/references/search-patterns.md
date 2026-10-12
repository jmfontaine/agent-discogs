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

`date_added` is undocumented: Discogs lists no sort keys for search and has only been seen honoring it on release searches, so `--sort` on artist, label, master, or untyped searches is rejected before any request. If Discogs ever drops the parameter, results most likely come back in relevance order with no error, so check the dates before trusting the order.

## Filter Combinations

Effective filter combinations for narrowing results:

| Goal | Filters |
|------|---------|
| Original pressing | `--year <original-year> --country <origin-country>` |
| Specific format | `--format "Vinyl"` or `--format "CD"` |
| Label + catalog | `--label "Nothing Records" --catno "INT-92346"` |
| Region-specific | `--country US` or `--country UK` or `--country Japan` |
| Genre exploration | `--genre "Electronic" --style "Industrial"` (Electronic requires a style) |

## Catalog Number and Barcode Searches

When you have a catalog number or barcode, these are the most precise search methods:

```bash
# Catalog number — often printed on the spine/label of a record
agent-discogs search release --catno "INT-92346"

# Barcode — UPC/EAN from the packaging
agent-discogs search release --barcode "606949235024"
```

These typically return very few results (often exactly 1), making them ideal for identifying a specific pressing.

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
- A `≤N` total means the count is the unfiltered upper bound of a client-side scan (`--release-type official`, or `unofficial` with `--format`). The scan reads 100-row pages until the page fills or the results end, one request per 100 rows, with no cap by default. `--release-type unofficial` on its own is filtered by Discogs, so its total is exact and it never shows `Continue scan:`.
- With a tool-call time limit, cap the scan: `--max-requests N` stops it after N HTTP sends (cache hits are free) and ends in `Continue scan:`, with `scan stopped at --max-requests N` in the header. A rare filter over a broad query can otherwise take tens of requests; narrowing the query beats both.
- `Continue scan:` with `scan stopped: rate limited` means Discogs answered 429 partway: the rows read so far are shown (`stop_reason: "rate_limited"`, `retry_after` in JSON). Wait before pasting the command.
