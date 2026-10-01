import importlib
import re
import sys
from pathlib import Path

import pytest

import config

TESTS_DIR = Path(__file__).resolve().parent
DATA_DIR = (TESTS_DIR.parent / "data").resolve()
# The applicant every test uses: made up, complete, the same on every machine. The owner's data/profile.json is
# never read by a test (architecture review, 1 October): 72 tests used to skip wherever it was missing -- in CI and in
# every worktree -- and the rest passed or failed on whatever the owner's profile said that day.
SYNTHETIC_PROFILE = TESTS_DIR / "fixtures" / "profile.synthetic.json"

# A module that names a state file under data/ by a module-level relative path (LOGIN_ATTEMPTS_FILE =
# Path("data/...")). They are found by reading the source, and loaded now, so that the fixture below sees
# them however a test run is started -- a test file run alone imports its modules only inside the test,
# after the fixture has already looked.
_STATE_PATH = re.compile(r"""^\w+\s*=\s*(?:Path\(["']data/|(?:config\.)?DATA_DIR\s*/)""", re.MULTILINE)
for _source in sorted(TESTS_DIR.parent.glob("*.py")):
    if _STATE_PATH.search(_source.read_text(encoding="utf-8", errors="replace")):
        importlib.import_module(_source.stem)


@pytest.fixture(autouse=True)
def _the_synthetic_applicant(monkeypatch, tmp_path):
    """Every test reads a fresh copy of the made-up profile, wherever the code under test asks for the profile."""
    copy = tmp_path / "profile.json"
    copy.write_text(SYNTHETIC_PROFILE.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(config, "PROFILE_PATH", copy)


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
            # 'First Name = Yash R.' was saved into the owner's real answers on 29 September. The profile is
            # pointed at the synthetic applicant by _the_synthetic_applicant above.
            elif value.is_absolute() and value.parent == DATA_DIR and not (module is config and name == "PROFILE_PATH"):
                monkeypatch.setattr(module, name, tmp_path / value.name)
