# Tasks

Recipes for requests about the database and the budget, by stable ID. The legend and the other skills' tasks are under Tasks in `agent-discogs skills get core`.

### F3 Artist names without the "(2)"

Status: works

```bash
agent-discogs get credits @r847868
```

Discogs adds ` (2)`, ` (3)`, ... to a name other artists share (`John B (2)`), and the CLI prints names as sent. Drop a trailing ` (N)` yourself when you show a name, and keep the ref to tell the artists apart: [Disambiguation suffixes](discogs-domain.md#disambiguation-suffixes).

Cost: 1.

### F4 What is the BPM or key of a track?

Status: not possible

Why not: not on Discogs: a track carries its position, title, duration and credits, no tempo or key. Say so; do not estimate.

Would need: tempo and key data, which Discogs does not hold (Discogs API limit).

### G1 Which releases did this person produce or engineer?

Status: works, costly

Produced: scan the person's discography for the role. Engineered: discography rows rarely carry that role, so search the credit and confirm the role on each release:

```bash
agent-discogs get releases @a20661 --role Producer --limit 100 --max-requests 60
agent-discogs search release --credit "Flood" --genre Rock --year 1994 --release-type all
agent-discogs get credits @r847868
```

`--role` matches part of the row's role, any case. `--credit` matches the person in any role: `get credits` says which.

Cost: produced: 1 (the name; none with `--json`) + 1 per 100 discography rows scanned; engineered: 1 per search page + 1 per release checked.

Would need: role filters on search and discographies (Discogs API limit).

### G2 Which releases contain this track?

Status: works

```bash
agent-discogs search release --track "Hurt" --artist "Nine Inch Nails" --release-type all
```

Matches track titles only. Without `--artist` a common title matches thousands of releases; `search master --track ...` gives one row per album.

Cost: 1 per page.

### G3 What has this label released?

Status: works

```bash
agent-discogs get releases @l647 --limit 100
```

Rows Discogs sends twice in a row are dropped (`N repeated rows dropped` in the header). Copy `@l...` from a release's `Label:` line, or find it with `search label`.

Cost: 1 per 100 catalogue rows, plus 1 for the label's name in text.

### H1 Trends across the whole database

Status: not possible

Why not: the API reads one entity or one search page at a time and has no aggregate query. Discogs publishes monthly data dumps (data.discogs.com) for that.

Would need: aggregate queries (Discogs API limit).

### I1 Run a large job within the rate limit

Status: works, costly

Cap each command at a minute of budget, then paste the printed continuation after the window:

```bash
agent-discogs get releases @a20661 --role Producer --max-requests 60 --limit 100
agent-discogs get releases @a20661 --role Producer --max-requests 60 --limit 100 --after 2:5900
```

The first stops with `scan stopped at --max-requests 60` and prints the second as its `Continue scan:`, with the cursor where it stopped; wait ~60s before pasting it. Repeats within the hour are free, and `--cached` sends nothing at all.

Cost: each run its cap at most; the header lookup is cached after the first.

Would need: a higher rate limit (Discogs API limit: 60 requests a minute).

### I2 My own collection or wantlist, without typing a username

Status: works

```bash
agent-discogs get wantlist
```

`get collection`, `get wantlist`, `get lists`, `get owned`, `--owned` and `--wanted` read the account behind the token (`Wantlist: me`) and take no username. `get inventory` is the exception: it takes a seller's username, yours for your own listings.

Cost: 1 (the username) + 1 per page.
