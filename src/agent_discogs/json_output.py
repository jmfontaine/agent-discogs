"""JSON output helpers for --json flag."""

from __future__ import annotations

import codecs
import json
import re
import sys
from collections.abc import Callable, Mapping
from typing import Any, NamedTuple

from agent_discogs.pagination import PageResult

# Written as \uXXXX even on a UTF-8 stdout: C1 controls, bidi controls, and
# the line/paragraph separators. Literal, they can reorder or break what a
# terminal or log viewer shows. Lone surrogates (from undecodable argv bytes
# or a `\udXXX` escape in an API body) cannot be encoded as UTF-8 at all.
# json.dumps emits non-ASCII characters only inside string literals, so
# escaping them afterwards is still valid JSON and changes no value.
_ALWAYS_ESCAPED = re.compile(
    "[\u0080-\u009f\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069\u2028\u2029"
    "\ud800-\udfff]"
)

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


def _stdout_is_utf8() -> bool:
    # A stream without an encoding (StringIO) gets the safe ASCII output.
    encoding = getattr(sys.stdout, "encoding", None) or "ascii"
    return codecs.lookup(encoding).name == "utf-8"


def dumps(data: Any) -> str:
    """Compact JSON for stdout: non-ASCII literal when stdout is UTF-8, else
    `\\uXXXX` so the document is valid in any encoding. `_ALWAYS_ESCAPED`
    characters are escaped either way."""
    text = json.dumps(
        data, separators=(",", ":"), default=str, ensure_ascii=not _stdout_is_utf8()
    )
    return _ALWAYS_ESCAPED.sub(lambda m: f"\\u{ord(m.group()):04x}", text)


def _emit(data: Any) -> None:
    print(dumps(data))


def raw(obj: Any) -> Any:
    """The SDK `model_dump()` of an object, or the object itself if it has none.

    The dump keeps the shape discogs-sdk 0.7.0 gave it. 0.8.0 renamed
    `Track.type_` to `type` (see `tracks_as_sent()`) and completes a search
    result's site-relative `uri`; `raw` sends that `uri` as Discogs sent it.
    """
    data = tracks_as_sent(obj.model_dump() if hasattr(obj, "model_dump") else obj)
    sent = getattr(obj, "raw", None)
    if isinstance(sent, Mapping) and isinstance(sent.get("uri"), str):
        data["uri"] = sent["uri"]
    return data


_TRACK_LISTS = frozenset({"tracklist", "sub_tracks"})


def tracks_as_sent(data: Any) -> Any:
    """`data` with every dumped track's `type` key named `type_` again, the
    name Discogs sends and discogs-sdk 0.7.0 kept; 0.8.0 renamed the field."""
    if isinstance(data, list):
        return [tracks_as_sent(item) for item in data]
    if not isinstance(data, dict):
        return data
    return {
        key: [_track_as_sent(t) for t in value]
        if key in _TRACK_LISTS and isinstance(value, list)
        else tracks_as_sent(value)
        for key, value in data.items()
    }


def _track_as_sent(track: Any) -> Any:
    track = tracks_as_sent(track)
    if not isinstance(track, dict):
        return track
    return {("type_" if key == "type" else key): v for key, v in track.items()}


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
