"""JSON output helpers for --json flag."""

from __future__ import annotations

import codecs
import json
import re
import sys
from collections.abc import Callable
from typing import Any, NamedTuple

from agent_discogs.pagination import PageResult

# Written as \uXXXX even on a UTF-8 stdout: C1 controls, bidi controls, and
# the line/paragraph separators. Literal, they can reorder or break what a
# terminal or log viewer shows. So are the characters that draw nothing
# (U+200B-U+200D, U+2060-U+206F, U+FEFF, the tag characters U+E0000-U+E007F),
# which can hide text from a person while an agent reads it; the text views
# keep U+200C and U+200D, which words and emoji need, but JSON loses nothing by
# escaping them. Lone surrogates (from undecodable argv bytes or a `\udXXX`
# escape in an API body) cannot be encoded as UTF-8 at all. json.dumps emits
# non-ASCII characters only inside string literals, so escaping them
# afterwards is still valid JSON and changes no value.
_ALWAYS_ESCAPED = re.compile(
    "[\u0080-\u009f\u061c\u200b-\u200f\u202a-\u202e\u2028\u2029\u2060-\u206f"
    "\ud800-\udfff\ufeff\U000e0000-\U000e007f]"
)
_BMP_END = 0x10000

Projector = Callable[[Any], Any]


class Mode(NamedTuple):
    """How a command should emit: text, projected JSON, or the full SDK record."""

    json: bool = False
    full: bool = False

    def entity(self, obj: Any, project: Projector) -> Any:
        """The JSON document for one entity under this mode."""
        return full_dump(obj) if self.full else project(obj)

    def page(self, result: PageResult, project: Projector) -> dict[str, Any]:
        """The JSON envelope for one page under this mode."""
        return page_document(result, project, full=self.full)


def _stdout_is_utf8() -> bool:
    # A stream without an encoding (StringIO) gets the safe ASCII output.
    encoding = getattr(sys.stdout, "encoding", None) or "ascii"
    return codecs.lookup(encoding).name == "utf-8"


def _escape(match: re.Match[str]) -> str:
    # JSON has no `\U`: a code point above U+FFFF is a surrogate pair, which a
    # parser combines back into it. The text views write `\U000e0041` instead.
    code = ord(match.group())
    if code < _BMP_END:
        return f"\\u{code:04x}"
    code -= _BMP_END
    return f"\\u{0xD800 + (code >> 10):04x}\\u{0xDC00 + (code & 0x3FF):04x}"


def dumps(data: Any) -> str:
    """Compact JSON for stdout: non-ASCII literal when stdout is UTF-8, else
    `\\uXXXX` so the document is valid in any encoding. `_ALWAYS_ESCAPED`
    characters are escaped either way, an astral one as a `\\uXXXX\\uXXXX`
    surrogate pair."""
    text = json.dumps(
        data, separators=(",", ":"), default=str, ensure_ascii=not _stdout_is_utf8()
    )
    return _ALWAYS_ESCAPED.sub(_escape, text)


def _emit(data: Any) -> None:
    print(dumps(data))


def full_dump(model: Any) -> dict[str, Any]:
    """The `--full` document for one SDK model (or lazy proxy): its JSON-mode dump.

    That is the SDK's normalized record: its field names (`catalog_number`,
    `extra_artists`, a track's `type`), RFC 3339 timestamps with an offset,
    `None` for a timestamp that is absent or does not parse, and any field the
    SDK does not model as Discogs sent it.
    """
    return model.model_dump(mode="json")


def dump(data: Any) -> None:
    """Print an already-projected document."""
    _emit(data)


def page_document(
    result: PageResult, project: Projector, *, full: bool
) -> dict[str, Any]:
    """Paginated results as a JSON envelope.

    Client-side filtered pages add `filtered` (total is an upper bound),
    `capped`, and `next_cursor` (pass back via `--after`). A page that left
    out rows repeating the row before them adds `duplicates_dropped`. A
    capped page adds `stop_reason` (`max_requests` or `rate_limited`) and,
    after a 429 that said, `retry_after`. Totals are null when the scan
    stopped before reading any listing page.
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
    if result.duplicates_dropped:
        pagination["duplicates_dropped"] = result.duplicates_dropped
    if result.stop is not None:
        pagination["stop_reason"] = result.stop.reason
        if result.stop.retry_after is not None:
            pagination["retry_after"] = result.stop.retry_after
    rows = [full_dump(item) if full else project(item) for item in result.items]
    return {"pagination": pagination, "results": rows}
