---
name: agent-discogs
description: >
  Discogs music database CLI for AI agents. Search releases, artists,
  labels, and master releases. Look up album details, tracklists, and
  vinyl prices. Explore artist discographies and compare pressings.
  Use when asked to "find a record", "look up an album", "check vinyl
  prices", "what pressings exist", "explore an artist's discography",
  "identify a pressing by catalog number", "compare different pressings",
  "how much is this record worth", "what's this album's value",
  "what label released this", "who played on this album",
  "find this song's album", "what year did this come out",
  "look up this catalog number", "search the music database",
  or any task involving music collecting and the Discogs database.
allowed-tools: Bash(agent-discogs:*)
---

# agent-discogs

Token-efficient Discogs CLI for AI agents. Typed refs (`@r847868`, `@m3719`, `@a3857`, `@l647`) flow from one command into the next.

Install: `pip install agent-discogs` (or `uv tool install agent-discogs`, or `brew install jmfontaine/tap/agent-discogs`), then `export DISCOGS_TOKEN=<token>` from discogs.com/settings/developers.

## Start here

This file is a discovery stub, not the usage guide. Before running any `agent-discogs` command, load the core guide from the CLI itself so the instructions match the installed version, then the skill the task needs:

```bash
agent-discogs skills get core          # setup, refs, search and navigation, output, budget, troubleshooting
```

## Specialized skills

```bash
agent-discogs skills get collection    # the user's own collection, lists, ownership marks
agent-discogs skills get marketplace   # prices, the wantlist's copies for sale, a seller's inventory
agent-discogs skills get pressings     # telling pressings apart: versions, identifiers, unofficial releases
```

Add `--full` to a skill only when you need its command reference. `agent-discogs skills` lists everything the installed version ships.

Do not guess commands or flags from memory or from this file; they change between releases.

If a skill named here is not in `agent-discogs skills`, the installed agent-discogs is older than this file: use the skills it lists, run `agent-discogs --help`, invent nothing, and tell the user that upgrading restores the full guide.
