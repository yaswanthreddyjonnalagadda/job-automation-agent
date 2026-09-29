"""What the person answers while applying becomes their saved answer, used on every form after.

Before: an answer typed on a form was kept in the database for that employer only, never shown on the dashboard,
and any 'Yes' or 'No' was never learned at all ('Yes' and 'No' are in every profile, and profile values were
skipped). Now a general question answered in a few words joins data/profile_answers.json -- which every form is
answered from first -- and anything about the one employer, an essay, or a legal or signed answer does not.
"""
import json
from types import SimpleNamespace

import pytest

import apply_flow
import config
import profile_setup


@pytest.mark.parametrize("question, answer", [
    ("Are you willing to work night shifts?", "Yes"),
    ("How many years of Python experience do you have?", "6"),
    ("What is your notice period?", "2 weeks"),
    ("Do you have a valid driver's license?", "Yes"),
    ("Highest level of education completed", "Master's Degree"),
])
def test_a_general_question_answered_in_a_few_words_is_kept(question, answer):
    assert profile_setup.worth_keeping(question, answer, company="Acme Corp")


@pytest.mark.parametrize("question, answer", [
    ("Have you ever worked for Acme before?", "No"),                       # names the employer
    ("Have you worked for this company before?", "No"),
    ("Were you referred by a current employee?", "Yes"),
    ("Why do you want to work here?", "Because the team builds networks."),
    ("Why are you interested in this role?", "I like it."),
    ("Tell us about a project", "x" * 250),                                # an essay
    ("Are you legally authorized to work in the United States?", "Yes"),   # legal status
    ("Do you require visa sponsorship (H-1B)?", "No"),
    ("I certify that the information above is true and complete", "Yes"),  # a declaration
    ("Name", "Jane"),                                                      # too short to be a question
    ("What is your notice period?", ""),
])
def test_anything_about_the_employer_an_essay_or_a_legal_answer_is_not(question, answer):
    assert not profile_setup.worth_keeping(question, answer, company="Acme Corp")


def test_a_kept_answer_is_saved_once_and_never_over_what_the_person_set(tmp_path):
    path = tmp_path / "profile_answers.json"
    path.write_text(json.dumps({"re:notice\\s+period": "1 month", "Preferred shift": "Day"}))
    assert not profile_setup.remember_answer("What is your notice period?", "2 weeks", path=path)  # a pattern covers it
    assert not profile_setup.remember_answer("preferred shift", "Night", path=path)                 # already saved
    assert profile_setup.remember_answer("Are you willing to work night shifts? *", "Yes", path=path)
    assert json.loads(path.read_text()) == {"re:notice\\s+period": "1 month", "Preferred shift": "Day",
                                            "Are you willing to work night shifts?": "Yes"}


# --- through the agent's learning step -----------------------------------------------------------

class Tracker:
    def __init__(self):
        self.recorded = []

    def record_answer(self, key, host, question, answer, answered_by):
        self.recorded.append((question, answer))


def assistant(fields):
    return SimpleNamespace(read_back_fields=lambda page: fields,
                           values=SimpleNamespace(wrote_value=lambda page, value: False))


def test_the_persons_answers_on_a_form_join_their_saved_answers(tmp_path, monkeypatch):
    monkeypatch.setattr(profile_setup, "ANSWERS_PATH", tmp_path / "profile_answers.json")
    fields = [
        {"label": "Are you willing to work night shifts?", "value": "Yes", "source": "user"},
        {"label": "Have you worked for Acme before?", "value": "No", "source": "user"},
        {"label": "What is your notice period?", "value": "2 weeks", "source": "agent"},    # the agent's own
        {"label": "Current city of residence", "value": "Springfield", "source": "user"},   # in the profile
    ]
    profile = config.UserProfile(email="jane@example.com", city="Springfield", felony_conviction="No")
    tracker = Tracker()
    page = SimpleNamespace(url="https://acme.wd1.myworkdayjobs.com/apply")
    learned = apply_flow.learn_user_answers(assistant(fields), page, tracker, "k", profile, "Acme Corp")
    # 'Yes' is in the profile (a Yes/No field) and was once skipped; it is learned now.
    assert ("Are you willing to work night shifts?", "Yes") in tracker.recorded and learned == 2
    assert json.loads((tmp_path / "profile_answers.json").read_text()) == {
        "Are you willing to work night shifts?": "Yes"}
