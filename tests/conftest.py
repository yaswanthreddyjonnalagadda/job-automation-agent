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


_IN_THE_REPOSITORY_ROOT: dict[str, bool] = {}


def _in_the_repository_root(where: str, root: Path) -> bool:
    """Whether a module file sits directly in the repository root. Path.resolve() on a Windows path costs about
    0.2 ms, and the fixture below asked it of every loaded module (about 2,500) before every test: 0.5 s of each
    test's setup, about half an hour over the suite (24 October 2026). A module's location does not change in a
    run, so each is asked once."""
    found = _IN_THE_REPOSITORY_ROOT.get(where)
    if found is None:
        found = _IN_THE_REPOSITORY_ROOT[where] = Path(where).resolve().parent == root
    return found


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
        if not where or not _in_the_repository_root(where, root):
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


@pytest.fixture(autouse=True)
def _instant_typing_unless_a_test_asks(monkeypatch):
    # The agent types at a person's pace (interaction.type_like_a_person). Across hundreds of tests that
    # would only add waiting, so tests fill instantly; tests/test_human_typing.py turns typing back on.
    monkeypatch.setenv("HUMAN_TYPING", "0")


_STARTS_PLAYWRIGHT: dict[str, bool] = {}


def pytest_collection_modifyitems(config, items):
    """Marks every test that gets a real Chromium as `browser`: `pytest -m "not browser"` is the fast tier (no
    browser to start); the browser tests keep running in CI and before a commit. A test counts when it asks for a
    `browser` or `page` fixture (also one imported from another test file) or its file starts Playwright itself, so a
    new browser test needs no marker."""
    for item in items:
        path = str(item.fspath)
        if path not in _STARTS_PLAYWRIGHT:
            _STARTS_PLAYWRIGHT[path] = "sync_playwright" in Path(path).read_text(encoding="utf-8", errors="replace")
        if _STARTS_PLAYWRIGHT[path] or {"browser", "page"} & set(item.fixturenames):
            item.add_marker(pytest.mark.browser)
