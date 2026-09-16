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

Install: `pip install agent-discogs` (or `uv tool install agent-discogs`), then `export DISCOGS_TOKEN=<token>` from discogs.com/settings/developers.

## Start here

This file is a discovery stub, not the usage guide. Before running any `agent-discogs` command, load the workflow guide from the CLI itself so the instructions match the installed version:

```bash
agent-discogs skills get core          # workflows, output format, common patterns, troubleshooting
agent-discogs skills get core --full   # plus the full command reference, search patterns, pressings guide, Discogs data model
```

`agent-discogs skills` lists everything the installed version ships.
