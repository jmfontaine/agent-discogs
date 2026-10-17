"""Discogs markup in profiles and notes, converted for the CLI.

Discogs stores link and formatting tags inside free text, and its website
renders them. `parse()` turns such a field into view parts: a link to an
entity becomes its ref (`[l7783]` → `@l7783`, a ref-style `Span`: the CLI
writes it from the digits), a name link its name (`[a=Carl Craig]`), a URL
its text and site (`text (host)`), a guideline or forum link the text the
website shows, and `[b]`, `[i]`, `[u]` and `[s]` a `Styled` run of the text
they wrap. Anything else in brackets stays as written, and tags are
lowercase only, as Discogs writes them: `[A123]` is text. So is a ref tag
whose id has more than 18 digits (`_MAX_ID_DIGITS`), longer than any
Discogs id: `int()` always converts 18 digits.

No part carries an escape code. A view neutralizes each part's text
(`sanitize.line()`) before it paints it, so Discogs data can neither inject
an escape sequence nor take a color. Every run is closed by construction:
an opening tag still open at the end of the field ends there, a closing tag
closes any tag opened inside it, and a closing tag with nothing to close is
dropped. A link inside a link is text, as a web page cannot nest them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from agent_discogs.refs import PREFIX_TO_TYPE, make_ref
from agent_discogs.style import ATTRS, Attr, Span, Styled

# A view line's part: text (which may hold Discogs values), a `Span` of CLI
# framing, or a `Styled` run of Discogs text under its markup's attributes.
Part = str | Span | Styled

# Every line break `str.splitlines()` knows; no tag spans one.
_BREAKS = "\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029"
_LINE_BREAK = re.compile(f"\r\n|[{_BREAKS}]")
_VALUE = rf"[^\[\]{_BREAKS}]+"
_MAX_ID_DIGITS = 18
_ID = rf"\d{{1,{_MAX_ID_DIGITS}}}"

_TAG = re.compile(
    r"\[(?:"
    rf"(?P<ref>[arml])(?P<ref_id>{_ID})"  # [a123]: an entity by ID
    rf"|(?P<title_ref>[rm])=(?P<title_ref_id>{_ID})"  # [r=123]: by ID, title only
    rf"|(?P<named>[al])=(?P<name>{_VALUE})"  # [a=Name]: by name
    rf"|url=(?P<href>{_VALUE})"
    r"|(?P<open>[bius]|url)"
    r"|/(?P<close>[bius]|url)"
    r"|g(?P<guideline>\d+(?:\.\d+)*)"  # [g1.2.3]: a database guideline
    r"|t=(?P<topic>\d+)"  # [t=262]: a forum topic
    r")\]"
)

_ATTR: dict[str, Attr] = {"b": "bold", "i": "italic", "u": "underline", "s": "strike"}


def plain(parts: list[Part]) -> str:
    """The parts' text, without styles."""
    return "".join(_text_of(p) for p in parts)


def parse(text: str) -> list[Part]:
    """`text` with its Discogs markup converted (see the module docstring),
    without leading or trailing whitespace."""
    parser = _Parser()
    pos = 0
    for match in _TAG.finditer(text):
        parser.text(text[pos : match.start()])
        pos = match.end()
        parser.tag(match)
    parser.text(text[pos:])
    return _trimmed(parser.finish())


def truncate(parts: list[Part], length: int = 300) -> list[Part]:
    """At most `length` characters of text, cut at the last space before the
    limit and ended with `...`. A ref is never cut: one the cut would split
    is dropped whole."""
    text = plain(parts)
    if len(text) <= length:
        return parts
    cut = len(text[:length].rsplit(" ", 1)[0])
    kept: list[Part] = []
    pos = 0
    for part in parts:
        size = len(_text_of(part))
        if pos + size > cut:
            if not isinstance(part, Span) and cut > pos:
                kept.append(_with_text(part, _text_of(part)[: cut - pos]))
            break
        kept.append(part)
        pos += size
    kept.append("...")
    return kept


def profile(text: str | None) -> list[Part]:
    """An artist or label profile, converted and truncated."""
    return truncate(parse(text or ""))


def split_lines(parts: list[Part]) -> list[list[Part]]:
    """The parts cut into lines at each line break, keeping each piece's
    styles: a run that spans lines is styled on each of them."""
    lines: list[list[Part]] = [[]]
    for part in parts:
        if isinstance(part, Span):
            lines[-1].append(part)
            continue
        for i, piece in enumerate(_LINE_BREAK.split(_text_of(part))):
            if i:
                lines.append([])
            if piece:
                lines[-1].append(_with_text(part, piece))
    return lines


@dataclass
class _Open:
    """A tag waiting for its closing tag. `start` indexes the output where
    its text begins."""

    tag: str
    start: int
    href: str | None = None


@dataclass
class _Parser:
    """One pass over a field: text is styled as it arrives, under the tags
    open at that point, so nesting depth costs nothing."""

    out: list[Part] = field(default_factory=list)
    stack: list[_Open] = field(default_factory=list)
    depth: dict[str, int] = field(
        default_factory=lambda: dict.fromkeys(("b", "i", "u", "s", "url"), 0)
    )
    # Text not yet in `out`, all under `pending_attrs`: adjacent text joins
    # into one part without rebuilding a string per piece.
    pending: list[str] = field(default_factory=list)
    pending_attrs: tuple[Attr, ...] = ()

    def text(self, text: str) -> None:
        if not text:
            return
        attrs = self._attrs()
        if attrs != self.pending_attrs:
            self._flush()
            self.pending_attrs = attrs
        self.pending.append(text)

    def tag(self, match: re.Match[str]) -> None:
        close = match["close"]
        opening = "url" if match["href"] is not None else match["open"]
        if close is not None:
            if self.depth[close]:  # else a stray closing tag: dropped
                self._close(close)
        elif opening == "url" and self.depth["url"]:
            self.text(match[0])
        elif opening is not None:
            if opening == "url":  # `start` must index the link's own text
                self._flush()
            href = match["href"]
            self.stack.append(_Open(opening, len(self.out), href and href.strip()))
            self.depth[opening] += 1
        else:
            atom = _atom(match)
            if isinstance(atom, Span):
                self._flush()
                self.out.append(atom)
            else:
                self.text(atom)

    def finish(self) -> list[Part]:
        self._close(None)
        self._flush()
        return self.out

    def _close(self, tag: str | None) -> None:
        """Close the open tags down to `tag`, or all of them."""
        while self.stack:
            frame = self.stack.pop()
            self.depth[frame.tag] -= 1
            if frame.tag == "url":
                self._end_link(frame)
            if frame.tag == tag:
                return

    def _end_link(self, frame: _Open) -> None:
        """`text (host)`, or the host alone when the link has no text."""
        self._flush()
        content = plain(self.out[frame.start :])
        host = _host(frame.href if frame.href is not None else content)
        if host is None:  # no site to show: the text stays as it is
            return
        if frame.href is None or not content.strip():
            del self.out[frame.start :]
            self.out.append(Styled(host, _with_attr(self._attrs(), "underline")))
        else:
            self.text(f" ({host})")

    def _attrs(self) -> tuple[Attr, ...]:
        on = {attr for tag, attr in _ATTR.items() if self.depth[tag]}
        if self.depth["url"]:
            on.add("underline")
        return tuple(a for a in ATTRS if a in on)

    def _flush(self) -> None:
        if not self.pending:
            return
        text = "".join(self.pending)
        self.pending.clear()
        attrs = self.pending_attrs
        self.out.append(Styled(text, attrs) if attrs else text)


def _atom(match: re.Match[str]) -> str | Span:
    """A tag that stands alone: a ref, a name, or a link's text."""
    prefix = match["ref"] or match["title_ref"]
    if prefix is not None:
        entity_id = int(match["ref_id"] or match["title_ref_id"])
        return Span(make_ref(PREFIX_TO_TYPE[prefix], entity_id), "ref")
    if match["named"] is not None:
        return match["name"].strip() or match[0]
    if match["guideline"] is not None:
        return f"RSG §{match['guideline']}"
    return f"Forum Topic #{match['topic']}"


def _host(url: str) -> str | None:
    """`discogs.com` for `https://www.discogs.com/label/1`; None when `url`
    names no host, so the link shows its text alone."""
    try:
        host = urlsplit(url.strip()).hostname
    except ValueError:
        return None
    return host.removeprefix("www.") if host else None


def _trimmed(parts: list[Part]) -> list[Part]:
    """`parts` without leading or trailing whitespace, which a tag that
    converts to nothing can leave behind."""
    kept = [
        i for i, p in enumerate(parts) if isinstance(p, Span) or _text_of(p).strip()
    ]
    if not kept:
        return []
    parts = parts[kept[0] : kept[-1] + 1]
    if not isinstance(first := parts[0], Span):
        parts[0] = _with_text(first, _text_of(first).lstrip())
    if not isinstance(last := parts[-1], Span):
        parts[-1] = _with_text(last, _text_of(last).rstrip())
    return parts


def _with_attr(attrs: tuple[Attr, ...], attr: Attr) -> tuple[Attr, ...]:
    return tuple(a for a in ATTRS if a in attrs or a == attr)


def _text_of(part: Part) -> str:
    return part if isinstance(part, str) else part.text


def _with_text(part: str | Styled, text: str) -> str | Styled:
    return text if isinstance(part, str) else part._replace(text=text)
