"""Palantir incident: motivation coverage and a self-ID date under sibling headings."""
import json
from datetime import date

import pytest

import open_answers
import page_agent
from test_workday_prompt_textbox import agent, browser, page


@pytest.mark.parametrize("heading", ["Voluntary self-identification of disability", "Veteran self-identification"])
def test_sibling_heading_supplies_date_context_without_changing_question(heading):
    snapshot = f'''- heading "{heading}" [level=4] [ref=h1]
- heading "Why are you being asked?" [level=5] [ref=h2]
- paragraph: Participation is voluntary.
- heading "How to complete this form" [level=5] [ref=h3]
- paragraph: Please complete the following fields.
- textbox "Date" [ref=d1]
- heading "Employment" [level=4] [ref=h4]
- textbox "Date" [ref=d2]
'''
    a = agent()
    a.library_answer = lambda *args, **kwargs: ""
    dates = [c for c in page_agent.parse_snapshot(snapshot) if c.role == "textbox"]
    assert dates[0].question == dates[1].question == "Date"
    assert a.known_answer(dates[0]) == (date.today().isoformat(), "profile.application_date")
    assert a.known_answer(dates[1])[0] == ""


def test_date_context_ends_at_separator():
    snapshot = '''- heading "Disability self-identification" [level=4] [ref=h1]
- textbox "Date" [ref=d1]
- separator
- textbox "Date" [ref=d2]
'''
    a = agent()
    a.library_answer = lambda *args, **kwargs: ""
    fields = [c for c in page_agent.parse_snapshot(snapshot) if c.role == "textbox"]
    assert a.known_answer(fields[0])[0]
    assert not a.known_answer(fields[1])[0]


def test_date_heading_does_not_escape_its_containing_structure():
    snapshot = '''- generic [ref=g1]:
  - heading "Disability self-identification" [level=4] [ref=h1]
  - textbox "Date" [ref=d1]
- textbox "Date" [ref=d2]
- heading "Disability accommodation appointment" [level=4] [ref=h2]
- textbox "Date" [ref=d3]
'''
    a = agent()
    a.library_answer = lambda *args, **kwargs: ""
    fields = [c for c in page_agent.parse_snapshot(snapshot) if c.role == "textbox"]
    assert a.known_answer(fields[0])[0]
    assert not a.known_answer(fields[1])[0]
    assert not a.known_answer(fields[2])[0]


def test_self_identification_date_is_filled_in_requested_format(page):
    page.set_content('''<h4>Voluntary self-identification of disability</h4>
      <p>Completion is voluntary.</p><label>Name<input value="Example Candidate"></label>
      <label>Date<input name="eeo[disabilitySignatureDate]" placeholder="MM/DD/YYYY" required></label>
      <h4>Employment history</h4><label>Date<input id="history-date"></label>''')
    a = agent()
    a.library_answer = lambda *args, **kwargs: ""
    snapshot = a.read_when_loaded(page)
    controls = page_agent.parse_snapshot(snapshot)
    a.answer_what_is_known(page, controls, page_agent.required_questions(snapshot))
    assert page.locator('[name="eeo[disabilitySignatureDate]"]').input_value() == date.today().strftime("%m/%d/%Y")
    assert page.locator('#history-date').input_value() == ""
    assert page.get_by_label('Name', exact=True).input_value() == "Example Candidate"


QUESTION = "Why do you want to work at Acme?"
POSTING = "Build reliable networks that keep emergency response teams connected during outages."
RESUME = "I automated firewall policies and supported resilient networks."
WEAK = "I automated firewall policies and supported resilient networks. This matches my experience."
GOOD = ("This role offers the chance to keep emergency response teams connected during outages. "
        "I want to contribute to that work using my experience supporting resilient networks.")


def test_motivation_contract_rejects_qualifications_only_then_rewrites():
    calls = []
    drafts = iter([WEAK, GOOD])

    def ask(system, payload, tokens):
        calls.append((system, payload))
        if "Review a job application answer" in system:
            if 'motivation' not in system.lower():
                return json.dumps({"supported": True, "complete": True, "issues": []})
            good = json.loads(payload)['answer'] == GOOD
            return json.dumps({"supported": True, "complete": good,
                               "issues": [] if good else ["Missing an employer or role reason; qualifications alone are insufficient"]})
        assert 'motivation' in system.lower()
        return json.dumps({"answer": next(drafts)})

    answer, issues = open_answers.write(QUESTION, ask=ask, facts='', resume_text=RESUME, job_text=POSTING)
    assert answer == GOOD and not issues
    assert len(calls) == 4
    assert "qualifications alone" in calls[2][1]


def test_motivation_review_requires_employer_evidence_without_inventing_feelings():
    captured = []
    def ask(system, payload, tokens):
        captured.append((system, json.loads(payload)))
        return '{"supported":true,"complete":true,"issues":[]}'
    open_answers.review(QUESTION, GOOD, ask=ask, facts='', resume_text=RESUME, job_text=POSTING)
    assert 'motivation' in captured[0][0].lower()
    assert 'qualifications alone' in captured[0][0].lower()
    assert captured[0][1]['job_posting'] == POSTING


def test_unrepaired_motivation_remains_unapproved():
    calls = []
    def ask(system, payload, tokens):
        calls.append(system)
        if "Review a job application answer" in system:
            return '{"supported":true,"complete":false,"issues":["qualifications alone do not answer motivation"]}'
        return json.dumps({"answer": WEAK})
    answer, issues = open_answers.write(QUESTION, ask=ask, facts='', resume_text=RESUME, job_text=POSTING)
    assert answer == '' and issues
    assert len(calls) <= 4
