"""SDK Pydantic models → compact text output."""

from __future__ import annotations

import re
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agent_discogs import markup, sanitize, style
from agent_discogs.collection import is_release_row, stat_copies, stat_wanted
from agent_discogs.config import CACHE_TTL_LABEL
from agent_discogs.inventory import GRADES, release_id, short_grade
from agent_discogs.markup import Part, plain
from agent_discogs.refs import TYPE_TO_PREFIX, make_ref
from agent_discogs.style import Span

if TYPE_CHECKING:
    from datetime import datetime

    from discogs_sdk import AuthMode

    from agent_discogs.collection import Copies, Ownership
    from agent_discogs.inventory import Wants
    from agent_discogs.pagination import ScanStop
    from agent_discogs.wantlist import Market

# A view line is plain text, or a list of parts (`markup.Part`): text (which
# may hold Discogs values), `Span`s of CLI framing color may paint, and
# `Styled` runs of Discogs text under its markup's attributes.
Line = str | list[Part]


def _text(lines: list[Line]) -> str:
    """Join a view's lines. Each line is neutralized here, so a line break or
    escape sequence inside a Discogs field cannot leave its line. When stdout
    takes color, each `Span` and `Styled` run is painted after that."""
    color = style.enabled(sys.stdout)
    return "\n".join(_line(line, color=color) for line in lines)


def _line(line: Line, *, color: bool) -> str:
    """One neutralized line. Without color it is the plain text, neutralized
    as one string. With color, each run of text is neutralized on its own
    and each span or styled run painted around its neutralized text, so an
    escape code only ever comes from the CLI's fixed styles."""
    if isinstance(line, str):
        return sanitize.line(line)
    if not color:
        return sanitize.line(plain(line))
    out: list[str] = []
    run: list[str] = []
    for part in line:
        if isinstance(part, str):
            run.append(part)
            continue
        out.append(sanitize.line("".join(run)))
        run.clear()
        styles = (part.style,) if isinstance(part, Span) else part.attrs
        out.append(style.paint(sanitize.line(part.text), *styles, color=True))
    out.append(sanitize.line("".join(run)))
    return "".join(out)


def _ref(entity_type: str, entity_id: int) -> Span:
    return Span(make_ref(entity_type, entity_id), "ref")


def _label(name: str) -> Span:
    """A section label such as `Tracklist:`; never a name Discogs sent."""
    return Span(f"{name}:", "label")


def _joined(groups: Iterable[list[Part]], sep: str) -> list[Part]:
    """`sep.join()` for part lists."""
    parts: list[Part] = []
    for i, group in enumerate(groups):
        if i:
            parts.append(sep)
        parts.extend(group)
    return parts


def _scan_note(stop: ScanStop) -> str:
    """Why a client-side scan returned early, for the page counts."""
    if stop.reason == "rate_limited":
        wait = "" if stop.retry_after is None else f", retry in {stop.retry_after}s"
        return f"scan stopped: rate limited{wait}"
    return f"scan stopped at --max-requests {stop.max_requests}"


def _page_counts(
    page: int,
    shown: int,
    total: int | None,
    noun: str,
    *,
    filtered: bool,
    stop: ScanStop | None = None,
    header_resent: bool = False,
    note: str | None = None,
) -> str:
    """`(page 2, 5 of ≤23 results; scan stopped at --max-requests 3)`.

    `≤` marks a client-side filtered list whose total is the unfiltered API
    count; a scan note means the scan stopped early and the page may be
    short; `note` adds what the view counted besides. With no total, the
    scan read no listing page before `--max-requests` ran out, or a read the
    scan needed first was rate limited, so no page, count or bound is known
    yet. `header_resent` (`--fresh`): the header lookup that spent the
    budget will be sent again by the continuation, so it needs a higher cap.
    """
    if total is None and stop is not None and stop.reason == "rate_limited":
        wait = "" if stop.retry_after is None else f", retry in {stop.retry_after}s"
        return f"(rate limited before the scan started{wait})"
    if total is None:
        cap = "" if stop is None else f" {stop.max_requests}"
        again = (
            "; --fresh sends the header lookup again on every run, so continue "
            "with a higher --max-requests"
            if header_resent
            else ""
        )
        return f"(--max-requests{cap} ran out before the scan started{again})"
    bound = "≤" if filtered else ""
    stop_note = None if stop is None else _scan_note(stop)
    notes = "".join(f"; {n}" for n in (stop_note, note) if n)
    return f"(page {page}, {shown} of {bound}{total:,} {noun}{notes})"


def _footer(next_page_cmd: str | None, *, capped: bool) -> list[Line]:
    """Continuation footer. `Continue scan:` warns the next window may be short."""
    if not next_page_cmd:
        return []
    label = "Continue scan" if capped else "Next page"
    return ["", [Span(f"{label}:", "dim"), f" {next_page_cmd}"]]


def _artist_parts(artists: list[Any] | None) -> list[Part]:
    """Join artist credits into display parts, each with its ref.

    Refs are always shown: the artist is the most common next hop from a
    release, and `[@a3857]` costs three tokens.
    """
    if not artists:
        return ["Unknown Artist"]
    words: list[list[Part]] = []
    for a in artists:
        words.append([getattr(a, "name", None) or "Unknown"])
        artist_id = getattr(a, "id", None)
        if artist_id is not None:
            words.append(["[", _ref("artist", artist_id), "]"])
        join = getattr(a, "join", None)
        if join:
            words.append([join])
    return _joined(words, " ")


def _format_string(formats: list[Any] | None) -> str:
    """One entry per format, `;`-separated: `2xVinyl (LP, Album, 180g); CD (Album)`.

    A quantity above 1 prefixes the name, joined by a multiplication sign (`x`
    above). Descriptions and the free text (`180g, Gatefold`) go in
    parentheses, so entries never blur together.
    """
    entries = []
    for f in formats or []:
        name = getattr(f, "name", None) or ""
        quantity = str(getattr(f, "quantity", None) or "")
        if name and quantity not in ("", "0", "1"):
            name = f"{quantity}\u00d7{name}"  # multiplication sign
        details = list(getattr(f, "descriptions", None) or [])
        text = getattr(f, "text", None)
        if text:
            details.append(text)
        detail = ", ".join(details)
        if name and detail:
            entries.append(f"{name} ({detail})")
        elif name or detail:
            entries.append(name or detail)
    return "; ".join(entries)


def _genre_lines(entity: Any) -> list[Line]:
    """The `Genres:` and `Styles:` lines, entries `;`-separated.

    One genre is called `Folk, World, & Country`, so a comma cannot separate
    entries; `;` is also what the `Format:` line uses. An empty list prints
    no line.
    """
    lines: list[Line] = []
    for label, attr in (("Genres", "genres"), ("Styles", "styles")):
        values = getattr(entity, attr, None)
        if values:
            lines.append([_label(label), f" {'; '.join(values)}"])
    return lines


def _label_parts(labels: list[Any] | None) -> list[Part]:
    """First label name with its ref and catalog number."""
    if not labels:
        return []
    lbl = labels[0]
    parts: list[Part] = [getattr(lbl, "name", None) or ""]
    label_id = getattr(lbl, "id", None)
    if label_id is not None:
        parts += [" [", _ref("label", label_id), "]"]
    if catno := getattr(lbl, "catalog_number", None) or "":
        parts.append(f" ({catno})")
    return parts


_ROLE_SPLIT = re.compile(r",\s*(?![^\[]*\])")  # commas outside [bracketed] notes


def iter_tracks(tracklist: list[Any] | None) -> Iterator[Any]:
    """Every track in order, each index track followed by its sub-tracks."""
    for track in tracklist or []:
        yield track
        yield from iter_tracks(getattr(track, "sub_tracks", None))


@dataclass
class Credit:
    """One person under one role; empty `tracks` means the whole release."""

    id: int | None
    name: str | None
    tracks: list[str]

    @property
    def person(self) -> int | str | None:
        """Identity for merging: the artist ID, or the name when there is none."""
        return self.id or self.name


def _scoped_credits(release: Any) -> Iterator[tuple[Any, list[str]]]:
    """Each credit with the tracks it covers: release credits first, then tracks.

    A release credit's scope is its own `tracks` string, split on commas; a
    track or sub-track credit covers that track, named by position or title.
    """
    for credit in getattr(release, "extra_artists", None) or []:
        tracks = (getattr(credit, "tracks", None) or "").split(",")
        yield credit, [t.strip() for t in tracks if t.strip()]
    for track in iter_tracks(getattr(release, "tracklist", None)):
        scope = getattr(track, "position", None) or getattr(track, "title", None)
        for credit in getattr(track, "extra_artists", None) or []:
            yield credit, [scope or "?"]


def credits_by_role(release: Any) -> dict[str, list[Credit]]:
    """Invert Discogs credits from per-person to per-role, across all tracks.

    Discogs stores one entry per person with a comma-joined role string such
    as `Producer [Production], Written-By`, on the release and on each track
    and sub-track; both the text and JSON views group by role so "who
    produced this?" is one line or one key. A person appears once per role,
    with the union of the tracks credited; a release-wide credit absorbs the
    same credit on a track. Roles sort alphabetically; people keep first-seen
    order within a role.
    """
    by_role: dict[str, dict[int | str | None, Credit]] = {}
    for source, scope in _scoped_credits(release):
        credit_id = getattr(source, "id", None)
        name = getattr(source, "name", None)
        for role in _ROLE_SPLIT.split(getattr(source, "role", None) or ""):
            people = by_role.setdefault(role.strip() or "Other", {})
            fresh = Credit(credit_id, name, list(scope))
            known = people.setdefault(fresh.person, fresh)
            if known is fresh:
                continue
            if not scope:
                known.tracks = []
            elif known.tracks:
                known.tracks += [t for t in scope if t not in known.tracks]
    return {role: list(people.values()) for role, people in sorted(by_role.items())}


def format_credits(release: Any) -> str:
    """Release and track credits grouped by role, each person with an artist ref."""
    return _text(_credit_lines(release))


def _credit_lines(release: Any) -> list[Line]:
    by_role = credits_by_role(release)
    people = {c.person for entries in by_role.values() for c in entries}
    lines: list[Line] = [
        [
            _label("Credits"),
            " ",
            _ref("release", release.id),
            f' "{release.title}" ',
            Span(f"({len(people)})", "dim"),
        ],
        "",
    ]
    if not by_role:
        lines.append("  (no credits listed)")
        return lines

    for role, entries in by_role.items():
        names: list[list[Part]] = []
        for credit in entries:
            name: list[Part] = [credit.name or "Unknown"]
            if credit.id:
                name += [" [", _ref("artist", credit.id), "]"]
            if credit.tracks:
                name.append(f" ({', '.join(credit.tracks)})")
            names.append(name)
        # The role is a Discogs value, so it is not painted as a label.
        lines.append([f"{role}: ", *_joined(names, ", ")])
    return lines


def format_identifiers(release: Any) -> str:
    """Barcodes, matrix/runout, and other identifiers: what tells pressings apart."""
    return _text(_identifier_lines(release))


def _identifier_lines(release: Any) -> list[Line]:
    identifiers = getattr(release, "identifiers", None) or []
    lines: list[Line] = [
        [
            _label("Identifiers"),
            " ",
            _ref("release", release.id),
            f' "{release.title}"',
        ],
        "",
    ]
    if not identifiers:
        lines.append("  (no identifiers listed)")
        return lines
    for ident in identifiers:
        desc = getattr(ident, "description", None)
        suffix = f" ({desc})" if desc else ""
        lines.append(
            f"{getattr(ident, 'type', None) or 'Other'}: "
            f"{getattr(ident, 'value', None) or ''}{suffix}"
        )
    return lines


def _track_artist_prefix(track: Any) -> list[Part]:
    """Return 'Artist - ' prefix for a track, or nothing if no track-level artists."""
    artists = getattr(track, "artists", None)
    if not artists:
        return []
    return [*_artist_parts(artists), " - "]


def _format_track_lines(tracklist: list[Any], depth: int = 1) -> list[Line]:
    """Format a tracklist into display lines with optional per-track artists.

    An index track (a suite or medley) lists its parts one level deeper.
    """
    indent = "  " * depth
    lines: list[Line] = []
    for track in tracklist:
        pos = getattr(track, "position", "") or ""
        title = getattr(track, "title", "") or ""
        dur = getattr(track, "duration", "") or ""
        if getattr(track, "type", None) == "heading":
            lines.append(f"{indent}{title}")
            continue
        dur_str = f" ({dur})" if dur else ""
        number = f"{pos}. " if pos else ""
        lines.append(
            [f"{indent}{number}", *_track_artist_prefix(track), f"{title}{dur_str}"]
        )
        sub_tracks = getattr(track, "sub_tracks", None) or []
        lines.extend(_format_track_lines(sub_tracks, depth + 1))
    return lines


def _ref_type(entity: Any) -> str | None:
    """The entity type of a search result or list item if it has a ref
    prefix, else None.

    Discogs may send any type string, or none. Only a known type says which
    kind of ID `entity.id` is, so anything else gets no ref.
    """
    return entity.type if entity.type in TYPE_TO_PREFIX else None


def _community_have(obj: Any) -> int | None:
    """Community 'have' count from a search result or master version, if present."""
    community = getattr(obj, "community", None)
    if community is None:
        stats = getattr(obj, "stats", None)
        community = getattr(stats, "community", None)
    if community is None:
        return None
    have = getattr(community, "have", None)
    if have is None:
        have = getattr(community, "in_collection", None)
    return have if isinstance(have, int) else None


def known_year(year: int | str | None) -> int | str | None:
    """The year, or None for Discogs' `0` / `"0"` meaning unknown."""
    return year if year and str(year).strip("0") else None


_UNKNOWN_DATE_PARTS = re.compile(r"(?:-00)+$")


def release_date(released: str | None) -> str | None:
    """`1994-05-00` → `1994-05`, `1994-00-00` → `1994`; None for an unknown year.

    Discogs pads unknown month and day with `-00`; shown as-is they read as a
    date, and a bare year would not match the release year it repeats.
    """
    if not released:
        return None
    date = _UNKNOWN_DATE_PARTS.sub("", released)
    return date if known_year(date.split("-", 1)[0]) else None


def _release_row(
    *,
    ref: Part,
    type_: str | None = None,
    title: str | None = None,
    artist: str | None = None,
    year: int | str | None = None,
    country: str | None = None,
    label: str | None = None,
    catno: str | None = None,
    fmt: str | None = None,
    role: str | None = None,
    have: int | None = None,
    folder: str | None = None,
    added: str | None = None,
    rating: int | None = None,
    master_ref: Span | None = None,
    notes: str | None = None,
    owned: Span | None = None,
    wanted: Span | None = None,
    tail: list[Part] | None = None,
) -> list[Part]:
    """One list row: everything an agent needs to pick or navigate, nothing else.

    Shared by search, versions, discography, collection and wantlist views so
    a release looks the same wherever it appears. Every part is optional and
    omitted when empty; `tail` ends the row (a want's Marketplace stats).
    """
    words: list[list[Part]] = [[ref]]
    if type_:
        words.append([f"[{type_}]"])
    if title:
        words.append([f'"{title}"'])
    if artist:
        words.append([f"by {artist}"])
    meta = " ".join(str(p) for p in (known_year(year), country) if p)
    if meta:
        words.append([meta])
    if label or catno:
        words.append(["· " + " ".join(p for p in (label, catno) if p)])
    if fmt:
        words.append([f"· {fmt}"])
    if role:
        words.append([f"· {role}"])
    if have is not None:
        words.append([f"· have {have:,}"])
    if folder:
        words.append([f"· folder {folder}"])
    if added:
        words.append([f"· added {added}"])
    if rating:
        words.append([f"· rating {rating}"])
    if master_ref:
        words.append(["→ ", master_ref])
    if notes:
        words.append([f"· notes: {notes}"])
    if owned:
        words.append([owned])
    if wanted:
        words.append([wanted])
    if tail:
        words.append(tail)
    return _joined(words, " ")


# Between `owned` and the copy count, as between a format's quantity and name.
_TIMES = "\u00d7"  # multiplication sign


def _copies(count: int) -> str:
    """`owned` for one copy; for more, the count after a multiplication sign."""
    return "owned" if count == 1 else f"owned {_TIMES}{count}"


def _owned(ownership: Ownership | None, row: Any) -> Span | None:
    """`· owned` (one copy, or with the count for more), `· not owned`, or
    `· owned ?` (not in the part of the collection read) for a release row
    under `--owned`; nothing otherwise."""
    if ownership is None or not is_release_row(row):
        return None
    return _owned_mark(ownership.owned(row.id))


def _stat_owned(owned: bool, row: Any) -> Span | None:
    """The `--owned` mark of a `get versions` or `get releases` release row,
    from the count its own stats carry (`owned ?` without one); nothing
    otherwise."""
    if not owned or not is_release_row(row):
        return None
    return _owned_mark(stat_copies(row))


def _owned_mark(copies: int | None) -> Span:
    """The `--owned` mark for a release with `copies` (None: undecided)."""
    if copies is None:
        return Span("· owned ?", "dim")
    return Span(f"· {_copies(copies)}" if copies else "· not owned", "dim")


def _stopped(stop: ScanStop) -> str:
    """Why a read of the user's own list stopped short, after `the read`."""
    if stop.reason == "rate_limited":
        wait = "" if stop.retry_after is None else f", retry in {stop.retry_after}s"
        return f"was rate limited{wait}"
    return f"stopped at --max-requests {stop.max_requests}"


def _ownership_note(ownership: Ownership | None) -> list[Line]:
    """Why `owned ?` rows are undecided: the collection read stopped short."""
    if ownership is None or ownership.stop is None:
        return []
    total = (
        "" if ownership.total_records is None else f" of {ownership.total_records:,}"
    )
    read = f"{ownership.records_read:,}{total} records read"
    return [
        "",
        [
            _label("Owned"),
            (
                f" the collection read {_stopped(ownership.stop)} ({read}): "
                '"owned ?" rows are not in the part read, and a count may be short.'
            ),
        ],
    ]


def _wants_mark(wants: Wants | None, row: Any) -> Span | None:
    """The `search --wanted` mark of a release row, from the wantlist read;
    nothing otherwise."""
    if wants is None or not is_release_row(row):
        return None
    return _wanted_mark(wants.wanted(row.id))


def _stat_wanted(wanted: bool, row: Any) -> Span | None:
    """The `--wanted` mark of a `get versions` or `get releases` release row,
    from the want count its own stats carry (`wanted ?` without one);
    nothing otherwise."""
    if not wanted or not is_release_row(row):
        return None
    return _wanted_mark(stat_wanted(row))


def _wanted_mark(wanted: bool | None) -> Span:
    """`· wanted`, `· not wanted`, or `· wanted ?` (undecided)."""
    if wanted is None:
        return Span("· wanted ?", "dim")
    return Span("· wanted" if wanted else "· not wanted", "dim")


def _wanted_note(wants: Wants | None) -> list[Line]:
    """Why `wanted ?` rows are undecided: the wantlist read stopped short."""
    if wants is None or wants.stop is None:
        return []
    total = "" if wants.total is None else f" of {wants.total:,}"
    return [
        "",
        [
            _label("Wantlist"),
            (
                f" the read {_stopped(wants.stop)} ({wants.read:,}{total} wants "
                'read): "wanted ?" rows are not in the part read.'
            ),
        ],
    ]


def format_owned(release_id: int, copies: Copies) -> str:
    """`get owned`: `@r123 owned` (with the count for more than one copy) and
    the folder names (`: Vinyl, Uncategorized`), or `@r123 not owned`. A copy
    in no folder Discogs names adds no name."""
    if not copies.count:
        return _text([[_ref("release", release_id), " not owned"]])
    folders = ", ".join(copies.folders)
    owned = f" {_copies(copies.count)}" + (f": {folders}" if folders else "")
    return _text([[_ref("release", release_id), owned]])


def _market_tail(market: Market) -> list[Part]:
    """`· for sale: 3 from EUR 12.50`; the amount is Discogs', not framing."""
    lowest = _price(market.lowest)
    sale = f" {market.for_sale:,}" + (f" from {lowest}" if lowest else "")
    return [Span("· for sale:", "dim"), sale]


def format_wantlist(
    wants: list[Any],
    username: str,
    page: int,
    total_results: int | None,
    next_page_cmd: str | None,
    *,
    filters: dict[str, str] | None = None,
    filtered: bool = False,
    stop: ScanStop | None = None,
    header_resent: bool = False,
    markets: dict[int, Market] | None = None,
    misses: dict[int, str] | None = None,
) -> str:
    """`get wantlist`: one row per want, the release titled like a search
    result, with the want's notes. Under `--for-sale` (`filtered`), each row
    ends in its Marketplace stats (`markets`), or in the `--cached` miss that
    left it undecided (`misses`, by release id, the error message)."""
    counts = _page_counts(
        page,
        len(wants),
        total_results,
        "wants",
        filtered=filtered,
        stop=stop,
        header_resent=header_resent,
    )
    subject = " ".join(
        p
        for p in (username, "for sale" if filtered else "", _format_filters(filters))
        if p
    )
    lines: list[Line] = [[_label("Wantlist"), f" {subject} ", Span(counts, "dim")], ""]
    markets = markets or {}
    misses = misses or {}
    for want in wants:
        release = getattr(want, "basic_information", None)
        labels = getattr(release, "labels", None) or []
        first = labels[0] if labels else None
        if want.id in misses:
            tail: list[Part] | None = [Span("· ✗", "error"), f" {misses[want.id]}"]
        elif want.id in markets:
            tail = _market_tail(markets[want.id])
        else:
            tail = None
        lines.append(
            _release_row(
                ref=_ref("release", want.id),
                title=artist_title(release),
                year=getattr(release, "year", None),
                label=getattr(first, "name", None),
                catno=getattr(first, "catalog_number", None),
                fmt=_format_string(getattr(release, "formats", None)) or None,
                notes=getattr(want, "notes", None),
                tail=tail,
            )
        )
    if misses:
        lines += ["", "Rerun without --cached to check the wants marked ✗."]
    lines.extend(_footer(next_page_cmd, capped=stop is not None))
    return _text(lines)


def _visibility(public: bool | None) -> str | None:
    """`public` or `private`; nothing when Discogs did not say."""
    if public is None:
        return None
    return "public" if public else "private"


def format_lists(
    lists: list[Any],
    username: str,
    page: int,
    total_results: int | None,
    next_page_cmd: str | None,
) -> str:
    """`get lists`: one row per list, its id, name, visibility and the UTC
    day it last changed, ending in the command that opens it. Discogs sends
    no item count here."""
    counts = _page_counts(page, len(lists), total_results, "lists", filtered=False)
    lines: list[Line] = [[_label("Lists"), f" {username} ", Span(counts, "dim")], ""]
    for summary in lists:
        words = [f'{summary.id} "{summary.name}"']
        visibility = _visibility(summary.public)
        if visibility:
            words.append(f"· {visibility}")
        day = _utc_day(summary.date_changed)
        if day:
            words.append(f"· changed {day}")
        words.append(f"→ get list {summary.id}")
        lines.append(" ".join(words))
    lines.extend(_footer(next_page_cmd, capped=False))
    return _text(lines)


def format_list(found: Any) -> str:
    """`get list`: the list's name, visibility and whole description (markup
    converted, never truncated), then one row per item, its ref and title
    with its comment. An item of a type with no ref prefix starts `id N`
    and shows its type, as a search row does."""
    visibility = _visibility(found.public)
    lines: list[Line] = [
        [
            _label("List"),
            f' {found.id} "{found.name}"',
            f" · {visibility}" if visibility else "",
        ]
    ]
    lines.extend(_paragraph("Description", markup.parse(found.description or "")))
    lines.append("")
    items = found.items or []
    if not items:
        lines.append("No items.")
    for item in items:
        item_type = _ref_type(item)
        comment: list[Part] | None = (
            [f"· comment: {item.comment}"] if item.comment else None
        )
        if item_type is None:
            ref: Part = f"id {item.id}"
            type_: str | None = item.type or "no type"
        else:
            ref = _ref(item_type, item.id)
            type_ = None
        lines.append(
            _release_row(ref=ref, type_=type_, title=item.display_title, tail=comment)
        )
    return _text(lines)


def format_collection(
    records: list[Any],
    username: str,
    page: int,
    total_results: int | None,
    next_page_cmd: str | None,
    *,
    folders: dict[int, str],
    filters: dict[str, str] | None = None,
    filtered: bool = False,
    stop: ScanStop | None = None,
    header_resent: bool = False,
) -> str:
    """`get collection`: one row per copy, the release titled like a search
    result, then the copy's folder by name (`folders`), the day it was added
    and its rating when set. A filter makes it a scan (`filtered`)."""
    counts = _page_counts(
        page,
        len(records),
        total_results,
        "records",
        filtered=filtered,
        stop=stop,
        header_resent=header_resent,
    )
    subject = " ".join(p for p in (username, _format_filters(filters)) if p)
    lines: list[Line] = [
        [_label("Collection"), f" {subject} ", Span(counts, "dim")],
        "",
    ]
    for record in records:
        release = getattr(record, "basic_information", None)
        labels = getattr(release, "labels", None) or []
        first = labels[0] if labels else None
        folder_id = getattr(record, "folder_id", None)
        added = getattr(record, "date_added", None)
        lines.append(
            _release_row(
                ref=_ref("release", record.id),
                title=artist_title(release),
                year=getattr(release, "year", None),
                label=getattr(first, "name", None),
                catno=getattr(first, "catalog_number", None),
                fmt=_format_string(getattr(release, "formats", None)) or None,
                # `· folder 99` for a folder the list does not name.
                folder=None
                if folder_id is None
                else folders.get(folder_id, str(folder_id)),
                # The day in the timestamp's own offset, as `get release` and
                # the JSON `added` beside it give it.
                added=added.date().isoformat() if added else None,
                rating=getattr(record, "rating", None),
            )
        )
    lines.extend(_footer(next_page_cmd, capped=stop is not None))
    return _text(lines)


def format_collection_value(currency: str, amounts: dict[str, float]) -> str:
    """`get collection --value`: `Collection value (EUR): min 75.50 · median
    100.25 · max 250.00`, bare numbers after the ISO code, never a symbol."""
    words = {"minimum": "min", "median": "median", "maximum": "max"}
    values = " · ".join(f"{words[name]} {value:.2f}" for name, value in amounts.items())
    return _text([[Span("Collection value", "label"), f" ({currency}): {values}"]])


def listing_title(release: Any) -> str | None:
    """`Artist - Title` of a listing's release, or whichever of the two
    Discogs sent."""
    names = [getattr(release, "artist", None), getattr(release, "title", None)]
    return " - ".join(name for name in names if name) or None


def listing_grades(listing: Any) -> str | None:
    """`NM/VG+`: the media grade, then the sleeve's (`?` for a media grade
    Discogs did not send), each as its short code; a sleeve that is no grade
    (`Generic`) as sent."""
    media = short_grade(listing.condition)
    sleeve = short_grade(listing.sleeve_condition)
    if sleeve is None:
        return media
    return f"{media or '?'}/{sleeve}"


def _listing_row(listing: Any, ownership: Ownership | None) -> list[Part]:
    """A listing: its release (`listing N` when Discogs sent no release id),
    grades, price, origin and listing day, then the `--owned` mark."""
    release = listing.release
    rid = release_id(listing)
    posted = listing.posted
    words = [
        listing_grades(listing),
        _price(listing.price),
        listing.ships_from and f"ships from {listing.ships_from}",
        posted and f"listed {posted.date().isoformat()}",
    ]
    tail: list[list[Part]] = [[f"· {word}"] for word in words if word]
    if ownership is not None:
        tail.append([_owned_mark(ownership.owned(rid))])
    return _release_row(
        ref=f"listing {listing.id}" if rid is None else _ref("release", rid),
        title=listing_title(release),
        fmt=getattr(release, "format", None),
        tail=_joined(tail, " "),
    )


def _wants_note(wants: Wants | None, *, resent: bool) -> list[Line]:
    """Why an `--in-wantlist` page is empty: the wantlist read stopped short,
    so no listing could be judged (and the page has no row). `resent`
    (`--fresh`): the continuation reads the whole wantlist again, so only a
    higher cap moves it on."""
    if wants is None or wants.stop is None:
        return []
    stop = wants.stop
    again = ""
    if stop.reason != "rate_limited" and resent:
        again = (
            " --fresh reads it again on every run, so continue with a higher "
            "--max-requests."
        )
    total = "" if wants.total is None else f" of {wants.total:,}"
    return [
        [
            _label("Wantlist"),
            (
                f" the read {_stopped(stop)} ({wants.read:,}{total} wants read), "
                "so no "
                "listing was scanned: a listing is kept only against the whole "
                f"wantlist.{again}"
            ),
        ],
    ]


def format_inventory(
    listings: list[Any],
    username: str,
    page: int,
    total_results: int | None,
    next_page_cmd: str | None,
    *,
    condition: str | None,
    sleeve: str | None,
    filters: dict[str, str],
    in_wantlist: bool,
    filtered: bool,
    stop: ScanStop | None,
    skipped_currency: int,
    wants: Wants | None,
    wants_resent: bool,
    ownership: Ownership | None,
) -> str:
    """`get inventory`: one row per listing of `username`, under a header
    naming the filters, the minimum grades (short codes) as
    `condition≥NM sleeve≥VG+`, with how many listings priced in another
    currency were skipped."""
    skipped = None
    if skipped_currency:
        where = "another currency" if skipped_currency == 1 else "other currencies"
        noun = "listing" if skipped_currency == 1 else "listings"
        skipped = f"{skipped_currency:,} {noun} in {where} skipped"
    counts = _page_counts(
        page,
        len(listings),
        total_results,
        "listings",
        filtered=filtered,
        stop=stop,
        note=skipped,
    )
    grades = [
        f"{name}≥{code}"
        for name, code in (("condition", condition), ("sleeve", sleeve))
        if code
    ]
    subject = " ".join(
        p
        for p in (
            username,
            "in wantlist" if in_wantlist else "",
            *grades,
            _format_filters(filters),
        )
        if p
    )
    lines: list[Line] = [[_label("Inventory"), f" {subject} ", Span(counts, "dim")], ""]
    lines += _wants_note(wants, resent=wants_resent)
    lines += [_listing_row(listing, ownership) for listing in listings]
    lines.extend(_ownership_note(ownership))
    lines.extend(_footer(next_page_cmd, capped=stop is not None))
    return _text(lines)


def _format_filters(filters: dict[str, str] | None) -> str:
    """Render applied filters for a list header: `year=1994 label="R & S"`."""
    if not filters:
        return ""
    parts = []
    for key, value in filters.items():
        shown = f'"{value}"' if " " in value else value
        parts.append(f"{key}={shown}")
    return " ".join(parts)


def _urls_short(urls: list[str] | None) -> str:
    """Extract domain names from URLs for compact display."""
    if not urls:
        return ""
    domains = []
    for url in urls:
        parts = url.split("//", 1)
        domain = parts[1].split("/", 1)[0] if len(parts) > 1 else parts[0]
        domain = domain.removeprefix("www.")
        if domain:  # an empty URL (or a bare "https://") names no site
            domains.append(domain)
    return ", ".join(domains)


def split_members(artist: Any) -> tuple[list[Any], list[Any]]:
    """(members, former): only `active is False` is former; unknown (None) is not."""
    members = getattr(artist, "members", None) or []
    former = [m for m in members if getattr(m, "active", None) is False]
    current = [m for m in members if getattr(m, "active", None) is not False]
    return current, former


def _profile(text: str | None) -> list[Line]:
    """`Profile: ...`: the markup converted, truncated, on one line."""
    parts = markup.profile(text)
    if not plain(parts):
        return []
    return [[_label("Profile"), " ", *_joined(markup.split_lines(parts), " ")]]


def _paragraph(label: str, parts: list[Part]) -> list[Line]:
    """`Notes: ...`: converted markup kept whole, its line breaks kept. The
    continuation lines are indented: nothing in the text may start a line
    at column 0, where it could pass for CLI framing."""
    if not plain(parts):
        return []
    first, *rest = markup.split_lines(parts)
    return [
        [_label(label), " ", *first],
        *(["  ", *part] if plain(part).strip() else "" for part in rest),
    ]


def format_artist(artist: Any) -> str:
    """Format an artist profile view."""
    lines: list[Line] = [
        [_ref("artist", artist.id), f' [artist] "{artist.name}"'],
    ]

    lines.extend(_profile(getattr(artist, "profile", None)))

    urls = _urls_short(getattr(artist, "urls", None))
    if urls:
        lines.append([_label("URLs"), f" {urls}"])

    current, former = split_members(artist)
    if current:
        lines.append([_label("Members"), " ", *named_refs(current, "artist")])
    if former:
        lines.append([_label("Former"), " ", *named_refs(former, "artist")])

    return _text(lines)


def field(obj: Any, name: str) -> Any:
    """Attribute or dict key: the SDK leaves untyped API fields as raw dicts."""
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def named_refs(items: list[Any], entity_type: str) -> list[Part]:
    """`Name [@a123], Other [@a456]`: every navigable name carries its ref."""
    names: list[list[Part]] = []
    for item in items:
        name = field(item, "name") or "Unknown"
        item_id = field(item, "id")
        names.append(
            [name, " [", _ref(entity_type, item_id), "]"] if item_id else [name]
        )
    return _joined(names, ", ")


def format_label_releases(
    releases: list[Any],
    label_ref: str,
    label_name: str,
    page: int,
    total_results: int | None,
    next_page_cmd: str | None,
    *,
    filtered: bool = False,
    stop: ScanStop | None = None,
    header_resent: bool = False,
    owned: bool = False,
    wanted: bool = False,
) -> str:
    """Format a label's catalogue (client-side `--year` scans are `filtered`)."""
    counts = _page_counts(
        page,
        len(releases),
        total_results,
        "results",
        filtered=filtered,
        stop=stop,
        header_resent=header_resent,
    )
    lines: list[Line] = [
        [
            "Releases on ",
            Span(label_ref, "ref"),
            f' "{label_name}" ',
            Span(counts, "dim"),
        ],
        "",
    ]
    lines.extend(
        _release_row(
            ref=_ref("release", rel.id),
            title=rel.title,
            artist=getattr(rel, "artist", None),
            year=getattr(rel, "year", None),
            catno=getattr(rel, "catalog_number", None),
            fmt=getattr(rel, "format", None),
            owned=_stat_owned(owned, rel),
            wanted=_stat_wanted(wanted, rel),
        )
        for rel in releases
    )
    lines.extend(_footer(next_page_cmd, capped=stop is not None))
    return _text(lines)


def format_artist_releases(
    releases: list[Any],
    artist_ref: str,
    artist_name: str,
    page: int,
    total_results: int | None,
    next_page_cmd: str | None,
    *,
    filtered: bool = False,
    stop: ScanStop | None = None,
    header_resent: bool = False,
    owned: bool = False,
    wanted: bool = False,
) -> str:
    """Format artist releases/discography."""
    counts = _page_counts(
        page,
        len(releases),
        total_results,
        "results",
        filtered=filtered,
        stop=stop,
        header_resent=header_resent,
    )
    lines: list[Line] = [
        [
            "Releases by ",
            Span(artist_ref, "ref"),
            f' "{artist_name}" ',
            Span(counts, "dim"),
        ],
        "",
    ]

    lines.extend(
        _release_row(
            ref=_ref(getattr(rel, "type", "release"), rel.id),
            type_=getattr(rel, "type", "release"),
            title=rel.title,
            year=getattr(rel, "year", None),
            label=getattr(rel, "label", None),
            fmt=getattr(rel, "format", None),
            role=getattr(rel, "role", None),
            owned=_stat_owned(owned, rel),
            wanted=_stat_wanted(wanted, rel),
        )
        for rel in releases
    )
    lines.extend(_footer(next_page_cmd, capped=stop is not None))
    return _text(lines)


def format_label(label: Any) -> str:
    """Format a label profile view."""
    lines: list[Line] = [[_ref("label", label.id), f' [label] "{label.name}"']]

    lines.extend(_profile(getattr(label, "profile", None)))

    urls = _urls_short(getattr(label, "urls", None))
    if urls:
        lines.append([_label("URLs"), f" {urls}"])

    parent = getattr(label, "parent_label", None)
    if parent:
        lines.append([_label("Parent"), " ", *named_refs([parent], "label")])

    sub_labels = getattr(label, "sub_labels", None)
    if sub_labels:
        lines.append([_label("Sub-labels"), " ", *named_refs(sub_labels, "label")])

    return _text(lines)


def _market(num_for_sale: int, lowest_price: float | None) -> list[Part]:
    """`Market: 9 for sale from 9.36`, a bare amount in the buyer currency."""
    sale = f" {num_for_sale:,} for sale"
    if lowest_price is not None:
        sale += f" from {lowest_price:.2f}"
    return [_label("Market"), sale]


def format_master(master: Any) -> str:
    """Format a master release detail view."""
    artists = _artist_parts(getattr(master, "artists", None))
    year = known_year(getattr(master, "year", None)) or ""
    year_str = f" ({year})" if year else ""

    lines: list[Line] = [
        [
            _ref("master", master.id),
            f' [master] "{master.title}" by ',
            *artists,
            year_str,
        ]
    ]

    lines.extend(_genre_lines(master))

    main_release = getattr(master, "main_release", None)
    if main_release:
        lines.append([_label("Main release"), " ", _ref("release", main_release)])

    num_for_sale = getattr(master, "num_for_sale", None)
    if num_for_sale is not None:
        lines.append(_market(num_for_sale, getattr(master, "lowest_price", None)))

    tracklist = getattr(master, "tracklist", None)
    if tracklist:
        lines.append("")
        lines.append([_label("Tracklist")])
        lines.extend(_format_track_lines(tracklist))

    return _text(lines)


def format_master_versions(
    versions: list[Any],
    master_ref: str,
    master_title: str,
    page: int,
    total_results: int | None,
    next_page_cmd: str | None,
    *,
    filtered: bool = False,
    stop: ScanStop | None = None,
    header_resent: bool = False,
    owned: bool = False,
    wanted: bool = False,
) -> str:
    """Format master release versions (`--release-type` scans are `filtered`)."""
    counts = _page_counts(
        page,
        len(versions),
        total_results,
        "versions",
        filtered=filtered,
        stop=stop,
        header_resent=header_resent,
    )
    lines: list[Line] = [
        [
            "Versions of ",
            Span(master_ref, "ref"),
            f' "{master_title}" ',
            Span(counts, "dim"),
        ],
        "",
    ]

    lines.extend(
        _release_row(
            ref=_ref("release", ver.id),
            year=release_date(getattr(ver, "released", None)),
            country=getattr(ver, "country", None),
            label=getattr(ver, "label", None),
            catno=getattr(ver, "catalog_number", None),
            fmt=getattr(ver, "format", None),
            have=_community_have(ver),
            owned=_stat_owned(owned, ver),
            wanted=_stat_wanted(wanted, ver),
        )
        for ver in versions
    )
    lines.extend(_footer(next_page_cmd, capped=stop is not None))
    return _text(lines)


def _price(price: Any) -> str | None:
    """`EUR 20.00`: an SDK `Price` with the ISO code Discogs sent, never a symbol.

    A dollar sign would be ambiguous (USD, CAD, AUD) and wrong for anything else.
    None when the price carries no value.
    """
    value = getattr(price, "value", None)
    if value is None:
        return None
    currency = getattr(price, "currency", None)
    return f"{currency} {value:.2f}" if currency else f"{value:.2f}"


def format_price_guide(
    release: Any,
    price_suggestions: Any,
    marketplace_stats: Any,
) -> str:
    """Format price guide for a release."""
    artists = _artist_parts(getattr(release, "artists", None))
    year = getattr(release, "year", None) or ""
    year_str = f" ({year})" if year else ""

    lines: list[Line] = [
        [
            _label("Price Guide"),
            " ",
            _ref("release", release.id),
            f' "{release.title}" by ',
            *artists,
            year_str,
        ],
        "",
    ]

    conditions: Any = getattr(price_suggestions, "conditions", None)

    if conditions:
        for cond in GRADES:
            if cond in conditions:
                amount = _price(conditions[cond])
                if amount is not None:
                    name = f"{cond}:"  # one of the names above, never Discogs'
                    pad = " " * (30 - len(name))
                    lines.append([Span(name, "label"), f"{pad} {amount}"])
    else:
        lines.append("  No price suggestions available")

    lines.append("")

    num_for_sale = getattr(marketplace_stats, "num_for_sale", None)
    lowest = _price(getattr(marketplace_stats, "lowest_price", None))
    market: list[list[Part]] = []
    if num_for_sale is not None:
        market.append([_label("For sale"), f" {num_for_sale:,}"])
    if lowest is not None:
        market.append([_label("Lowest"), f" {lowest}"])
    if market:
        lines.append(_joined(market, " · "))

    return _text(lines)


def _duration_seconds(duration: str | None) -> int | None:
    """`4:30` or `1:02:03` in seconds; None for a blank or malformed duration."""
    parts = (duration or "").split(":")
    if not all(p.isdigit() for p in parts):
        return None
    seconds = 0
    for part in parts:
        seconds = seconds * 60 + int(part)
    return seconds


def _leaf_timing(tracklist: list[Any]) -> tuple[int, int | None]:
    """`(leaf track count, summed seconds)`; seconds is None if any leaf is untimed.

    Headings are skipped. An index track counts as its sub-tracks and sums
    their durations; when a sub-track is untimed, the index's own duration
    stands in for the group, since Discogs often times only the whole work.
    """
    count = 0
    total: int | None = 0
    for track in tracklist:
        if (getattr(track, "type", None) or "track") == "heading":
            continue
        own = _duration_seconds(getattr(track, "duration", None))
        sub_tracks = getattr(track, "sub_tracks", None) or []
        leaves, seconds = _leaf_timing(sub_tracks) if sub_tracks else (0, None)
        if not leaves:
            leaves, seconds = 1, own
        elif seconds is None:
            seconds = own
        count += leaves
        total = None if total is None or seconds is None else total + seconds
    return count, total


def track_summary(tracklist: list[Any]) -> str:
    """`14 (65:01)`: track count and summed duration, for compact views.

    Headings are not counted, and an index track counts as its sub-tracks
    (see `_leaf_timing`). The total is shown only when every counted track has
    a valid duration; a partial sum would read as the whole release's running
    time, so a single blank duration drops it to the bare count.
    """
    count, seconds = _leaf_timing(tracklist)
    if not count:
        return "0"
    if seconds is None:
        return str(count)
    minutes, secs = divmod(seconds, 60)
    return f"{count} ({minutes}:{secs:02d})"


def format_release(
    release: Any,
    *,
    verbose: bool = False,
    compact: bool = False,
    master_year: int | str | None = None,
    master_error: str | None = None,
) -> str:
    """Format a full release detail view.

    `verbose` appends notes, credits, and identifiers: the facets that matter
    when identifying a pressing or asking who worked on it. `compact` replaces
    the tracklist with a one-line `Tracks:` summary; `tracks @r...` is the
    dedicated tracklist view. Under `--master-year`, the `Master:` line ends in
    the master's year (`master_year`), or in why it was not read
    (`master_error`, a message).
    """
    artists = _artist_parts(getattr(release, "artists", None))
    year = known_year(getattr(release, "year", None)) or ""
    year_str = f" ({year})" if year else ""

    lines: list[Line] = [
        [
            _ref("release", release.id),
            f' [release] "{release.title}" by ',
            *artists,
            year_str,
        ]
    ]

    label = _label_parts(getattr(release, "labels", None))
    if plain(label):
        lines.append([_label("Label"), " ", *label])

    fmt = _format_string(getattr(release, "formats", None))
    if fmt:
        lines.append([_label("Format"), f" {fmt}"])

    country = getattr(release, "country", None)
    released = release_date(getattr(release, "released", None))
    # `released` repeats the header year unless it carries a month/day.
    origin: list[list[Part]] = []
    if country:
        origin.append([_label("Country"), f" {country}"])
    if released and released != str(year):
        origin.append([_label("Released"), f" {released}"])
    added = getattr(release, "date_added", None)
    if added:
        # The day in the timestamp's own offset, not converted to UTC or local.
        origin.append([_label("Added"), f" {added.date().isoformat()}"])
    if origin:
        lines.append(_joined(origin, " · "))

    lines.extend(_genre_lines(release))

    community = getattr(release, "community", None)
    if community:
        rating = getattr(community, "rating", None)
        if rating:
            avg = getattr(rating, "average", None)
            count = getattr(rating, "count", None)
            if avg is not None and count is not None:
                lines.append([_label("Rating"), f" {avg:.2f}/5 ({count:,} votes)"])

        have = getattr(community, "have", None)
        want = getattr(community, "want", None)
        if have is not None and want is not None:
            lines.append(
                [_label("Have"), f" {have:,} · ", _label("Want"), f" {want:,}"]
            )

    num_for_sale = getattr(release, "num_for_sale", None)
    if num_for_sale is not None:
        lines.append(_market(num_for_sale, getattr(release, "lowest_price", None)))

    master_id = getattr(release, "master_id", None)
    if master_id:
        master: list[Part] = [_label("Master"), " ", _ref("master", master_id)]
        if master_error:
            master += [" ", Span("· ✗", "error"), f" {master_error}"]
        elif master_year:
            master.append(f" ({master_year})")
        lines.append(master)

    if verbose:
        lines.extend(
            _paragraph("Notes", markup.parse(getattr(release, "notes", None) or ""))
        )

    tracklist = getattr(release, "tracklist", None) or []
    if tracklist and compact:
        lines.append([_label("Tracks"), f" {track_summary(tracklist)}"])
    elif tracklist:
        lines.append("")
        lines.append([_label("Tracklist")])
        lines.extend(_format_track_lines(tracklist))

    if verbose:
        for section in (_credit_lines(release), _identifier_lines(release)):
            lines.append("")
            lines.extend(section)

    return _text(lines)


def format_search_results(
    results: list[Any],
    query: str,
    type_filter: str | None,
    page: int,
    total_results: int | None,
    next_page_cmd: str | None,
    filters: dict[str, str] | None = None,
    *,
    filtered: bool = False,
    stop: ScanStop | None = None,
    ownership: Ownership | None = None,
    wants: Wants | None = None,
) -> str:
    """Format search results as compact text.

    The `[type]` tag is shown only for untyped searches; with a type filter the
    ref prefix already carries it. Release rows carry country, catalog number,
    community have-count, and the master ref so an agent can pick and navigate
    without a detail call. A result of an unknown or missing type has no ref:
    its row starts with `id N` and always shows its tag.
    """
    head = _search_head(query, type_filter, filters)
    counts = _page_counts(
        page, len(results), total_results, "results", filtered=filtered, stop=stop
    )
    lines: list[Line] = [[*head, " ", Span(counts, "dim")], ""]

    for result in results:
        result_type = _ref_type(result)
        if result_type is None:
            lines.append(
                _release_row(
                    ref=f"id {result.id}",
                    type_=result.type or "no type",
                    title=result.title,
                )
            )
            continue
        ref = _ref(result_type, result.id)
        show_type = None if type_filter else result_type

        if result_type in ("release", "master"):
            label_list = getattr(result, "label", None)
            fmt_list = getattr(result, "format", None)
            master_id = getattr(result, "master_id", None)
            master_ref = (
                _ref("master", master_id)
                if result_type == "release" and master_id
                else None
            )
            lines.append(
                _release_row(
                    ref=ref,
                    type_=show_type,
                    title=result.title,
                    year=getattr(result, "year", None),
                    country=getattr(result, "country", None),
                    label=label_list[0] if label_list else None,
                    catno=getattr(result, "catalog_number", None),
                    fmt=", ".join(fmt_list) if fmt_list else None,
                    have=_community_have(result),
                    master_ref=master_ref,
                    owned=_owned(ownership, result),
                    wanted=_wants_mark(wants, result),
                )
            )
        else:
            lines.append(_release_row(ref=ref, type_=show_type, title=result.title))

    lines.extend(_ownership_note(ownership))
    lines.extend(_wanted_note(wants))
    lines.extend(_footer(next_page_cmd, capped=stop is not None))
    return _text(lines)


def _search_head(
    query: str, type_filter: str | None, filters: dict[str, str] | None
) -> list[Part]:
    """`Search: release "query" artist="Nine Inch Nails"`: what was searched."""
    subject = " ".join(
        p for p in (f'"{query}"' if query else "", _format_filters(filters)) if p
    )
    searched = " ".join(p for p in (type_filter or "all", subject) if p)
    return [_label("Search"), f" {searched}"]


def artist_title(release: Any) -> str | None:
    """`Artist - Title`, the way a search result titles a release. A comma
    join attaches to the name before it (`A, B`); others stand apart (`A & B`)."""
    names = ""
    for artist in getattr(release, "artists", None) or []:
        names += getattr(artist, "name", None) or "Unknown"
        join = (getattr(artist, "join", None) or "").strip()
        if join:
            names += join if join == "," else f" {join}"
        names += " "
    title = getattr(release, "title", None)
    return f"{names.rstrip()} - {title}" if names else title


def _utc_day(moment: datetime | None) -> str | None:
    """`YYYY-MM-DD`: the UTC day of an aware timestamp."""
    return moment.astimezone(timezone.utc).date().isoformat() if moment else None


def added_day(release: Any) -> str | None:
    """The UTC day a release was added: the day `--added-since` compares, so
    a row never shows a day before the cutoff it passed."""
    return _utc_day(getattr(release, "date_added", None))


def format_added_releases(
    releases: list[Any],
    *,
    query: str,
    filters: dict[str, str],
    since: str,
    total: int,
    complete: bool,
    max_requests: int | None,
) -> str:
    """`search release ... --added-since`: looked-up releases, newest first,
    each row ending in its added day. `total` counts the window before an
    explicit `--limit` cut it to `releases`; an incomplete window ends with
    a note naming the cap that stopped it."""
    shown = len(releases)
    count = f"{total:,}" if shown == total else f"{shown} of {total:,}"
    head = _search_head(query, "release", filters)
    lines: list[Line] = [
        [
            *head,
            f" added since {since} ",
            Span(f"({count} results, newest first)", "dim"),
        ],
        "",
    ]
    for rel in releases:
        labels = getattr(rel, "labels", None) or []
        master_id = getattr(rel, "master_id", None)
        lines.append(
            _release_row(
                ref=_ref("release", rel.id),
                title=artist_title(rel),
                year=getattr(rel, "year", None),
                country=getattr(rel, "country", None),
                label=getattr(labels[0], "name", None) if labels else None,
                catno=getattr(labels[0], "catalog_number", None) if labels else None,
                fmt=_format_string(getattr(rel, "formats", None)) or None,
                added=added_day(rel),
                master_ref=_ref("master", master_id) if master_id else None,
            )
        )
    if not complete:
        lines += [
            "",
            (
                f"Stopped at --max-requests {max_requests}: the result may be "
                "incomplete. Rerun with a higher --max-requests to look further; "
                "the lookups already made are cached and cost nothing."
            ),
        ]
    return _text(lines)


# Discogs serves 60 req/min to any credentials and 25 without. Consumer
# key/secret identify an app, not a user, so user-only endpoints such as price
# suggestions refuse them.
_AUTH_LABELS: dict[AuthMode, str] = {
    "token": "token (authenticated)",
    "oauth": "oauth (authenticated)",
    "consumer": "consumer (authenticated app, not a user: no price suggestions)",
    "none": "none (unauthenticated, 25 req/min)",
}


def format_status(version: str, auth_mode: AuthMode, cache_dir: Path) -> str:
    """Format status output."""
    auth_str = _AUTH_LABELS[auth_mode]
    return _text(
        [
            f"agent-discogs v{version}",
            [_label("Auth"), f" {auth_str}"],
            [
                _label("Cache"),
                f" {cache_dir} ",
                Span(f"({CACHE_TTL_LABEL} TTL)", "dim"),
            ],
        ]
    )


def format_tracklist(release: Any) -> str:
    """Format just the tracklist from a release."""
    artists = _artist_parts(getattr(release, "artists", None))
    year = getattr(release, "year", None) or ""
    year_str = f" ({year})" if year else ""

    lines: list[Line] = [
        [
            _label("Tracklist"),
            " ",
            _ref("release", release.id),
            f' "{release.title}" by ',
            *artists,
            year_str,
        ],
        "",
    ]

    tracklist = getattr(release, "tracklist", None) or []
    if not tracklist:
        lines.append("  (no tracklist available)")
        return _text(lines)

    lines.extend(_format_track_lines(tracklist))

    return _text(lines)
