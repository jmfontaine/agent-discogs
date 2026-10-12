"""Shared configuration: where the cache lives, how long it keeps responses,
how many requests one scan may spend, and whether a token is set.

A leaf module: it imports nothing from the package, so any module can import
it without creating a cycle.
"""

from __future__ import annotations

import os
from pathlib import Path

# Passed to Discogs(cache_ttl=...) and shown by --debug.
CACHE_TTL = 3600.0
# Requests one client-side filtered scan may spend.
MAX_API_CALLS = 5


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


def has_token() -> bool:
    """Check if a DISCOGS_TOKEN is configured."""
    return bool(os.environ.get("DISCOGS_TOKEN"))
