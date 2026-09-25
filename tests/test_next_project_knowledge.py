"""One JSON file for the next project: every failure the agent has met, and every question it had to leave.

The owner asked (25 September 2026) to keep a note of each failure and each question the agent could not
answer, so the next project can build the fixes in from the start and start with the answers. The failure
notes live one per file in reference/failures/ and the questions in data/unanswered_questions.json;
next_project_knowledge.py puts them side by side.

It found a fault on the way: a test that built an agent wrote its made-up question ("I certify ...
signature", Example, five times) into the owner's real data/unanswered_questions.json, because only the
sign-in record was kept out of the owner's data for tests. The class: a module-level state file the test
setup does not know about. The last tests here cover the whole class, not the one file.
"""
import json
import sys
from pathlib import Path

import pytest

import next_project_knowledge as knowledge

ROOT = Path(__file__).resolve().parents[1]
FAILURE_FIELDS = ("id", "date", "class", "symptom", "site", "root_cause", "why_tests_missed", "fix", "tests",
                  "prevention")


def a_failure(number: int, **over) -> dict:
    entry = {"id": f"f{number:03d}", "date": "2026-09-25", "class": f"class {number}", "symptom": "s",
             "site": "somewhere", "root_cause": "r", "why_tests_missed": "w", "fix": "f",
             "tests": ["tests/test_x.py"], "prevention": f"build in {number}"}
    entry.update(over)
    return entry


def a_question(text: str, times: int = 1, **over) -> dict:
    entry = {"question": text, "required": True, "first_seen": "2026-09-24", "times": times,
             "sites": ["jobs.somewhere.com"], "companies": ["Somewhere"], "options": ["Yes", "No"],
             "last_seen": "2026-09-25", "reason": "not in your profile", "answer_key": "re:" + text.lower(),
             "answer": None}
    entry.update(over)
    return entry


@pytest.fixture
def sources(tmp_path):
    failures = tmp_path / "failures"
    failures.mkdir()
    for number in (3, 1, 2):
        entry = a_failure(number)
        (failures / f"{entry['id']}-thing.json").write_text(json.dumps(entry), encoding="utf-8")
    (failures / "README.md").write_text("not an entry", encoding="utf-8")
    asked = tmp_path / "unanswered_questions.json"
    asked.write_text(json.dumps({
        "rare": a_question("Rare one", times=1),
        "often": a_question("Often asked", times=7),
        "middle": a_question("Middle", times=3),
    }), encoding="utf-8")
    return failures, asked


def built(sources, **kw):
    failures, asked = sources
    return knowledge.build(failures_dir=failures, unanswered_file=asked, today="2026-09-25", **kw)


# --- what goes in ---------------------------------------------------------------------------------

def test_every_failure_is_in_by_id_and_whole(sources):
    out = built(sources)
    assert [f["id"] for f in out["failures"]] == ["f001", "f002", "f003"]
    assert out["failures"][0] == a_failure(1)


def test_a_note_that_is_not_an_entry_is_left_out(sources):
    assert len(built(sources)["failures"]) == 3


def test_every_question_is_in_the_most_asked_first_with_its_key_and_every_field(sources):
    asked = built(sources)["unanswered_questions"]
    assert [q["question"] for q in asked] == ["Often asked", "Middle", "Rare one"]
    assert [q["times"] for q in asked] == [7, 3, 1]
    assert asked[0]["key"] == "often"
    assert {k: v for k, v in asked[0].items() if k != "key"} == a_question("Often asked", times=7)


def test_what_to_build_in_from_the_start_is_one_line_per_failure(sources):
    lines = built(sources)["build_in_from_the_start"]
    assert lines == [{"id": f"f00{n}", "class": f"class {n}", "prevention": f"build in {n}"} for n in (1, 2, 3)]


def test_it_says_what_it_is_and_when_it_was_made(sources):
    out = built(sources)
    assert out["generated"] == "2026-09-25" and out["about"]
    assert out["counts"] == {"failures": 3, "unanswered_questions": 3}


# --- what is not there, or is broken -------------------------------------------------------------------

def test_no_question_log_still_gives_the_failures(sources):
    failures, asked = sources
    asked.unlink()
    out = built(sources)
    assert out["unanswered_questions"] == [] and len(out["failures"]) == 3


@pytest.mark.parametrize("content", ["", "{not json", "[1, 2]", '"text"', "null"])
def test_a_broken_question_log_is_skipped_not_fatal(sources, content):
    failures, asked = sources
    asked.write_text(content, encoding="utf-8")
    out = built(sources)
    assert out["unanswered_questions"] == [] and len(out["failures"]) == 3


def test_a_line_in_the_log_that_is_not_a_question_is_skipped(sources):
    failures, asked = sources
    data = json.loads(asked.read_text(encoding="utf-8"))
    data["junk"] = "a string"
    data["also junk"] = ["a", "list"]
    asked.write_text(json.dumps(data), encoding="utf-8")
    assert len(built(sources)["unanswered_questions"]) == 3


def test_a_failure_file_that_will_not_parse_is_named_not_swallowed(sources):
    failures, _ = sources
    (failures / "f009-bad.json").write_text("{oops", encoding="utf-8")
    with pytest.raises(ValueError, match="f009-bad.json"):
        built(sources)


# --- the file ---------------------------------------------------------------------------------------

def test_it_writes_one_readable_json_file_and_the_same_one_twice(sources, tmp_path):
    failures, asked = sources
    out = tmp_path / "out" / "next_project_knowledge.json"
    knowledge.write(out, failures_dir=failures, unanswered_file=asked, today="2026-09-25")
    first = out.read_text(encoding="utf-8")
    knowledge.write(out, failures_dir=failures, unanswered_file=asked, today="2026-09-25")
    assert out.read_text(encoding="utf-8") == first
    assert json.loads(first)["counts"]["failures"] == 3
    assert not list(out.parent.glob("*.tmp"))


def test_non_ascii_text_is_kept_readable(sources, tmp_path):
    failures, asked = sources
    entry = a_failure(4, symptom="the form said “No” — and kept it")
    (failures / "f004-quotes.json").write_text(json.dumps(entry), encoding="utf-8")
    out = tmp_path / "k.json"
    knowledge.write(out, failures_dir=failures, unanswered_file=asked, today="2026-09-25")
    assert "“No” —" in out.read_text(encoding="utf-8")


def test_the_real_catalogue_goes_in_whole():
    """Whatever is in reference/failures/ today is in the file: nothing to keep in step by hand."""
    real = sorted(p for p in (ROOT / "reference" / "failures").glob("f*.json"))
    out = knowledge.build(unanswered_file=Path("no-such-file.json"))
    assert len(real) >= 22
    assert [f["id"] for f in out["failures"]] == [json.loads(p.read_text(encoding="utf-8"))["id"] for p in real]
    assert all(all(f.get(field) for field in FAILURE_FIELDS) for f in out["failures"])


def test_the_file_it_writes_by_default_can_never_be_committed_by_accident():
    """It names the employers the owner applied to, so like the rest of the owner's records in data/ it is
    ignored by git (found untracked-but-not-ignored the day it was first written)."""
    import subprocess
    real = f"data/{knowledge.OUTPUT_FILE.name}"        # during a test OUTPUT_FILE itself points into a temp folder
    try:
        run = subprocess.run(["git", "check-ignore", "-q", "--no-index", real], cwd=ROOT, capture_output=True,
                             timeout=30)
    except (OSError, subprocess.SubprocessError):
        pytest.skip("git is not available here")
    assert run.returncode != 128, "not a git checkout"
    assert run.returncode == 0, f"{real} is not ignored by git"


# --- tests keep out of the owner's data (the class behind the "Example" entry) --------------------------

def project_modules():
    for module in list(sys.modules.values()):
        where = getattr(module, "__file__", None) or ""
        if where and Path(where).resolve().parent == ROOT:
            yield module


def test_no_module_level_path_into_data_is_left_pointing_at_the_owners_files():
    """The setup redirects every module-level state path under data/ for a test, whichever module holds it and
    however it is spelled: a new one is covered without anyone remembering to list it."""
    import login_guard, page_agent  # noqa: F401  (make sure the two known holders are loaded)
    left = [f"{module.__name__}.{name} = {value}"
            for module in project_modules()
            for name, value in vars(module).items()
            if isinstance(value, Path) and not value.is_absolute() and value.parts[:1] == ("data",)]
    assert not left, f"still the owner's real data during a test: {left}"


def test_an_agent_that_records_a_question_does_not_write_the_owners_log(tmp_path):
    import page_agent
    real = ROOT / "data" / "unanswered_questions.json"
    before = real.read_bytes() if real.exists() else None
    assert not (page_agent.UNANSWERED_FILE.parts[:1] == ("data",))
    agent = page_agent.PageAgent.__new__(page_agent.PageAgent)
    agent.unanswered_path = page_agent.UNANSWERED_FILE
    agent.job = type("Job", (), {"company": "Example"})()
    agent._current_host = "jobs.example.com"
    agent.library_answer = lambda question: None
    agent.record_unanswered("I certify signature", "a declaration", [])
    assert (real.read_bytes() if real.exists() else None) == before
