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
agent-discogs get versions @m4917
```

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

- Default: 5 results per page (token-efficient for quick lookups)
- Use `--limit 10` or `--limit 20` when browsing larger result sets
- Always prefer adding filters over paginating through hundreds of results
- Discogs does not paginate beyond 10,000 results. If a query is that broad, add filters to narrow it.
- Use `--page N` to navigate to specific pages shown in "Next page:" hints
