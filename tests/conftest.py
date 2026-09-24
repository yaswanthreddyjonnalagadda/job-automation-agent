import sys
from pathlib import Path

import pytest

import config

TESTS_DIR = Path(__file__).resolve().parent
NO_PROFILE = "needs the owner's real profile (data/profile.json, or the PROFILE_JSON secret in CI)"


@pytest.fixture(autouse=True)
def _skip_a_test_that_asks_for_a_profile_nobody_supplied(monkeypatch):
    # Until the tests use synthetic fixtures (RFC-001, M2), some read the
    # owner's real profile. Where there is none, such a test is skipped rather
    # than failed on placeholder values. Only a call made from a test counts:
    # production code that calls get_user_profile() keeps getting the real one.
    real = config.get_user_profile

    def guarded():
        caller = Path(sys._getframe(1).f_code.co_filename).resolve()
        if TESTS_DIR in caller.parents and not config.PROFILE_PATH.is_file():
            pytest.skip(NO_PROFILE)
        return real()

    monkeypatch.setattr(config, "get_user_profile", guarded)
