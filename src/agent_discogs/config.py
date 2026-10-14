"""Shared configuration: where the cache lives and how long it keeps
responses, and the release types `--release-type` filters on.

A leaf module: it imports nothing from the package, so any module can import
it without creating a cycle.
"""

from __future__ import annotations

import os
from pathlib import Path

# Passed to Discogs(cache_ttl=...); `status` and --debug show CACHE_TTL_LABEL.
CACHE_TTL = 3600.0
CACHE_TTL_LABEL = f"{CACHE_TTL / 3600:g}h"

# The format description Discogs tags bootlegs, counterfeits and pirates with,
# and the `--release-type` values of `search` and `get versions` that filter
# on it. "Partially Unofficial" is another description: it counts as official.
UNOFFICIAL = "Unofficial Release"
RELEASE_TYPES = ("official", "unofficial", "all")


def cache_dir() -> Path:
    """`$XDG_CACHE_HOME/agent-discogs` when that variable is non-empty and
    absolute, as the XDG spec requires, otherwise `~/.cache/agent-discogs`.

    Resolved on each call rather than at import, and home only on the
    fallback, so commands that never touch the cache work without one.
    """
    # An empty value parses as ".", which is relative, so it falls back too.
    xdg = Path(os.environ.get("XDG_CACHE_HOME", ""))
    base = xdg if xdg.is_absolute() else Path.home() / ".cache"
    return base / "agent-discogs"
