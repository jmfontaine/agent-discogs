"""Tests for error formatting."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from discogs_sdk import (
    AuthenticationError,
    CacheError,
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
        assert format_error(exc) == "✗ API error (500): server error"

    def test_api_error_without_a_message(self) -> None:
        exc = DiscogsAPIError("", status_code=502, response_body="")
        assert classify(exc).message == "API error (502)."

    def test_api_error_detail_is_bounded(self) -> None:
        exc = DiscogsAPIError("x" * 500, status_code=500, response_body="x" * 3000)
        message = classify(exc).message
        assert message == "API error (500): " + "x" * 199 + "…"

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

    def test_encoding_error_is_not_an_invalid_argument(self) -> None:
        """UnicodeError subclasses ValueError, but an output encoding failure
        says nothing about the caller's arguments."""
        exc = UnicodeEncodeError("ascii", "ヒ", 0, 1, "ordinal not in range(128)")
        info = classify(exc)
        assert info.code == "unexpected"
        assert "'ascii' codec" in info.message

    def test_unexpected_error_names_the_exception_class(self) -> None:
        result = format_error(RuntimeError("something broke"))
        assert "RuntimeError" in result
        assert "something broke" in result
        # A class outside builtins keeps its module, which says where it came from.
        info = classify(sqlite3.OperationalError("database is locked"))
        assert info.code == "unexpected"
        assert "sqlite3.OperationalError" in info.message
        assert "database is locked" in info.message

    def test_unexpected_error_without_a_message_still_names_its_class(self) -> None:
        message = classify(RuntimeError()).message
        assert "RuntimeError" in message
        assert not message.rstrip().endswith(":")


class TestCacheError:
    """A cache the CLI cannot use names its path and says how to recover;
    nothing in the CLI deletes it."""

    PATH = Path("/home/u/.cache/agent-discogs/cache.db")

    def _error(
        self, cause: Exception | None, operation: str = "vet the database file"
    ) -> CacheError:
        exc = CacheError(
            f"SQLite cache {self.PATH}: could not {operation}: {cause}",
            operation=operation,
            path=self.PATH,
        )
        exc.__cause__ = cause
        return exc

    def test_names_path_operation_and_cause(self) -> None:
        info = classify(self._error(PermissionError("not a regular file")))
        assert info.code == "cache_error"
        assert info.path == str(self.PATH)
        assert str(self.PATH) in info.message
        assert "vet the database file" in info.message
        assert "PermissionError" in info.message
        assert "not a regular file" in info.message
        assert info.hint is not None
        assert f"delete {self.PATH}" in info.hint
        assert f"{self.PATH.name}-wal" in info.hint  # vetting covers sidecars
        assert "--fresh" in info.hint
        assert info.status is None

    def test_uncreatable_directory_points_at_the_path_not_a_file(self) -> None:
        """No database exists yet, so the hint must not say to delete one."""
        exc = self._error(
            NotADirectoryError(20, "Not a directory"), "create the cache directory"
        )
        info = classify(exc)
        assert info.code == "cache_error"
        assert "create the cache directory" in info.message
        assert info.hint is not None
        assert str(self.PATH.parent) in info.hint
        assert "XDG_CACHE_HOME" in info.hint
        assert "delete" not in info.hint

    def test_failed_purge_points_at_the_lock_or_the_file(self) -> None:
        """A purge fails on a locked or corrupt database; --fresh cannot help."""
        exc = self._error(
            sqlite3.OperationalError("database is locked"), "clear the cache"
        )
        info = classify(exc)
        assert info.code == "cache_error"
        assert "clear the cache" in info.message
        assert "sqlite3.OperationalError: database is locked" in info.message
        assert info.hint is not None
        assert f"delete {self.PATH}" in info.hint
        assert "--fresh" not in info.hint

    def test_text_and_json_carry_the_same_error(self) -> None:
        exc = self._error(sqlite3.OperationalError("unable to open database file"))
        info = classify(exc)
        assert format_error(exc) == f"✗ {info.message}\n  {info.hint}"
        assert json.loads(format_error_json(exc)) == {
            "error": {
                "code": "cache_error",
                "message": info.message,
                "hint": info.hint,
                "path": str(self.PATH),
            }
        }

    def test_without_a_cause_or_path(self) -> None:
        """A custom backend may raise it with neither; it still classifies."""
        info = classify(CacheError("backend unavailable"))
        assert info.code == "cache_error"
        assert "backend unavailable" in info.message
        assert info.path is None
        assert info.hint is not None
        assert "--fresh" in info.hint


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
            (CacheError("x"), "cache_error"),
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
