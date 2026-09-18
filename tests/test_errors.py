"""Tests for error formatting."""

from __future__ import annotations

import json

import pytest
from discogs_sdk import (
    AuthenticationError,
    CacheMissError,
    DiscogsAPIError,
    DiscogsConnectionError,
    ForbiddenError,
    NotFoundError,
    RateLimit,
    RateLimitError,
)

from agent_discogs.errors import (
    classify,
    fail,
    format_error,
    format_error_json,
)


class TestFormatError:
    def test_not_found_default_context(self) -> None:
        exc = NotFoundError("nope", status_code=404, response_body={})
        result = format_error(exc)
        assert result.startswith("✗ Resource not found")
        assert "search" in result

    def test_not_found_with_context(self) -> None:
        exc = NotFoundError("nope", status_code=404, response_body={})
        result = format_error(exc, context="Artist @a123")
        assert "Artist @a123 not found" in result

    def test_not_found_from_incomplete_seller_settings(self) -> None:
        # Discogs answers the price endpoints with 404 when the account has no
        # seller settings, so the generic "not found, go search" advice sends
        # the caller after a release that does exist.
        exc = NotFoundError(
            "not found",
            status_code=404,
            response_body={"message": "You must fill out your seller settings first."},
        )
        result = format_error(exc, context="Price @r6276183")
        assert "seller settings" in result
        assert "discogs.com/settings/seller" in result
        assert "not found. Try" not in result

    def test_not_found_from_seller_settings_with_plain_text_body(self) -> None:
        # The SDK's binary endpoints pass `response.text` straight through, so
        # response_body is a bare string rather than a parsed JSON payload.
        exc = NotFoundError(
            "not found",
            status_code=404,
            response_body="You must fill out your seller settings first.",
        )
        result = format_error(exc, context="Price @r6276183")
        assert "seller settings" in result
        assert "not found. Try" not in result

    def test_authentication_error(self) -> None:
        exc = AuthenticationError("bad token", status_code=401, response_body={})
        result = format_error(exc)
        assert "Authentication failed" in result
        assert "DISCOGS_TOKEN" in result

    def test_api_error(self) -> None:
        exc = DiscogsAPIError("server error", status_code=500, response_body={})
        result = format_error(exc)
        assert "API error (500)" in result

    def test_connection_error(self) -> None:
        exc = DiscogsConnectionError("timeout")
        result = format_error(exc)
        assert "Connection error" in result

    def test_forbidden_error(self) -> None:
        exc = ForbiddenError("forbidden", status_code=403, response_body={})
        result = format_error(exc)
        assert "Access forbidden" in result

    def test_rate_limit_error_with_retry_after(self) -> None:
        exc = RateLimitError(
            "slow down", status_code=429, response_body={}, retry_after="60"
        )
        assert format_error(exc) == (
            "✗ Rate limit exceeded.\n"
            "  Retry in 60s. 60 req/min with DISCOGS_TOKEN, 25 without."
        )
        assert classify(exc).retry_after == 60

    def test_rate_limit_error_without_usable_retry_after(self) -> None:
        exc = RateLimitError(
            "slow down", status_code=429, response_body={}, retry_after="soon"
        )
        info = classify(exc)
        assert info.retry_after is None
        assert info.hint is not None
        assert info.hint.startswith("Wait a moment and retry.")

    def test_rate_limit_error_reports_the_budget(self) -> None:
        exc = RateLimitError(
            "slow down",
            status_code=429,
            response_body={},
            retry_after="30",
            ratelimit=RateLimit(60, 60, 0),
        )
        assert format_error(exc) == (
            "✗ Rate limited: 0/60 requests left this minute.\n"
            "  Retry in 30s. 60 req/min with DISCOGS_TOKEN, 25 without."
        )
        assert json.loads(format_error_json(exc))["error"] == {
            "code": "rate_limited",
            "message": "Rate limited: 0/60 requests left this minute.",
            "hint": "Retry in 30s. 60 req/min with DISCOGS_TOKEN, 25 without.",
            "retry_after": 30,
            "status": 429,
            "limit": 60,
            "remaining": 0,
        }

    def test_cache_miss(self) -> None:
        exc = CacheMissError(
            "GET", "https://api.discogs.com/releases/847868?curr_abbr=USD"
        )
        assert format_error(exc) == (
            "✗ Not cached: GET /releases/847868?curr_abbr=USD\n"
            "  Rerun without --cached to fetch it from the API."
        )
        assert classify(exc).code == "cache_miss"
        assert classify(exc).status is None

    def test_value_error(self) -> None:
        exc = ValueError("bad input")
        result = format_error(exc)
        assert result == "✗ bad input"

    def test_fallback_exception(self) -> None:
        exc = RuntimeError("something broke")
        result = format_error(exc)
        assert "Unexpected error" in result
        assert "something broke" in result


class TestClassify:
    def test_codes(self) -> None:
        cases = [
            (NotFoundError("x", status_code=404, response_body={}), "not_found"),
            (
                NotFoundError(
                    "x", status_code=404, response_body={"message": "seller settings"}
                ),
                "seller_settings_required",
            ),
            (
                AuthenticationError("x", status_code=401, response_body={}),
                "auth_required",
            ),
            (ForbiddenError("x", status_code=403, response_body={}), "forbidden"),
            (RateLimitError("x", status_code=429, response_body={}), "rate_limited"),
            (DiscogsAPIError("x", status_code=502, response_body={}), "api_error"),
            (DiscogsConnectionError("x"), "connection_error"),
            (CacheMissError("GET", "https://api.discogs.com/x"), "cache_miss"),
            (ValueError("x"), "invalid_argument"),
            (RuntimeError("x"), "unexpected"),
        ]
        assert [classify(exc).code for exc, _ in cases] == [code for _, code in cases]

    def test_status_carried_for_api_errors(self) -> None:
        assert (
            classify(DiscogsAPIError("x", status_code=502, response_body={})).status
            == 502
        )
        assert classify(ValueError("x")).status is None


class TestFormatErrorJson:
    def test_envelope_omits_empty_fields(self) -> None:
        assert json.loads(format_error_json(ValueError("bad input"))) == {
            "error": {"code": "invalid_argument", "message": "bad input"}
        }

    def test_envelope_carries_hint_status_and_retry(self) -> None:
        exc = RateLimitError("x", status_code=429, response_body={}, retry_after="5")
        assert json.loads(format_error_json(exc)) == {
            "error": {
                "code": "rate_limited",
                "message": "Rate limit exceeded.",
                "hint": "Retry in 5s. 60 req/min with DISCOGS_TOKEN, 25 without.",
                "retry_after": 5,
                "status": 429,
            }
        }

    def test_context_lands_in_message(self) -> None:
        exc = NotFoundError("x", status_code=404, response_body={})
        data = json.loads(format_error_json(exc, "Release @r1"))
        assert data["error"]["message"] == "Release @r1 not found."


class TestFail:
    def test_text_goes_to_stderr(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as exc_info:
            fail(ValueError("nope"), json_output=False)
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == "✗ nope\n"

    def test_json_goes_to_stdout(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as exc_info:
            fail(ValueError("nope"), json_output=True)
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert captured.err == ""
        assert json.loads(captured.out) == {
            "error": {"code": "invalid_argument", "message": "nope"}
        }
