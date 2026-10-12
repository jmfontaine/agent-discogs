"""Neutralize untrusted text before a text view prints it.

Discogs text is user-generated: titles, notes and error bodies can carry
escape sequences, CR/LF, bidi overrides and line separators. Printed raw, they
can rewrite the terminal, reorder what it shows, or start a line that looks
like CLI framing (`Next page:`, `✗`). Text views pass every line through
`line()`. JSON keeps values exact and escapes such characters losslessly
instead (`json_output`).
"""

from __future__ import annotations

import re

# C0 controls, DEL, C1 controls, bidi controls, and the line/paragraph
# separators. Line breaks are removed before this applies; it catches the rest.
_CONTROLS = re.compile(
    "[\x00-\x1f\x7f-\x9f\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069\u2028\u2029]"
)
_ONE_BYTE = 0x100


def _escape(match: re.Match[str]) -> str:
    code = ord(match.group())
    return f"\\x{code:02x}" if code < _ONE_BYTE else f"\\u{code:04x}"


def line(text: str) -> str:
    """One display line: line breaks (every one `str.splitlines()` knows) and
    tabs become spaces; any other control or bidi character becomes a visible
    escape such as `\\x1b` or `\\u202e`."""
    return _CONTROLS.sub(_escape, " ".join(text.splitlines()).replace("\t", " "))


def summary(text: str, limit: int = 200) -> str:
    """Untrusted text as a single line of at most `limit` characters, for
    error messages: whitespace runs collapse, the rest is `line()`."""
    text = line(" ".join(text.split()))
    return text if len(text) <= limit else text[: limit - 1] + "…"
