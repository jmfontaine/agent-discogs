"""JSON output helpers for --json flag."""

from __future__ import annotations

import json
from typing import Any

from agent_discogs.pagination import PageResult


def dump_entity(obj: Any, **extra: Any) -> None:
    """Print a single SDK object as JSON.

    Extra kwargs are merged into the top-level dict (used for price data).
    """
    data = obj.model_dump()
    if extra:
        for key, value in extra.items():
            data[key] = value.model_dump() if hasattr(value, "model_dump") else value
    print(json.dumps(data, indent=2, default=str))


def dump_list(key: str, items: list[Any]) -> None:
    """Print a list of SDK objects as JSON under the given key."""
    data = {key: [item.model_dump() for item in items]}
    print(json.dumps(data, indent=2, default=str))


def dump_page(result: PageResult) -> None:
    """Print paginated results as JSON with envelope.

    Client-side filtered pages add `filtered` (total is an upper bound),
    `capped`, and `next_cursor` (pass back via `--after`).
    """
    pagination: dict[str, Any] = {
        "page": result.page,
        "total_items": result.total_items,
        "total_pages": result.total_pages,
    }
    if result.filtered:
        pagination["filtered"] = True
        pagination["capped"] = result.capped
        pagination["next_cursor"] = result.next_cursor
    data = {
        "pagination": pagination,
        "results": [item.model_dump() for item in result.items],
    }
    print(json.dumps(data, indent=2, default=str))
