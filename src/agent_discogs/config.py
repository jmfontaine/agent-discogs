"""Shared configuration: where the cache lives, how long it keeps responses,
how many requests one scan may spend, and whether a token is set.

A leaf module: it imports nothing from the package, so any module can import
it without creating a cycle.
"""

from __future__ import annotations

import os
from pathlib import Path

CACHE_DIR = (
    Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "agent-discogs"
)
# Passed to Discogs(cache_ttl=...) and shown by --debug.
CACHE_TTL = 3600.0
# Requests one client-side filtered scan may spend.
MAX_API_CALLS = 5


def has_token() -> bool:
    """Check if a DISCOGS_TOKEN is configured."""
    return bool(os.environ.get("DISCOGS_TOKEN"))
