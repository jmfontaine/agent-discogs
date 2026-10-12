"""SDK client initialization."""

from __future__ import annotations

import os

from discogs_sdk import Discogs

from agent_discogs import trace
from agent_discogs.config import CACHE_TTL, cache_dir

_client: Discogs | None = None


def get_client() -> Discogs:
    """Get or create the Discogs SDK client."""
    global _client  # noqa: PLW0603  # module-level singleton is the point
    if _client is not None:
        return _client

    token = os.environ.get("DISCOGS_TOKEN")
    _client = Discogs(
        token=token,
        cache=True,
        cache_dir=cache_dir(),
        cache_ttl=CACHE_TTL,
        on_request=trace.record,
    )
    return _client
