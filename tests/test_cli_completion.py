"""Tests for shell completion: the `completion` command and what click offers
for each closed set of values, driven the way the bash script drives it."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from agent_discogs import cli, client
from tests.conftest import SKILLS

_NOUNS = [
    "artist",
    "credits",
    "identifiers",
    "label",
    "master",
    "owned",
    "price",
    "release",
    "releases",
    "tracklist",
    "versions",
    "list",
    "inventory",
    "collection",
    "lists",
    "wantlist",
    "ids",
]
_RELEASES_SORT = ["year", "title", "format"]
_VERSIONS_SORT = ["released", "title", "format", "label", "catno", "country"]
_INVENTORY_SORT = ["listed", "price", "item", "artist", "label", "catno"]


def complete(line: str) -> list[str]:
    """What Tab offers at the end of `line`, a command line as typed
    (a trailing space completes a new word)."""
    words = line.split(" ")
    result = CliRunner().invoke(
        cli,
        [],
        prog_name="agent-discogs",
        env={
            "_AGENT_DISCOGS_COMPLETE": "bash_complete",
            "COMP_WORDS": line,
            "COMP_CWORD": str(len(words) - 1),
        },
    )
    assert result.exit_code == 0, result.output
    # Nothing to offer prints one empty line.
    offered = [row.split(",", 1) for row in result.stdout.splitlines() if row]
    assert all(kind == "plain" for kind, _ in offered)
    return [value for _, value in offered]


class TestCompletion:
    def test_top_level_offers_each_command_once_without_aliases(self) -> None:
        assert complete("agent-discogs ") == [
            "cache",
            "completion",
            "get",
            "price",
            "search",
            "skills",
            "status",
            "tracks",
        ]
        assert complete("agent-discogs f") == []
        assert complete("agent-discogs q") == []

    def test_an_alias_typed_in_full_still_completes(self) -> None:
        assert complete("agent-discogs show ") == _NOUNS
        assert complete("agent-discogs find --release-type u") == ["unofficial"]

    def test_get_offers_the_nouns(self) -> None:
        assert complete("agent-discogs get ") == _NOUNS
        assert complete("agent-discogs get rel") == ["release", "releases"]

    def test_search_offers_the_type_word_only_first(self) -> None:
        assert complete("agent-discogs search ") == [
            "artist",
            "label",
            "master",
            "release",
        ]
        assert complete("agent-discogs search r") == ["release"]
        assert complete("agent-discogs search --year 1994 ma") == ["master"]
        assert complete("agent-discogs search --credit Gelder r") == ["release"]
        # A query word that is no type gets nothing, and after the first
        # positional nothing is offered: the rest is free text.
        assert complete("agent-discogs search Radiohead") == []
        assert complete("agent-discogs search release ") == []
        assert complete("agent-discogs search Wire a") == []
        assert complete("agent-discogs search Radiohead --bogus r") == []

    def test_search_offers_the_new_filters(self) -> None:
        assert complete("agent-discogs search --cr") == ["--credit"]
        assert complete("agent-discogs search --t") == ["--title", "--track"]
        assert complete("agent-discogs search --an") == ["--anv"]

    @pytest.mark.parametrize(
        ("line", "offered"),
        [
            ("agent-discogs search --release-type ", ["official", "unofficial", "all"]),
            ("agent-discogs search release --sort ", ["date_added"]),
            ("agent-discogs --color ", ["auto", "always", "never"]),
            ("agent-discogs --color a", ["auto", "always"]),
            ("agent-discogs cache ", ["clear"]),
            ("agent-discogs skills ", ["get", "list", "path"]),
            ("agent-discogs skills get ", list(SKILLS)),
            ("agent-discogs skills path ", list(SKILLS)),
            ("agent-discogs skills get m", ["marketplace"]),
            ("agent-discogs completion ", ["bash", "zsh", "fish"]),
            (
                "agent-discogs get wantlist --currency ",
                [
                    *("USD", "GBP", "EUR", "CAD", "AUD", "JPY"),
                    *("CHF", "MXN", "BRL", "NZD", "SEK", "ZAR"),
                ],
            ),
            ("agent-discogs get wantlist --currency e", ["EUR"]),
            (
                "agent-discogs get inventory seller --condition ",
                ["M", "NM", "VG+", "VG", "G+", "G", "F", "P"],
            ),
            ("agent-discogs get inventory seller --sleeve vg", ["VG+", "VG"]),
        ],
    )
    def test_fixed_option_and_argument_values(
        self, line: str, offered: list[str]
    ) -> None:
        assert complete(line) == offered

    @pytest.mark.parametrize(
        ("line", "offered"),
        [
            ("agent-discogs get versions --sort ", _VERSIONS_SORT),
            ("agent-discogs get releases @a3857 --sort ", _RELEASES_SORT),
            ("agent-discogs get versions --sort c", ["catno", "country"]),
            (
                "agent-discogs get collection --sort ",
                [
                    *("label", "artist", "title", "catno"),
                    *("format", "rating", "added", "year"),
                ],
            ),
            ("agent-discogs get inventory seller --sort ", _INVENTORY_SORT),
            # `--sort=` as one word (zsh, fish): the noun is parsed already.
            ("agent-discogs get versions --sort=", _VERSIONS_SORT),
            ("agent-discogs get release --sort=", []),
            # No noun yet: every key some noun takes.
            (
                "agent-discogs get --sort ",
                [
                    *("year", "title", "format", "released", "label", "catno"),
                    *("country", "artist", "rating", "added"),
                    *("listed", "price", "item"),
                ],
            ),
            # A noun that takes no --sort gets nothing.
            ("agent-discogs get release --sort ", []),
        ],
    )
    def test_get_sort_follows_the_noun(self, line: str, offered: list[str]) -> None:
        assert complete(line) == offered

    def test_get_sort_still_rejects_another_nouns_key(self) -> None:
        result = CliRunner().invoke(
            cli, ["get", "versions", "@m3719", "--sort", "year", "--json"]
        )
        assert result.exit_code == 1
        assert "released, title, format, label, catno, country" in result.stdout

    def test_refs_are_not_completed(self) -> None:
        assert complete("agent-discogs get release ") == []
        assert complete("agent-discogs tracks @r") == []


class TestCompletionCommand:
    @pytest.mark.parametrize("shell", ["bash", "zsh", "fish"])
    def test_prints_the_script_that_calls_back_into_the_cli(self, shell: str) -> None:
        result = CliRunner().invoke(cli, ["completion", shell])
        assert result.exit_code == 0
        # The script runs agent-discogs with the variable click's main() reads.
        assert f"_AGENT_DISCOGS_COMPLETE={shell}_complete" in result.stdout

    def test_an_unknown_shell_is_a_usage_error_naming_the_supported_ones(
        self,
    ) -> None:
        result = CliRunner().invoke(cli, ["completion", "powershell"])
        assert result.exit_code == 2
        assert result.stdout == ""
        assert "'powershell' is not one of 'bash', 'zsh', 'fish'" in result.stderr

    def test_never_builds_the_client_or_prints_a_budget_line(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(client, "_client", None)
        result = CliRunner().invoke(cli, ["completion", "zsh"])
        assert result.exit_code == 0
        assert result.stderr == ""
        assert client._client is None
