"""JSON output helpers for --json flag."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, NamedTuple

from agent_discogs.pagination import PageResult

Projector = Callable[[Any], Any]


class Mode(NamedTuple):
    """How a command should emit: text, projected JSON, or raw SDK JSON."""

    json: bool = False
    full: bool = False

    def emit_entity(self, obj: Any, project: Projector) -> None:
        dump_entity(obj, project, full=self.full)

    def emit_page(self, result: PageResult, project: Projector) -> None:
        dump_page(result, project, full=self.full)


def _emit(data: Any) -> None:
    print(json.dumps(data, separators=(",", ":"), default=str))


def raw(obj: Any) -> Any:
    """The SDK `model_dump()` of an object, or the object itself if it has none."""
    return obj.model_dump() if hasattr(obj, "model_dump") else obj


def dump(data: Any) -> None:
    """Print an already-projected document."""
    _emit(data)


def dump_entity(obj: Any, project: Projector, *, full: bool) -> None:
    """Print one entity: its projection, or the raw SDK model when `full`."""
    _emit(raw(obj) if full else project(obj))


def dump_page(result: PageResult, project: Projector, *, full: bool) -> None:
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
    rows = [raw(item) if full else project(item) for item in result.items]
    _emit({"pagination": pagination, "results": rows})
