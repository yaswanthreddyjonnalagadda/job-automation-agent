"""reference/failures/ keeps a note of every failure and why it happened.

The notes are only worth keeping while they are complete and true, so this checks each entry has
every field, that ids are not reused, and that each test an entry names still exists.
"""
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
FAILURES = ROOT / "reference" / "failures"
FIELDS = ("id", "date", "class", "symptom", "site", "root_cause", "why_tests_missed", "fix", "tests",
          "prevention")


def entries():
    return [(path, json.loads(path.read_text(encoding="utf-8"))) for path in sorted(FAILURES.glob("f*.json"))]


def test_the_catalogue_has_entries():
    assert entries()


@pytest.mark.parametrize("path, entry", entries(), ids=lambda v: v.name if isinstance(v, Path) else "")
def test_every_entry_says_what_happened_and_why(path, entry):
    for name in FIELDS:
        assert entry.get(name), f"{path.name}: '{name}' is missing or empty"
    assert re.fullmatch(r"f\d{3}", entry["id"]) and path.name.startswith(entry["id"] + "-"), path.name
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", entry["date"]), path.name
    assert isinstance(entry["tests"], list) and all(isinstance(t, str) for t in entry["tests"]), path.name
    assert set(entry) <= set(FIELDS), f"{path.name}: unknown field(s) {sorted(set(entry) - set(FIELDS))}"


def test_no_id_is_used_twice():
    ids = [entry["id"] for _path, entry in entries()]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("path, entry", entries(), ids=lambda v: v.name if isinstance(v, Path) else "")
def test_every_test_an_entry_names_exists(path, entry):
    for reference in entry["tests"]:
        file_part, _, function = reference.partition("::")
        target = ROOT / file_part
        assert target.is_file(), f"{path.name}: {file_part} does not exist"
        if function:
            assert re.search(rf"def {re.escape(function)}\b", target.read_text(encoding="utf-8")), (
                f"{path.name}: {function} is not in {file_part}")
