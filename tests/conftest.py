import importlib
import re
import sys
from pathlib import Path

import pytest

import config

TESTS_DIR = Path(__file__).resolve().parent
DATA_DIR = (TESTS_DIR.parent / "data").resolve()
READ_ONLY_OWNER_FILES = {"PROFILE_PATH"}

# A module that names a state file under data/ by a module-level relative path (LOGIN_ATTEMPTS_FILE =
# Path("data/...")). They are found by reading the source, and loaded now, so that the fixture below sees
# them however a test run is started -- a test file run alone imports its modules only inside the test,
# after the fixture has already looked.
_STATE_PATH = re.compile(r"""^\w+\s*=\s*(?:Path\(["']data/|(?:config\.)?DATA_DIR\s*/)""", re.MULTILINE)
for _source in sorted(TESTS_DIR.parent.glob("*.py")):
    if _STATE_PATH.search(_source.read_text(encoding="utf-8", errors="replace")):
        importlib.import_module(_source.stem)
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


@pytest.fixture(autouse=True)
def _keep_state_files_out_of_the_owners_data(monkeypatch, tmp_path):
    # The agent keeps records in data/ across runs (sign-in attempts, the questions it could not answer, ...),
    # named by a module-level path spelled relative to the repository. A test must neither read the owner's
    # history nor add to it: a test's made-up question once landed in the owner's real log. So every such path,
    # in whichever module holds it, points into this test's own folder -- a new one needs no entry here.
    # (tests/test_next_project_knowledge.py checks that none is missed, run alone or with the rest.)
    root = TESTS_DIR.parent
    for module in list(sys.modules.values()):
        where = getattr(module, "__file__", None)
        if not where or Path(where).resolve().parent != root:
            continue
        for name, value in list(vars(module).items()):
            if not isinstance(value, Path):
                continue
            if not value.is_absolute() and value.parts[:1] == ("data",):
                monkeypatch.setattr(module, name, tmp_path / value.name)
            # The same file named by its full path (profile_setup.ANSWERS_PATH, config.DB_PATH): a test's typed
            # 'First Name = Yash R.' was saved into the owner's real answers on 29 September. The profile itself
            # stays readable -- tests that need it read it, and none writes it without patching the path.
            elif value.is_absolute() and value.parent == DATA_DIR and name not in READ_ONLY_OWNER_FILES:
                monkeypatch.setattr(module, name, tmp_path / value.name)
