"""The suite's results must not depend on what the developer's shell exports.

`AGENT_DISCOGS_DEBUG` adds the debug panel to stderr, which CliRunner mixes
into `result.output`, and the `DISCOGS_*` variables pick who the SDK
authenticates as. Offline tests run without them; live tests need them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import ISOLATED_ENV

_INNER_TESTS = f"""
import os

import pytest

NAMES = {ISOLATED_ENV!r}


def test_offline_sees_none() -> None:
    assert [n for n in NAMES if n in os.environ] == []


@pytest.mark.live
def test_live_sees_all() -> None:
    assert [n for n in NAMES if os.environ.get(n) != "from-shell"] == []
"""


def test_offline_tests_drop_shell_env_and_live_tests_keep_it(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ISOLATED_ENV:
        monkeypatch.setenv(name, "from-shell")
    pytester.makeconftest(Path(__file__).with_name("conftest.py").read_text())
    pytester.makeini("[pytest]\nmarkers =\n    live: hits the real Discogs API\n")
    pytester.makepyfile(_INNER_TESTS)

    pytester.runpytest("-p", "no:cacheprovider").assert_outcomes(passed=2)
