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

    def entity(self, obj: Any, project: Projector) -> Any:
        """The JSON document for one entity under this mode."""
        return raw(obj) if self.full else project(obj)

    def page(self, result: PageResult, project: Projector) -> dict[str, Any]:
        """The JSON envelope for one page under this mode."""
        return page_document(result, project, full=self.full)


def _emit(data: Any) -> None:
    print(json.dumps(data, separators=(",", ":"), default=str))


def raw(obj: Any) -> Any:
    """The SDK `model_dump()` of an object, or the object itself if it has none."""
    return obj.model_dump() if hasattr(obj, "model_dump") else obj


def dump(data: Any) -> None:
    """Print an already-projected document."""
    _emit(data)


def page_document(
    result: PageResult, project: Projector, *, full: bool
) -> dict[str, Any]:
    """Paginated results as a JSON envelope.

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
    return {"pagination": pagination, "results": rows}


def dump_page(result: PageResult, project: Projector, *, full: bool) -> None:
    """Print one page as JSON."""
    _emit(page_document(result, project, full=full))
