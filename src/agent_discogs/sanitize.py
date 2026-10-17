"""Neutralize untrusted text before a text view prints it.

Discogs text is user-generated: titles, notes and error bodies can carry
escape sequences, CR/LF, bidi overrides and line separators. Printed raw, they
can rewrite the terminal, reorder what it shows, or start a line that looks
like CLI framing (`Next page:`, `✗`). They can also carry characters that draw
nothing (zero-width and tag characters): a message the person at the terminal
cannot see and the agent reading stdout can. Text views pass every line
through `line()`. JSON keeps values exact and escapes such characters
losslessly instead (`json_output`).
"""

from __future__ import annotations

import re

# C0 controls, DEL, C1 controls, bidi controls, the line/paragraph
# separators, and invisible characters: U+200B, U+2060-U+206F (word joiner,
# invisible operators, isolates, deprecated format controls), U+FEFF and the
# tag characters U+E0000-U+E007F. U+200C and U+200D stay: emoji sequences and
# ordinary words in several scripts need them. Line breaks are removed before
# this applies; it catches the rest.
_CONTROLS = re.compile(
    "[\x00-\x1f\x7f-\x9f\u061c\u200b\u200e\u200f\u202a-\u202e\u2028\u2029"
    "\u2060-\u206f\ufeff\U000e0000-\U000e007f]"
)
_ONE_BYTE = 0x100
_BMP_END = 0x10000


def _escape(match: re.Match[str]) -> str:
    # Python's own spellings: `\uNNNN` has four digits, so a code point above
    # U+FFFF (a tag character) takes `\U` and eight.
    code = ord(match.group())
    if code < _ONE_BYTE:
        return f"\\x{code:02x}"
    if code < _BMP_END:
        return f"\\u{code:04x}"
    return f"\\U{code:08x}"


def line(text: str) -> str:
    """One display line: line breaks (every one `str.splitlines()` knows) and
    tabs become spaces; any other control or bidi character, U+200B,
    U+2060-U+206F, U+FEFF and the tag characters U+E0000-U+E007F become a
    visible escape such as `\\x1b`, `\\u202e`, `\\u200b` or `\\U000e0041`."""
    return _CONTROLS.sub(_escape, " ".join(text.splitlines()).replace("\t", " "))


def summary(text: str, limit: int = 200) -> str:
    """Untrusted text as a single line of at most `limit` characters, for
    error messages: whitespace runs collapse, the rest is `line()`."""
    text = line(" ".join(text.split()))
    return text if len(text) <= limit else text[: limit - 1] + "…"
