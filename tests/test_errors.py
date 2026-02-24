"""Tests for error formatting."""

from __future__ import annotations

from discogs_sdk import (
    AuthenticationError,
    DiscogsAPIError,
    DiscogsConnectionError,
    ForbiddenError,
    NotFoundError,
    RateLimitError,
)

from agent_discogs.errors import format_error


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

    def test_rate_limit_error(self) -> None:
        exc = RateLimitError(
            "slow down", status_code=429, response_body={}, retry_after="60"
        )
        result = format_error(exc)
        assert "Rate limit exceeded" in result

    def test_value_error(self) -> None:
        exc = ValueError("bad input")
        result = format_error(exc)
        assert result == "✗ bad input"

    def test_fallback_exception(self) -> None:
        exc = RuntimeError("something broke")
        result = format_error(exc)
        assert "Unexpected error" in result
        assert "something broke" in result
