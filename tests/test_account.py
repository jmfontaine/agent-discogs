"""Tests for reading the collection value: the amount parser and the
profile's currency code."""

from __future__ import annotations

import pytest
from discogs_sdk import User

from agent_discogs.account import UnknownFormatError, amount, currency
from agent_discogs.errors import classify


@pytest.mark.parametrize(
    ("text", "number"),
    [
        ("$250.00", 250.0),
        ("€1.250,00", 1250.0),
        ("£1,250.50", 1250.5),
        ("¥1,250", 1250.0),
        ("1\u00a0250,00 kr", 1250.0),
        ("CHF 1'250.00", 1250.0),
        ("$0.00", 0.0),
        ("CA$1,234,567.8", 1234567.8),
        ("1.250", 1250.0),
        ("12,5 €", 12.5),
        ("R$ 1.250.000,99", 1250000.99),
        ("1\u202f250,00\u00a0€", 1250.0),
        ("CHF 1\u2019250.00", 1250.0),
        ("Fr. 250.00", 250.0),
        ("250", 250.0),
    ],
)
def test_reads_the_number_out_of_a_formatted_amount(text: str, number: float) -> None:
    assert amount(text, "minimum") == number


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "N/A",
        "€12.34.56",
        "1,2345",
        "1 2345,00",
        "12345,678.90",
        "1,,250",
        "75 to 80",
        "\u00b2",
        # A separator before the first digit: `.50` is not 50.
        ".50",
        "€ ,50",
        # A sign the symbol would drop.
        "-$5.00",
        "$-5.00",
        "($5.00)",
        "\u22125,00 €",
        # A leading 0 cannot start a thousands group.
        "0.250",
        "0,250",
        # More than a float holds.
        "9" * 400,
    ],
)
def test_an_unknown_format_names_the_field_and_never_the_value(
    text: str | None,
) -> None:
    with pytest.raises(UnknownFormatError) as raised:
        amount(text, "the collection value's median")
    assert raised.value.field == "the collection value's median"
    info = classify(raised.value)
    assert info.code == "bad_response"
    assert info.message.startswith(
        "Discogs sent a response that cannot be read: the collection value's median"
    )
    if text:
        assert text not in info.message
    assert info.hint is not None
    assert "retrying will not help" in info.hint
    assert "--json --full prints the amounts" in info.hint


def test_the_currency_is_the_profile_code() -> None:
    profile = User.model_validate({"id": 7, "username": "me", "curr_abbr": "EUR"})
    assert currency(profile) == "EUR"


@pytest.mark.parametrize("code", [None, "", "€", "eur", "EURO"])
def test_a_profile_without_a_currency_code_is_a_bad_response(
    code: str | None,
) -> None:
    profile = User.model_validate({"id": 7, "username": "me", "curr_abbr": code})
    with pytest.raises(UnknownFormatError) as raised:
        currency(profile)
    info = classify(raised.value)
    assert info.code == "bad_response"
    assert "currency_code" in info.message
    assert info.hint is not None
    assert "--full" not in info.hint  # it shows the amounts, not the code
