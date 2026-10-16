"""Doc commands must parse: every `agent-discogs ...` command in the docs, the
bundled skills and `--help` goes through click's own parser, so a renamed
command, a removed flag or a flag on the wrong command fails here, naming the
file and line, before an agent copies it.

Only parsing: nothing is invoked and no request is sent, so a flag that click
accepts and the command rejects later (one that does not apply to a noun) is
not caught here.

A candidate is a line of a fenced block, or an inline code span, whose first
word (after `VAR=value` words) is `agent-discogs`; in `--help`, a line starting
with it. `eval "$(...)"` is unwrapped; a `# comment` and anything from an
unquoted pipe (` |`), `>`, `;` or `&&` are dropped; each `<placeholder>` becomes
a dummy word. A synopsis (a `[`, a bare `...` or `…` word, or an `a|b`
alternation) is skipped, and so is a deliberate error example, which ends in
`# error`.
"""

from __future__ import annotations

import re
import shlex
from contextlib import ExitStack
from typing import TYPE_CHECKING, NamedTuple

import click
import pytest

from agent_discogs import _HELP_TEXT, cli
from tests.conftest import DOC_FILES, REPO_ROOT

if TYPE_CHECKING:
    from collections.abc import Iterator

PROGRAM = "agent-discogs"
ERROR_MARK = "# error"
# Any placeholder: a string option, an argument and a number option all take it.
DUMMY = "1"

_FENCE_OPEN = re.compile(r"^\s*(?:(?:[-*+]|\d+[.)])\s+)?(`{3,}|~{3,})")
_FENCE_CLOSE = re.compile(r"^\s*(`{3,}|~{3,})\s*$")
_SPAN = re.compile(r"(`+)(.+?)\1")
# The `$(...)` of `eval "$(...)"`, whatever comment or operator follows it.
_EVAL = re.compile(r'^eval "\$\((.*)\)"(?=\s|[|>;]|&&|$)')
_PLACEHOLDER = re.compile(r"<[^<>]+>")
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
# classify() verdicts for a candidate that is not parsed as is.
SYNOPSIS = "synopsis"
ERROR = "error"
UNSPLITTABLE = "unsplittable"


class Command(NamedTuple):
    location: str
    text: str
    argv: list[str] | None  # None: the shell cannot split it (unbalanced quotes).


def _unquoted(text: str) -> Iterator[int]:
    """The index of every character of `text` the shell sees as syntax: not
    quoted, not escaped, and not a quote itself."""
    quote = None
    escaped = False
    for i, char in enumerate(text):
        if escaped:
            escaped = False
        elif quote:
            if char == quote:
                quote = None
            elif char == "\\" and quote == '"':
                escaped = True
        elif char == "\\":
            escaped = True
        elif char in "'\"":
            quote = char
        else:
            yield i


def _cut(text: str) -> str:
    """`text` up to its first comment, pipe, redirect, `;` or `&&`. A `|`
    inside a word is an `a|b` alternation, not a pipe, and stays."""
    for i in _unquoted(text):
        char = text[i]
        after_space = i == 0 or text[i - 1].isspace()
        if char in ">;" or text.startswith("&&", i) or (char in "#|" and after_space):
            return text[:i]
    return text


def _is_synopsis(text: str, argv: list[str]) -> bool:
    return any(text[i] in "[|" for i in _unquoted(text)) or any(
        word in ("...", "…") for word in argv
    )


def classify(text: str) -> list[str] | str | None:
    """The argv after `agent-discogs` of a command to parse; SYNOPSIS, ERROR or
    UNSPLITTABLE for a candidate that is not; None for no candidate."""
    raw = text.strip()
    # Unwrapped first: the inside of `$(...)` has quotes of its own.
    text = match[1] if (match := _EVAL.match(raw)) else raw
    text = _cut(_PLACEHOLDER.sub(DUMMY, text)).strip()
    try:
        words: list[str] | None = shlex.split(text)
    except ValueError:
        words = None
    head = text.split() if words is None else words
    while head and _ASSIGNMENT.match(head[0]):
        head = head[1:]
    if not head or head[0] != PROGRAM:
        return None
    if raw.endswith(ERROR_MARK):
        return ERROR
    if words is None:
        return UNSPLITTABLE
    argv = head[1:]
    return SYNOPSIS if _is_synopsis(text, argv) else argv


def texts(lines: list[str]) -> list[tuple[int, str]]:
    """(line number, text) of every line inside a fenced block and every
    inline code span outside one."""
    found = []
    fence = ""  # The opening fence of the block we are in, if any.
    for number, line in enumerate(lines, 1):
        if fence:
            close = _FENCE_CLOSE.match(line)
            if close and close[1][0] == fence[0] and len(close[1]) >= len(fence):
                fence = ""
            else:
                found.append((number, line))
        elif opening := _FENCE_OPEN.match(line):
            fence = opening[1]
        else:
            # In a table row, a code span's `\|` is a literal `|`.
            table = line.lstrip().startswith("|")
            found.extend(
                (number, span[2].replace("\\|", "|") if table else span[2])
                for span in _SPAN.finditer(line)
            )
    return found


def _collect() -> tuple[list[Command], list[str]]:
    sources = {
        str(path.relative_to(REPO_ROOT)): texts(
            path.read_text(encoding="utf-8").splitlines()
        )
        for path in DOC_FILES
    }
    # --help is no Markdown: its examples are its indented lines. Its first
    # line, "agent-discogs - token-efficient ...", is a title.
    sources["_HELP_TEXT"] = [
        (number, line)
        for number, line in enumerate(str(_HELP_TEXT).splitlines(), 1)
        if line[:1].isspace()
    ]
    commands, synopses = [], []
    for name, found in sources.items():
        for number, text in found:
            kind = classify(text)
            location = f"{name}:{number}"
            if isinstance(kind, list):
                commands.append(Command(location, text.strip(), kind))
            elif kind == UNSPLITTABLE:
                commands.append(Command(location, text.strip(), None))
            elif kind == SYNOPSIS:
                synopses.append(f"{location}: {text.strip()}")
    return commands, synopses


COMMANDS, SYNOPSES = _collect()


def resolve(argv: list[str]) -> None:
    """Parse `argv` down to its last subcommand, as click would before
    invoking it; raise the click exception it would print."""
    with ExitStack() as stack:
        try:
            ctx = stack.enter_context(cli.make_context(PROGRAM, list(argv)))
            # The same walk as click's own shell completion (_resolve_context).
            args = [*ctx._protected_args, *ctx.args]
            while isinstance(ctx.command, click.Group) and args:
                name, command, args = ctx.command.resolve_command(ctx, args)
                assert name is not None
                assert command is not None
                ctx = stack.enter_context(command.make_context(name, args, parent=ctx))
                args = [*ctx._protected_args, *ctx.args]
        except click.exceptions.Exit:
            return  # --help and --version stop the parse, successfully.


def test_docs_contain_commands() -> None:
    # Far fewer would mean DOC_FILES or classify() went wrong and skipped them.
    assert len(COMMANDS) > 100, f"{len(COMMANDS)} commands, {len(SYNOPSES)} synopses"


@pytest.mark.parametrize("command", COMMANDS, ids=[c.location for c in COMMANDS])
def test_doc_command_parses(command: Command) -> None:
    if command.argv is None:
        pytest.fail(f"{command.location}: {command.text}: unbalanced quotes")
    try:
        resolve(command.argv)
    except click.ClickException as e:
        pytest.fail(f"{command.location}: {command.text}: {e.format_message()}")


@pytest.mark.parametrize(
    ("argv", ERROR),
    [
        (["get", "release", "@r1", "--nope"], click.NoSuchOption),
        (["--nope", "status"], click.NoSuchOption),
        (["nope"], click.UsageError),
        (["get", "nope", "@r1"], click.BadParameter),
        (["skills", "list", "--nope"], click.NoSuchOption),
        (["skills", "get"], click.MissingParameter),
        (["status", "extra"], click.UsageError),
    ],
)
def test_resolve_rejects_what_click_would(
    argv: list[str], error: type[click.ClickException]
) -> None:
    with pytest.raises(error):
        resolve(argv)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "agent-discogs get release @r1 --json | jq .title",
            ["get", "release", "@r1", "--json"],
        ),
        ('DISCOGS_TOKEN=x agent-discogs search "a # b"  # note', ["search", "a # b"]),
        ('eval "$(agent-discogs completion zsh)"', ["completion", "zsh"]),
        (
            'eval "$(agent-discogs completion bash)"   # in ~/.bashrc',
            ["completion", "bash"],
        ),
        ('eval "$(agent-discogs completion "bash;zsh")"', ["completion", "bash;zsh"]),
        ('eval "$(agent-discogs completion zsh)"; true', ["completion", "zsh"]),
        ('DISCOGS_TOKEN="a b" agent-discogs skills list', ["skills", "list"]),
        ('agent-discogs search "a|b" x', ["search", "a|b", "x"]),
        ("agent-discogs search a\\|b", ["search", "a|b"]),
        ('agent-discogs search "a [b]"', ["search", "a [b]"]),
        ('agent-discogs search "x', UNSPLITTABLE),
        ("agent-discogs completion fish > ~/f.fish", ["completion", "fish"]),
        ("agent-discogs status && agent-discogs cache clear", ["status"]),
        ("agent-discogs get list <id>", ["get", "list", DUMMY]),
        ('agent-discogs search --format 12\\" x', ["search", "--format", '12"', "x"]),
        ("agent-discogs search artist|label <query>", SYNOPSIS),
        ("agent-discogs get release @r... [@r...]", SYNOPSIS),
        ("agent-discogs find ...", SYNOPSIS),
        ("agent-discogs get release @r1 --nope  # error", ERROR),
        ("agent-discogs-like tool", None),
        ("pipx install agent-discogs", None),
        ('{"hint":"Try: agent-discogs search x"}', None),
    ],
)
def test_classify(text: str, expected: list[str] | str | None) -> None:
    assert classify(text) == expected


def test_texts_follow_fences_and_unescape_table_pipes() -> None:
    lines = [
        "- ```bash",
        "  agent-discogs status",
        "  ```",
        "~~~~",
        "```",
        "~~~",
        "~~~~",
        "| `agent-discogs a\\|b` |",
        "`agent-discogs c\\|d` and `e`",
    ]
    assert texts(lines) == [
        (2, "  agent-discogs status"),
        (5, "```"),
        (6, "~~~"),
        (8, "agent-discogs a|b"),
        (9, "agent-discogs c\\|d"),
        (9, "e"),
    ]
