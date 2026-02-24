"""SDK client initialization."""

from __future__ import annotations

import os
from pathlib import Path

from discogs_sdk import Discogs

_client: Discogs | None = None

CACHE_DIR = (
    Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "agent-discogs"
)


def get_client() -> Discogs:
    """Get or create the Discogs SDK client."""
    global _client
    if _client is not None:
        return _client

    token = os.environ.get("DISCOGS_TOKEN")
    _client = Discogs(token=token, cache=True, cache_dir=CACHE_DIR)
    return _client


def has_token() -> bool:
    """Check if a DISCOGS_TOKEN is configured."""
    return bool(os.environ.get("DISCOGS_TOKEN"))
