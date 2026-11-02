"""The saved corpus of real application questions is the engine's benchmark (f230).

reference/question_corpus.json holds the questions read from public Greenhouse job boards (scripts/question_coverage.py
harvest): text, field type and option labels, nothing about a person. The engine runs over it with a synthetic person.
Coverage may only go up, and the answers that would be wrong to give stay unanswered.
"""
import json
from pathlib import Path

import pytest

import answer_engine as ae
from test_answer_engine import OWNER, RESUME

CORPUS = json.loads((Path(__file__).resolve().parent.parent / "reference" / "question_corpus.json").read_text(encoding="utf-8"))["questions"]
ROLE = {"multi_value_single_select": "combobox", "multi_value_multi_select": "checkbox"}


def run(row, **kw):
    return ae.answer(ae.Ask(question=row["label"], options=row["options"], role=ROLE.get(row["type"], "textbox"), profile=OWNER,
                            company="Globex", resume_text=RESUME, employers=["Acme Corp"], **kw))[0]


def test_the_engine_answers_at_least_a_quarter_of_the_corpus_and_the_floor_only_rises():
    answered = sum(1 for row in CORPUS if run(row))
    assert len(CORPUS) >= 300
    assert answered >= 80, f"the engine answered {answered} of {len(CORPUS)}; it answered 90 when this floor was set"


@pytest.mark.parametrize("fragment", ["gender", "race", "veteran", "pronoun"])
def test_who_the_owner_is_is_never_answered_by_the_engine(fragment):
    rows = [r for r in CORPUS if fragment in r["label"].lower()]
    assert rows, fragment
    assert all(run(r) == "" for r in rows)


def test_no_free_text_box_is_ever_answered_yes_or_no():
    for row in CORPUS:
        if row["type"] in ("input_text", "textarea") and not row["options"]:
            assert run(row) not in ("Yes", "No"), row["label"]


def test_no_answer_claims_more_years_than_the_owner_has_or_a_skill_the_resume_lacks():
    for row in CORPUS:
        label = row["label"].lower()
        if row["options"] and label.startswith(("do you have", "have you")) and "year" in label:
            import re
            wanted = re.search(r"(\d+)\+?\s*years", label)
            if wanted and int(wanted.group(1)) > 6:
                assert run(row) in ("", "No"), row["label"]


@pytest.mark.parametrize("label, expected", [
    ("How did you hear about this role?", "LinkedIn"),
    ("What is the name of your current or most recent employer?", "Acme Corp"),
    ("Are you legally entitled to work in Canada?", "No"),
    ("Are you fluent in German or French?", "No"),
    ("Do you possess business fluency in Japanese?", "No"),
])
def test_known_corpus_questions_get_the_right_answer(label, expected):
    rows = [r for r in CORPUS if r["label"].lower().startswith(label.lower()[:40])]
    assert rows, label
    assert run(rows[0]) in (expected, expected + "."), (label, run(rows[0]))
