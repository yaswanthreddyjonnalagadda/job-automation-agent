"""Anyone can set the agent up: a profile first, then saved answers used on every application.

The owner's decision of 29 September 2026: the agent is for anyone who runs it on their own computer. A new person
had no way in -- the profile was a JSON file edited by hand, and the resume and cover letter were named after the
owner in the code. These tests use a made-up person; nothing here reads the owner's data.
"""
import io
import json
from types import SimpleNamespace

import pytest

import config
import profile_setup
from apply_flow import document_name

RESUME = """JANE MARIE DOE
Senior Network Engineer
Springfield, IL | (217) 555-0142 | jane.doe@example.com | linkedin.com/in/jane-doe-net
SUMMARY
Network engineer with 7 years of experience in routing and cloud networking.
EXPERIENCE
Acme Corp, Springfield, IL | March 2021 - Present | Senior Network Engineer
"""


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A new person's data folder: nothing in it yet."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "PROFILE_PATH", tmp_path / "profile.json")
    monkeypatch.setattr(profile_setup, "ANSWERS_PATH", tmp_path / "profile_answers.json")
    monkeypatch.setattr(profile_setup, "UNANSWERED_PATH", tmp_path / "unanswered_questions.json")
    return tmp_path


# --- a draft from the resume ------------------------------------------------------------------

def test_the_plain_facts_are_read_from_the_resume():
    draft = profile_setup.draft_from_text(RESUME)
    assert draft == {"full_name": "Jane Marie Doe", "first_name": "Jane", "middle_name": "Marie", "last_name": "Doe",
                     "email": "jane.doe@example.com", "phone_mobile": "2175550142",
                     "linkedin_url": "https://linkedin.com/in/jane-doe-net", "city": "Springfield", "state": "IL",
                     "years_experience": 7}


def test_nothing_is_guessed_when_the_resume_does_not_say_it():
    draft = profile_setup.draft_from_text("Some Person\nsome.person@example.com\nA line about work.")
    assert set(draft) == {"full_name", "first_name", "middle_name", "last_name", "email"}


def ai(reply):
    calls = []

    def ask(system, user, tokens):
        calls.append((system, user))
        if isinstance(reply, Exception):
            raise reply
        return reply
    ask.calls = calls
    return ask


def test_the_ai_adds_the_work_history_and_the_plain_read_wins():
    reply = json.dumps({"full_name": "J. Doe", "current_position_title": "Senior Network Engineer",
                        "current_employer": "Acme Corp", "current_employment_dates": "March 2021 - Present",
                        "target_titles": ["Senior Network Engineer"],
                        "education": [["Master's", "Computer Science", "State University", "2019"]],
                        "made_up_field": "x"})
    ask = ai(reply)
    draft = profile_setup.draft_from_resume(RESUME, ask)
    assert draft["full_name"] == "Jane Marie Doe"                  # the resume's own words, not the AI's
    assert draft["current_employer"] == "Acme Corp"
    assert draft["education"] == (("Master's", "Computer Science", "State University", "2019"),)
    assert draft["target_titles"] == ("Senior Network Engineer",)
    assert "made_up_field" not in draft
    assert "never infer" in ask.calls[0][0]


@pytest.mark.parametrize("reply", ["not json at all", "", RuntimeError("no credit")])
def test_a_failed_ai_leaves_the_plain_read(reply):
    assert profile_setup.draft_from_resume(RESUME, ai(reply)) == profile_setup.draft_from_text(RESUME)


# --- the form and saving ----------------------------------------------------------------------

FORM = {"full_name": "Jane Marie Doe", "email": "jane.doe@example.com", "city": "Springfield", "state": "IL",
        "open_to_relocation": "on", "requires_visa_sponsorship": "", "years_experience": "7",
        "salary_min": "120,000", "salary_max": "150000", "target_titles": "Network Engineer, Cloud Engineer",
        "education": "Master's | Computer Science | State University | 2019\n\n",
        "us_citizen": "Yes", "felony_conviction": "Maybe"}


def test_the_form_is_read_in_each_fields_own_type():
    values = profile_setup.values_from_form(FORM)
    assert values["open_to_relocation"] is True and values["requires_visa_sponsorship"] is False
    assert values["years_experience"] == 7 and values["salary_min"] == 120000
    assert values["target_titles"] == ("Network Engineer", "Cloud Engineer")
    assert values["education"] == (("Master's", "Computer Science", "State University", "2019"),)
    assert values["us_citizen"] == "Yes"
    assert values["felony_conviction"] == ""                     # not one of the choices: left to be asked


@pytest.mark.parametrize("change, problem", [
    ({"full_name": ""}, "Full legal name is needed"),
    ({"email": ""}, "Email is needed"),
    ({"email": "not-an-address"}, "does not look right"),
    ({"salary_min": "200000"}, "above the top"),
])
def test_what_must_be_put_right_is_said(change, problem):
    values = profile_setup.values_from_form({**FORM, **change})
    assert any(problem in p for p in profile_setup.problems(values))


def test_a_saved_profile_is_the_one_the_agent_answers_from(home):
    (home / "profile.json").write_text(json.dumps({"answer_alternatives": {"A": ["B"]}, "not_a_field": 1}))
    profile_setup.save_profile(profile_setup.values_from_form(FORM))
    profile = config.get_user_profile()
    # Blank first and last names come from the full name; a blank middle name means none (the form says so).
    assert (profile.first_name, profile.middle_name, profile.last_name) == ("Jane", "", "Doe")
    assert profile.email == "jane.doe@example.com" and profile.years_experience == 7
    assert profile.current_location == "Springfield, IL"
    assert profile.answer_alternatives == {"A": ["B"]}           # a field the form does not show is kept
    assert "not_a_field" not in json.loads((home / "profile.json").read_text())


def test_an_invalid_profile_is_not_saved(home):
    with pytest.raises(ValueError):
        profile_setup.save_profile(profile_setup.values_from_form({**FORM, "email": ""}))
    assert not (home / "profile.json").exists()


def test_a_new_person_needs_setting_up_and_then_does_not(home):
    assert profile_setup.needs_setup()
    (home / "profile.json").write_text(json.dumps({"full_name": "Your Name", "email": "you@example.com"}))
    assert profile_setup.needs_setup()                            # the template's placeholders are not a profile
    profile_setup.save_profile(profile_setup.values_from_form(FORM))
    assert not profile_setup.needs_setup()


@pytest.mark.parametrize("full, parts", [("Jane Marie Doe", ("Jane", "Marie", "Doe")), ("Jane Doe", ("Jane", "", "Doe")),
                                         ("Jane", ("Jane", "", "")), ("", ("", "", ""))])
def test_names_are_split_first_middle_last(full, parts):
    assert profile_setup.split_name(full) == parts


# --- saved answers ----------------------------------------------------------------------------

def test_answers_are_added_changed_removed_and_blank_means_ask_me(home):
    profile_setup.save_answers({"Have you worked for a government agency?": "No", "re:notice period": "2 weeks",
                                "Preferred pronouns": "she/her"})
    profile_setup.save_answers({"re:notice period": "3 weeks", "Preferred pronouns": ""},
                               removed=("Have you worked for a government agency?",))
    saved = json.loads((home / "profile_answers.json").read_text())
    assert saved == {"re:notice period": "3 weeks"}


def test_saved_answers_are_shown_as_a_person_reads_them(home):
    (home / "profile_answers.json").write_text(json.dumps({"re:^\\s*job title\\b": "Engineer", "re:worked for \\w+ before": True}))
    rows = {r["key"]: r for r in profile_setup.saved_answers()}
    assert rows["re:^\\s*job title\\b"]["question"] == "job title"
    assert rows["re:worked for \\w+ before"]["answer"] == "Yes"


def test_a_question_the_agent_left_waits_until_it_is_answered(home):
    (home / "unanswered_questions.json").write_text(json.dumps({
        "a": {"question": "Do you hold a CDL?", "answer_key": "re:hold\\s+a\\s+cdl", "times": 3, "options": ["Yes", "No"]},
        "b": {"question": "Preferred shift?", "answer_key": "re:preferred\\s+shift", "times": 1}}))
    assert [q["question"] for q in profile_setup.waiting_questions()] == ["Do you hold a CDL?", "Preferred shift?"]
    profile_setup.save_answers({"re:hold\\s+a\\s+cdl": "No"})
    assert [q["question"] for q in profile_setup.waiting_questions()] == ["Preferred shift?"]


# --- documents carry the applicant's own name -------------------------------------------------

@pytest.mark.parametrize("profile, expected", [
    (SimpleNamespace(first_name="Jane", last_name="Doe", full_name="Jane Doe"), "Jane_Doe_Resume_AcmeCorp"),
    (SimpleNamespace(first_name="", last_name="", full_name="José O'Neil"), "Jos_O_Neil_Resume_AcmeCorp"),
    (SimpleNamespace(first_name="", last_name="", full_name=""), "Resume_AcmeCorp"),
])
def test_documents_are_named_after_the_applicant(profile, expected):
    assert document_name(profile, "Resume", "Acme Corp.") == expected


# --- the dashboard pages ----------------------------------------------------------------------

@pytest.fixture
def client(home):
    import web_ui
    web_ui.app.config["TESTING"] = True
    return web_ui.app.test_client()


def test_a_new_person_is_sent_to_setup_first(client):
    response = client.get("/")
    assert response.status_code == 302 and response.headers["Location"].endswith("/setup")
    assert b"Read my resume" in client.get("/setup").data


def test_only_a_pdf_or_docx_resume_is_taken(client, home):
    response = client.post("/setup", data={"resume": (io.BytesIO(b"hello"), "resume.txt")},
                           content_type="multipart/form-data")
    assert response.status_code == 302 and "must+be" in response.headers["Location"]
    assert not list(home.glob("resume*"))


def test_saving_the_profile_the_first_time_leads_to_the_saved_answers(client, home):
    response = client.post("/profile", data=FORM)
    assert response.status_code == 302 and "/answers" in response.headers["Location"]
    assert config.get_user_profile().email == "jane.doe@example.com"
    page = client.get("/profile").data
    assert b"Jane Marie Doe" in page and b"Network Engineer, Cloud Engineer" in page


def test_a_profile_with_problems_is_shown_again_with_them(client, home):
    response = client.post("/profile", data={**FORM, "email": "nope"})
    assert response.status_code == 200 and b"does not look right" in response.data
    assert not (home / "profile.json").exists()


def test_answers_are_saved_from_the_page(client, home):
    (home / "unanswered_questions.json").write_text(json.dumps({
        "a": {"question": "Do you hold a CDL?", "answer_key": "re:hold\\s+a\\s+cdl", "times": 2}}))
    assert b"Do you hold a CDL?" in client.get("/answers").data
    client.post("/answers", data={"key": ["re:hold\\s+a\\s+cdl"], "answer::re:hold\\s+a\\s+cdl": "No",
                                  "new_question": "Preferred shift?", "new_answer": "Day"})
    assert json.loads((home / "profile_answers.json").read_text()) == {"re:hold\\s+a\\s+cdl": "No",
                                                                       "Preferred shift?": "Day"}
