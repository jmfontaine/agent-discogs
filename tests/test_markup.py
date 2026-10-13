"""Tests for converting Discogs markup in profiles and notes."""

from __future__ import annotations

import pytest

from agent_discogs.markup import Part, parse, plain, profile, split_lines, truncate
from agent_discogs.style import Attr, Span, Styled


def ref(text: str) -> Span:
    return Span(text, "ref")


class TestLinks:
    @pytest.mark.parametrize(
        ("markup", "converted"),
        [
            ("[a3857]", "@a3857"),
            ("[l7783]", "@l7783"),
            ("[r847868]", "@r847868"),
            ("[m3719]", "@m3719"),
            # Discogs' title-only form, by ID too.
            ("[r=847868]", "@r847868"),
            ("[m=3719]", "@m3719"),
        ],
    )
    def test_entity_ids_become_refs(self, markup: str, converted: str) -> None:
        assert parse(f"founder of {markup}.") == ["founder of ", ref(converted), "."]

    @pytest.mark.parametrize(
        ("markup", "converted"),
        [
            ("[a=Carl Craig]", "Carl Craig"),
            ("[l=Warp Records]", "Warp Records"),
            ("[a= A Positive Life ]", "A Positive Life"),
            # A digit-only name is still a name: an artist may be called 1349.
            ("[a=1349]", "1349"),
        ],
    )
    def test_name_links_become_the_name(self, markup: str, converted: str) -> None:
        assert parse(f"by {markup}.") == [f"by {converted}."]

    @pytest.mark.parametrize(
        ("markup", "converted"),
        [
            ("[g1.2.3]", "RSG §1.2.3"),
            ("[t=262]", "Forum Topic #262"),
        ],
    )
    def test_links_without_a_cli_meaning_keep_their_text(
        self, markup: str, converted: str
    ) -> None:
        assert parse(f"see {markup}") == [f"see {converted}"]

    @pytest.mark.parametrize(
        "markup",
        [
            "[A3857]",  # tags are lowercase
            "[A= A Positive Life]",
            "[B]",
            "[x123]",  # no such entity
            "[r=Name]",  # releases and masters link by ID only
            "[a=]",
            "[a= ]",
            "[Booklet]",
            "[Catalog/series numbers]",
            "[img=https://example.com/a.png]",
            "[1]",
            "[]",
            "[a123",
        ],
    )
    def test_anything_else_in_brackets_is_unchanged(self, markup: str) -> None:
        assert parse(f"x {markup} y") == [f"x {markup} y"]


class TestUrls:
    def test_text_then_site(self) -> None:
        assert parse("[url=https://www.discogs.com/label/66349]SODEC[/url].") == [
            Styled("SODEC", ("underline",)),
            " (discogs.com).",
        ]

    def test_bare_url_shows_its_site(self) -> None:
        assert parse("at [url]http://warp.net/shop?x=1[/url]") == [
            "at ",
            Styled("warp.net", ("underline",)),
        ]

    def test_empty_text_shows_the_site(self) -> None:
        assert parse("[url=https://warp.net/][/url]") == [
            Styled("warp.net", ("underline",))
        ]

    def test_no_host_keeps_the_text(self) -> None:
        assert parse("[url=warp.net/shop]Shop[/url]") == [
            Styled("Shop", ("underline",))
        ]
        assert parse("[url]warp.net[/url]") == [Styled("warp.net", ("underline",))]
        assert parse("[url=mailto:x][/url]!") == ["!"]

    def test_whitespace_text_shows_the_site(self) -> None:
        assert parse("[url=https://warp.net/] [/url]x") == [
            Styled("warp.net", ("underline",)),
            "x",
        ]

    def test_a_link_inside_a_link_is_text(self) -> None:
        assert parse("[url=https://a.com]A [url=https://b.com]B[/url] C") == [
            Styled("A [url=https://b.com]B", ("underline",)),
            " (a.com) C",
        ]

    def test_unparsable_url_keeps_the_text(self) -> None:
        # urlsplit rejects an unbalanced IPv6 bracket.
        assert parse("[url]http://[::1[/url]") == [
            Styled("http://[::1", ("underline",))
        ]

    def test_unclosed_url_ends_with_its_field(self) -> None:
        assert parse("[url=https://warp.net]Warp, and more") == [
            Styled("Warp, and more", ("underline",)),
            " (warp.net)",
        ]

    def test_markup_inside_the_text(self) -> None:
        assert parse("[url=https://warp.net][b]Warp[/b] [a45][/url]") == [
            Styled("Warp", ("bold", "underline")),
            Styled(" ", ("underline",)),
            ref("@a45"),
            " (warp.net)",
        ]


class TestTextStyles:
    @pytest.mark.parametrize(
        ("tag", "attr"),
        [("b", "bold"), ("i", "italic"), ("u", "underline"), ("s", "strike")],
    )
    def test_each_tag(self, tag: str, attr: Attr) -> None:
        assert parse(f"a [{tag}]b[/{tag}] c") == ["a ", Styled("b", (attr,)), " c"]

    def test_nesting_combines_attributes_in_a_fixed_order(self) -> None:
        assert parse("[i][u]Albums:[/u] list[/i]") == [
            Styled("Albums:", ("italic", "underline")),
            Styled(" list", ("italic",)),
        ]
        assert parse("[s][b]x[/b][/s]") == [Styled("x", ("bold", "strike"))]

    def test_a_ref_keeps_its_own_style(self) -> None:
        assert parse("[b]by [a1][/b]") == [Styled("by ", ("bold",)), ref("@a1")]

    def test_unclosed_tag_ends_with_its_field(self) -> None:
        assert parse("[b]bold to the end") == [Styled("bold to the end", ("bold",))]

    def test_stray_closing_tag_is_dropped(self) -> None:
        assert parse("plain[/b] text[/url]") == ["plain text"]

    def test_closing_an_outer_tag_closes_the_inner_one(self) -> None:
        assert parse("[i][u]x[/i]y[/u]") == [
            Styled("x", ("italic", "underline")),
            "y",
        ]

    def test_empty_tags_print_nothing(self) -> None:
        assert parse("[b][/b]") == []

    def test_deep_nesting_is_styled(self) -> None:
        # Each run is styled once, under the tags open at that point.
        parts = parse("[b]x" * 3000)
        assert parts == [Styled("x" * 3000, ("bold",))]

    def test_whitespace_a_tag_leaves_is_trimmed(self) -> None:
        assert parse("[url=mailto:x][/url]\nHello [b] [/b]") == ["Hello"]


class TestTruncate:
    def test_short_text_is_kept(self) -> None:
        parts: list[Part] = ["short ", ref("@a1")]
        assert truncate(parts) == parts

    def test_cut_at_the_last_space_before_the_limit(self) -> None:
        result = truncate(["a " * 200])
        assert plain(result) == "a " * 149 + "a..."

    def test_counts_converted_text_not_markup(self) -> None:
        # 296 visible characters, 303 with the markup: nothing to cut.
        assert plain(profile("[b]" + "x " * 148 + "[/b]")) == ("x " * 148).rstrip()
        # The limit falls inside a converted ref: the cut goes back to the
        # space before it, never into the [l…] tag it came from.
        text = "x" * 280 + " [b]bold[/b] [l123456789012345678901234567890]"
        assert plain(profile(text)) == "x" * 280 + " bold..."

    def test_a_styled_run_is_cut_and_stays_closed(self) -> None:
        result = truncate(["y" * 290, Styled(" bold words here", ("bold",))])
        assert result == ["y" * 290, Styled(" bold", ("bold",)), "..."]

    def test_a_ref_is_never_split(self) -> None:
        # No space before the limit: the cut falls inside the ref.
        result = truncate(["x" * 295, ref("@a1234567890")])
        assert result == ["x" * 295, "..."]

    def test_profile_of_nothing(self) -> None:
        assert profile(None) == []
        assert profile("  ") == []


class TestSplitLines:
    def test_a_styled_run_is_styled_on_each_line(self) -> None:
        assert split_lines(parse("a [b]x\r\ny[/b] [a1]\n\nz")) == [
            ["a ", Styled("x", ("bold",))],
            [Styled("y", ("bold",)), " ", ref("@a1")],
            [],
            ["z"],
        ]
