"""The pre-commit check refuses a commit whose code imports a project module that is not committed.

28 September: web_ui.py and browser_automation.py went to GitHub importing visible_desktop.py, which had not been
committed, so the code there could not start.
"""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

CHECK = Path(__file__).resolve().parents[1] / ".githooks" / "check_imports.py"


def run(repo, *args):
    return subprocess.run(list(args), cwd=repo, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    run(tmp_path, "git", "init", "-q")
    run(tmp_path, "git", "config", "user.email", "t@example.com")
    run(tmp_path, "git", "config", "user.name", "t")
    return tmp_path


def check(repo):
    return run(repo, sys.executable, str(CHECK))


def test_an_import_of_a_module_left_out_of_the_commit_is_refused(repo):
    (repo / "web_ui.py").write_text("import visible_desktop\n")
    (repo / "visible_desktop.py").write_text("x = 1\n")
    run(repo, "git", "add", "web_ui.py")
    result = check(repo)
    assert result.returncode == 1 and "visible_desktop" in result.stderr


def test_the_same_commit_with_the_module_in_it_goes_through(repo):
    (repo / "web_ui.py").write_text("import visible_desktop\nimport json\nfrom pathlib import Path\n")
    (repo / "visible_desktop.py").write_text("x = 1\n")
    run(repo, "git", "add", "web_ui.py", "visible_desktop.py")
    assert check(repo).returncode == 0


def test_a_file_that_does_not_parse_is_refused(repo):
    (repo / "broken.py").write_text("def f(:\n")
    run(repo, "git", "add", "broken.py")
    result = check(repo)
    assert result.returncode == 1 and "does not parse" in result.stderr
