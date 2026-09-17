"""The reading agent: reads any page, answers from the owner's facts, moves on,
submits only when every check passes -- with no code written for the site.

The site here is made up, three steps long, built from the widget kinds real
career sites use: a plain box, a plain dropdown, a dropdown drawn by script,
radio buttons, a resume upload and a step-by-step wizard. Claude is replaced
by a planner that reads the same snapshot, so what is tested is the agent's
own reading, acting, checking and refusing.
"""

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

import config
import page_agent
import safety
from browser_automation import JobApplicationAssistant


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        yield b
        b.close()


@pytest.fixture
def page(browser):
    context = browser.new_context()
    pg = context.new_page()
    yield pg
    context.close()


@pytest.fixture
def resume_file(tmp_path):
    path = tmp_path / "Yaswanth_Jonnalagadda_Resume_Example.pdf"
    path.write_bytes(b"%PDF-1.4 test resume")
    return path


STEP_1 = """<html><body><h1>Apply: Network Engineer</h1><p>Step 1 of 2</p>
<form onsubmit="localStorage.first = first.value; localStorage.country = country.value;
                localStorage.sponsor = sponsor.value; location.href = '/apply/2'; return false;">
  <label for="first">First name *</label><input id="first" required>
  <label for="country">Country *</label>
  <select id="country"><option value="">-- Make a Selection --</option><option>India</option>
    <option>United States</option></select>
  <label for="sponsor">Will you now or in the future require visa sponsorship? *</label>
  <select id="sponsor">SPONSOR_OPTIONS</select>
  <button type="button">Finish Later</button>
  <button type="submit">Next</button>
</form></body></html>"""

STEP_2 = """<html><body><h1>Apply: Network Engineer</h1><p>Step 2 of 2</p>
<form onsubmit="localStorage.state = shown.textContent;
                localStorage.adult = (document.querySelector('[name=adult]:checked') || {}).value || '';
                localStorage.resume = resume.files.length ? resume.files[0].name : '';
                location.href = '/done'; return false;">
  <span id="stateLabel">State *</span>
  <div id="state" role="combobox" tabindex="0" aria-labelledby="stateLabel" aria-expanded="false"
       onclick="list.hidden = false"><span id="shown">Select</span></div>
  <ul id="list" role="listbox" hidden>
    <li role="option" onclick="shown.textContent='Texas'; list.hidden = true">Texas</li>
    <li role="option" onclick="shown.textContent='Virginia'; list.hidden = true">Virginia</li>
  </ul>
  <fieldset><legend>Are you at least 18 years old? *</legend>
    <label><input type="radio" name="adult" value="Yes"> Yes</label>
    <label><input type="radio" name="adult" value="No"> No</label></fieldset>
  <label for="resume">Resume *</label><input type="file" id="resume">
  CERTIFY
  <button type="submit">Submit Application</button>
</form></body></html>"""

DONE = "<html><body><h1>Thank you for applying!</h1><p>We have received your application.</p></body></html>"


def serve(page, step1=STEP_1, step2=STEP_2, sponsor_options='<option value="">-- Make a Selection --</option>'
          '<option>Yes</option><option>No</option>', certify=""):
    pages = {"/apply/1": step1.replace("SPONSOR_OPTIONS", sponsor_options),
             "/apply/2": step2.replace("CERTIFY", certify), "/done": DONE}
    page.route("https://jobs.example.com/**", lambda route: route.fulfill(
        status=200, content_type="text/html",
        body=pages.get("/" + route.request.url.split("/", 3)[3].split("?")[0], "not found")))
    page.goto("https://jobs.example.com/apply/1")


def ref_of(snapshot: str, role: str, name: str) -> str:
    m = re.search(rf'- {role} "{re.escape(name)}"[^\n]*\[ref=([\w-]+)\]', snapshot)
    return m.group(1) if m else ""


class Planner:
    """Stands in for Claude: plans each page from the same snapshot the agent reads."""

    def __init__(self, extra=None, next_label=None):
        self.extra, self.next_label, self.calls = extra or {}, next_label, 0

    def plan_page(self, snapshot, facts, feedback=""):
        self.calls += 1
        assert facts["profile"]["full_name"]  # the owner's facts are handed over
        if "Thank you for applying" in snapshot:
            return {"page_kind": "confirmation", "next": {"kind": "none"}}
        if "Step 1 of 2" in snapshot:
            answers = [
                {"ref": ref_of(snapshot, "textbox", "First name *"), "question": "First name", "action": "fill",
                 "value": "Yaswanth", "source": "profile.full_name"},
                {"ref": ref_of(snapshot, "combobox", "Country *"), "question": "Country", "action": "choose",
                 "value": "United States", "source": "profile.country"},
                {"ref": ref_of(snapshot, "combobox", "Will you now or in the future require visa sponsorship? *"),
                 "question": "sponsorship", "action": "choose", "value": "Yes",
                 "source": "profile.requires_visa_sponsorship"},
            ] + self.extra.get(1, [])
            label = self.next_label or "Next"
            return {"page_kind": "application_form", "step": "1 of 2", "answers": answers,
                    "next": {"ref": ref_of(snapshot, "button", label), "label": label, "kind": "next_step"}}
        answers = [
            {"ref": ref_of(snapshot, "combobox", "State *"), "question": "State", "action": "choose",
             "value": "Virginia", "source": "profile.state"},
            {"ref": ref_of(snapshot, "radio", "Yes"), "question": "Are you at least 18 years old?",
             "action": "check", "value": "Yes", "source": "profile.at_least_18"},
            {"ref": ref_of(snapshot, "button", "Resume *") or ref_of(snapshot, "button", "Choose File"),
             "question": "Resume", "action": "upload_resume", "value": "resume", "source": "document"},
        ] + self.extra.get(2, [])
        return {"page_kind": "application_form", "step": "2 of 2", "answers": answers,
                "next": {"ref": ref_of(snapshot, "button", "Submit Application"), "label": "Submit Application",
                         "kind": "final_submit"}}


def make_agent(planner, resume_file, auto_submit=True, profile=None):
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    assistant.values = safety.AgentValues()
    cfg = SimpleNamespace(auto_submit=auto_submit, ats_email="")
    job = SimpleNamespace(title="Network Engineer", company="Example", url="https://jobs.example.com/apply/1")
    return page_agent.PageAgent(assistant, planner, cfg, profile or config.get_user_profile(),
                                SimpleNamespace(raw_text="Network engineer, 6 years"), job,
                                resume_file=resume_file)


def stored(page, key):
    return page.evaluate(f"localStorage.{key} || ''")


# --- reading ---------------------------------------------------------------------

def test_the_page_is_read_as_questions_answers_and_choices(page):
    serve(page)
    controls = page_agent.parse_snapshot(page.locator("body").aria_snapshot(mode="ai"))
    by_name = {c.question: c for c in controls}
    country = by_name["Country *"]
    assert country.role == "combobox" and "United States" in country.options and country.answer == ""
    assert by_name["Next"].role == "button" and by_name["First name *"].role == "textbox"


def test_a_radio_button_carries_its_question():
    snapshot = """- group "Are you at least 18 years old? *" [ref=e5]:
  - radio "Yes" [ref=e6]
  - radio "No" [checked] [ref=e7]"""
    controls = page_agent.parse_snapshot(snapshot)
    assert [c.group for c in controls] == ["Are you at least 18 years old? *"] * 2
    assert page_agent.answered_fields(controls) == [{"label": "Are you at least 18 years old? *", "value": "No"}]


# --- a whole application ------------------------------------------------------------

def test_a_whole_application_is_read_answered_and_submitted(page, resume_file):
    serve(page)
    planner = Planner()
    outcome = make_agent(planner, resume_file).run(page)
    assert outcome.kind == "submitted", outcome.reasons
    assert stored(page, "first") == "Yaswanth"
    assert stored(page, "country") == "United States"
    assert stored(page, "sponsor") == "Yes"
    assert stored(page, "state") == "Virginia"          # the dropdown drawn by script
    assert stored(page, "adult") == "Yes"
    assert stored(page, "resume") == resume_file.name


def test_a_carried_over_no_to_sponsorship_stops_before_anything_is_sent(page, resume_file):
    serve(page, sponsor_options='<option value="">-- Make a Selection --</option><option>Yes</option>'
                                '<option selected="selected">No</option>')
    outcome = make_agent(Planner(), resume_file).run(page)
    assert outcome.kind == "owner_needed"
    assert any("sponsorship" in r for r in outcome.reasons)
    assert page.url.endswith("/apply/1")                # never pressed Next with it
    assert page.locator("#sponsor").input_value() == "No"  # and never changed it


def test_nothing_is_sent_when_automatic_submission_is_off(page, resume_file):
    serve(page)
    outcome = make_agent(Planner(), resume_file, auto_submit=False).run(page)
    assert outcome.kind == "owner_needed"
    assert "automatic submission is off" in outcome.summary
    assert page.url.endswith("/apply/2")


def test_a_declaration_waiting_on_the_last_page_stops_the_submit(page, resume_file):
    serve(page, certify='<label><input type="checkbox" id="cert"> I certify that the information above is true '
                        'and complete</label>')
    planner = Planner(extra={2: [{"ref": "", "question": "cert", "action": "check", "value": "", "source": "consent"}]})
    outcome = make_agent(planner, resume_file).run(page)
    assert outcome.kind == "owner_needed"
    assert not page.locator("#cert").is_checked()
    assert page.url.endswith("/apply/2")


# --- what is refused, whatever Claude proposes -------------------------------------------

def refusal_for(page, resume_file, html, action, value, source="profile.city", role="textbox", name="Box"):
    page.set_content(html)
    agent = make_agent(Planner(), resume_file)
    controls = page_agent.parse_snapshot(agent.snapshot(page))
    control = next(c for c in controls if c.role == role and c.question.startswith(name))
    return agent.refusal(page, page_agent.Answer(control.ref, control.question, action, value, source), control, controls)


def test_a_declaration_is_never_ticked(page, resume_file):
    why = refusal_for(page, resume_file, '<label><input type="checkbox"> I certify my answers are true and complete'
                      '</label>', "check", "", "consent", role="checkbox", name="I certify")
    assert "declaration" in why


def test_a_password_is_never_typed(page, resume_file):
    why = refusal_for(page, resume_file, '<label>Box <input type="password"></label>', "fill", "secret")
    assert "password" in why


def test_an_answer_someone_else_gave_is_never_changed(page, resume_file):
    why = refusal_for(page, resume_file, '<label>Box <input value="11092 Lee Highway"></label>', "fill", "9365 Lee Hwy")
    assert "already answered" in why


def test_a_legal_question_is_answered_only_from_a_profile_field_that_states_it(page, resume_file):
    html = '<label>Have you ever been convicted of a felony? <input></label>'
    assert "legal" in refusal_for(page, resume_file, html, "fill", "No", source="resume", name="Have you ever")
    assert refusal_for(page, resume_file, html, "fill", "No", source="profile.felony_conviction",
                       name="Have you ever") == ""


def test_finish_later_is_never_pressed(page, resume_file):
    serve(page)
    outcome = make_agent(Planner(next_label="Finish Later"), resume_file).run(page)
    assert outcome.kind == "owner_needed" and "never presses" in outcome.summary
    assert page.url.endswith("/apply/1")


def test_a_captcha_is_left_to_the_owner(page, resume_file):
    page.set_content("<h1>Verify you are human</h1>")
    planner = SimpleNamespace(plan_page=lambda s, f, fb="": {"page_kind": "captcha", "next": {"kind": "none"}})
    assert make_agent(planner, resume_file).run(page).kind == "captcha"


def test_a_confirmation_the_agent_did_not_cause_is_not_recorded_as_its_submission(page, resume_file):
    page.set_content(DONE)
    planner = SimpleNamespace(plan_page=lambda s, f, fb="": {"page_kind": "confirmation", "next": {"kind": "none"}})
    outcome = make_agent(planner, resume_file).run(page)
    assert outcome.kind == "owner_needed" and "already sent" in outcome.summary


def test_an_answer_that_does_not_stay_is_never_submitted_blank(page, resume_file):
    # The State list opens but ignores every choice.
    broken = STEP_2.replace("onclick=\"shown.textContent='Texas'; list.hidden = true\"", "") \
                   .replace("onclick=\"shown.textContent='Virginia'; list.hidden = true\"", "")
    serve(page, step2=broken)
    outcome = make_agent(Planner(), resume_file).run(page)
    assert outcome.kind == "owner_needed"
    assert any("could not set" in r and "State" in r for r in outcome.reasons)
    assert page.url.endswith("/apply/2")
