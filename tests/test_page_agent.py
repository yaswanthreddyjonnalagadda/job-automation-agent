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
from sites.workday import WorkdayAdapter


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


def _recorded_page(*parts: str) -> str:
    """A page recorded during a real run.

    Recordings live in output/, which is gitignored because they carry
    personal answers. Where one is missing -- on CI or a fresh clone -- the
    test is skipped instead of failing with FileNotFoundError.
    """
    path = Path(__file__).parents[1].joinpath("output", *parts)
    if not path.is_file():
        pytest.skip("needs the local recording output/" + "/".join(parts))
    return path.read_text(encoding="utf-8")


def test_workday_answer_buttons_use_their_question_text():
    snapshot = _recorded_page("The_Options_Clearing_Corporation_Associate_Principal,_Cloud_Engineering", "pages", "page_53.txt")
    controls = page_agent.parse_snapshot(snapshot)
    questions = {control.question for control in controls if control.role == "button"}
    assert "Have you ever worked for OCC as an intern or employee?*" in questions
    assert "What are your bonus expectations?*" in questions
    assert "No Required" not in questions


def test_workday_empty_application_shell_is_treated_as_loading():
    snapshot = _recorded_page("The_Options_Clearing_Corporation_Associate_Principal,_Cloud_Engineering", "pages", "page_02.txt")
    assert page_agent.workday_form_loading(snapshot)


def test_occ_disclosures_have_profile_answers_and_consent_action(resume_file):
    snapshot = _recorded_page("The_Options_Clearing_Corporation_Associate_Principal,_Cloud_Engineering", "pages", "page_08.txt")
    agent = make_agent(Planner(), resume_file)
    controls = page_agent.parse_snapshot(snapshot)
    gender = next(c for c in controls if "gender" in c.question.lower())
    veteran = next(c for c in controls if "veteran status" in c.question.lower())
    consent = next(c for c in controls if c.role == "checkbox" and "consent" in c.question.lower())
    assert page_agent.is_workday_choice_button(gender)
    assert agent.known_answer(gender)[0] == "Male"
    assert page_agent.is_workday_choice_button(veteran)
    assert agent.known_answer(veteran)[0] == "I am not a veteran"
    assert agent._attestation_answer(consent)[0] == "checked"


def test_empty_middle_name_overrides_historical_answer():
    import dataclasses

    agent = make_agent(Planner(), Path("resume.pdf"),
                       profile=dataclasses.replace(config.get_user_profile(), middle_name=""))
    control = page_agent.Control(ref="m", role="textbox", name="Middle Name")
    assert agent.known_answer(control) == ("", "profile.middle_name")
    required = page_agent.Control(ref="r", role="textbox", name="Middle Name *")
    assert agent.known_answer(required) == ("N/A", "profile.middle_name")


def test_paylocity_labels_survive_required_markers():
    snapshot = _recorded_page("WinChoice_Senior_DevOps_Engineer", "pages", "page_09.txt")
    controls = page_agent.parse_snapshot(snapshot)
    questions = {control.question for control in controls}
    assert "First Name" in questions and "Last Name" in questions
    assert any("SMS" in question and "permission" in question for question in questions)


def test_a_radio_button_carries_its_question():
    snapshot = """- group "Are you at least 18 years old? *" [ref=e5]:
  - radio "Yes" [ref=e6]
  - radio "No" [checked] [ref=e7]"""
    controls = page_agent.parse_snapshot(snapshot)
    radios = [c for c in controls if c.role == "radio"]
    assert [c.group for c in radios] == ["Are you at least 18 years old? *"] * 2
    assert page_agent.answered_fields(controls) == [{"label": "Are you at least 18 years old? *", "value": "No"}]


# --- a whole application ------------------------------------------------------------

def test_a_whole_application_is_read_answered_and_submitted(page, resume_file):
    serve(page)
    planner = Planner()
    outcome = make_agent(planner, resume_file).run(page)
    assert outcome.kind == "submitted", outcome.reasons
    # The agent fills his name from the profile before anyone is asked, so it
    # is the name as he writes it, not the one the page was offered.
    assert stored(page, "first") == "Yaswanth Reddy"
    assert stored(page, "country") == "United States"
    assert stored(page, "sponsor") == "Yes"
    assert stored(page, "state") == "Virginia"          # the dropdown drawn by script
    assert stored(page, "adult") == "Yes"
    assert stored(page, "resume") == resume_file.name


def test_a_carried_over_no_to_sponsorship_is_corrected_from_the_profile(page, resume_file):
    """Schwab's page came with "No" already chosen, from an old application. The
    owner's rule: the agent puts it right from the profile and carries on."""
    serve(page, sponsor_options='<option value="">-- Make a Selection --</option><option>Yes</option>'
                                '<option selected="selected">No</option>')
    agent = make_agent(Planner(), resume_file)
    outcome = agent.run(page)
    assert outcome.kind == "submitted", outcome.reasons
    assert stored(page, "sponsor") == "Yes"
    assert any("corrected" in n and "sponsorship" in n for n in agent.notes)


def test_an_answer_the_owner_set_is_never_corrected(page, resume_file):
    serve(page, sponsor_options='<option value="">-- Make a Selection --</option><option>Yes</option>'
                                '<option>No</option>')
    agent = make_agent(Planner(), resume_file)
    agent.remember_page_state(page)
    page.locator("#sponsor").select_option("No")          # the owner, while the agent waited
    agent.note_owner_changes(page)
    outcome = agent.run(page)
    assert outcome.kind == "owner_needed"
    assert page.locator("#sponsor").input_value() == "No"
    assert page.url.endswith("/apply/1")


def test_a_legal_answer_is_corrected_only_from_a_profile_field(page, resume_file):
    page.set_content('<label for="f">Have you ever been convicted of a felony? *</label>'
                     '<select id="f"><option>Yes</option><option selected>No</option></select>')
    agent = make_agent(Planner(), resume_file)
    controls = page_agent.parse_snapshot(agent.snapshot(page))
    plan = page_agent.PagePlan(mismatches=[{"question": "Have you ever been convicted of a felony? *",
                                            "on_page": "No", "correct_value": "Yes", "source": "resume"}])
    assert agent.correct_from_profile(page, plan, controls) == []
    assert page.locator("#f").input_value() == "No"


def test_nothing_is_sent_when_automatic_submission_is_off(page, resume_file):
    serve(page)
    outcome = make_agent(Planner(), resume_file, auto_submit=False).run(page)
    assert outcome.kind == "owner_needed"
    assert "automatic submission is off" in outcome.summary
    assert page.url.endswith("/apply/2")


CERT = ('<label><input type="checkbox" id="cert"> I certify that the information above is true and complete. '
        'Checking this box is equivalent to a handwritten signature.</label>')


def cert_planner(**kwargs):
    planner = Planner(**kwargs)
    original = planner.plan_page

    def plan_page(snapshot, facts, feedback=""):
        plan = original(snapshot, facts, feedback)
        box = re.search(r'- checkbox "I certify[^"]*" \[ref=([\w-]+)\]', snapshot)
        if box:
            plan["answers"].append({"ref": box.group(1), "question": "I certify ... signature", "action": "check",
                                    "value": "", "source": "consent"})
        return plan
    return SimpleNamespace(plan_page=plan_page)


def test_the_owner_allowed_signing_so_the_agent_signs_last_and_submits(page, resume_file):
    """The owner's decision (2026-09-17): the agent signs on their behalf."""
    serve(page, certify=CERT)
    agent = make_agent(cert_planner(), resume_file)
    outcome = agent.run(page)
    assert outcome.kind == "submitted", outcome.reasons
    assert any(n.startswith("signed") for n in agent.notes)


def test_without_the_owners_permission_nothing_is_signed(page, resume_file):
    import dataclasses
    serve(page, certify=CERT)
    profile = dataclasses.replace(config.get_user_profile(), sign_attestations=False)
    outcome = make_agent(cert_planner(), resume_file, profile=profile).run(page)
    assert outcome.kind == "owner_needed"
    assert not page.locator("#cert").is_checked()
    assert page.url.endswith("/apply/2")


def test_nothing_is_signed_on_a_page_with_an_answer_that_did_not_stay(page, resume_file):
    broken = STEP_2.replace("onclick=\"shown.textContent='Virginia'; list.hidden = true\"", "")
    serve(page, step2=broken, certify=CERT)
    outcome = make_agent(cert_planner(), resume_file).run(page)
    assert outcome.kind == "owner_needed"
    assert not page.locator("#cert").is_checked()


# --- what is refused, whatever Claude proposes -------------------------------------------

def refusal_for(page, resume_file, html, action, value, source="profile.city", role="textbox", name="Box"):
    page.set_content(html)
    agent = make_agent(Planner(), resume_file)
    controls = page_agent.parse_snapshot(agent.snapshot(page))
    control = next(c for c in controls if c.role == role and c.question.startswith(name))
    return agent.refusal(page, page_agent.Answer(control.ref, control.question, action, value, source), control, controls)


def test_a_declaration_is_ticked_only_with_the_owners_permission(page, resume_file):
    import dataclasses
    html = '<label><input type="checkbox"> I certify my answers are true and complete</label>'
    page.set_content(html)
    for allowed, expected in ((False, "declaration"), (True, "")):
        profile = dataclasses.replace(config.get_user_profile(), sign_attestations=allowed)
        agent = make_agent(Planner(), resume_file, profile=profile)
        controls = page_agent.parse_snapshot(agent.snapshot(page))
        box = next(c for c in controls if c.role == "checkbox")
        why = agent.refusal(page, page_agent.Answer(box.ref, box.question, "check", "", "consent"), box, controls)
        assert (expected in why) if expected else why == ""


def test_a_typed_signature_is_only_ever_the_owners_own_name(page, resume_file):
    page.set_content('<label>Electronic signature <input></label>')
    agent = make_agent(Planner(), resume_file)
    controls = page_agent.parse_snapshot(agent.snapshot(page))
    box = next(c for c in controls if c.role == "textbox")
    full_name = config.get_user_profile().full_name
    ok = agent.refusal(page, page_agent.Answer(box.ref, box.question, "fill", full_name, "profile.full_name"),
                       box, controls)
    wrong = agent.refusal(page, page_agent.Answer(box.ref, box.question, "fill", "Someone Else", "profile.full_name"),
                          box, controls)
    assert ok == "" and "declaration" in wrong


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


def test_already_applied_page_is_detected_and_reported(page, resume_file):
    page.set_content('<h3>Principal Engineer</h3><p>You\'ve already applied for this job.</p><a href="#">View My Applications</a>')
    planner = SimpleNamespace(plan_page=lambda s, f, fb="": {"page_kind": "application_form", "next": {"kind": "none"}})
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


def test_a_submit_button_on_a_step_before_the_last_just_moves_on(page, resume_file):
    """Schwab's step 2 of 5 says "Submit" and only saves that step: it was
    held as if it sent the application, for want of a resume attached earlier."""
    serve(page, step1=STEP_1.replace('<button type="submit">Next</button>', '<button type="submit">Submit</button>'))
    outcome = make_agent(Planner(next_label="Submit"), resume_file).run(page)
    assert outcome.kind == "submitted", outcome.reasons
    assert stored(page, "sponsor") == "Yes"


# --- radio buttons whose label is the text after them (Schwab's veteran form) -----

VETERAN = """<html><body><h1>Voluntary Self-Identification</h1><p>Step 4 of 5</p>
<form onsubmit="localStorage.vet = (document.querySelector('[name=vet]:checked') || {}).value || '';
                location.href = '/done'; return false;">
  <div>2. If you believe you belong to any of the categories of protected veterans, please check the box.</div>
  <input type="radio" name="vet" value="identify"> I IDENTIFY AS A PROTECTED VETERAN
  <input type="radio" name="vet" value="not"> I AM NOT A PROTECTED VETERAN
  <input type="radio" name="vet" value="decline"> I DON'T WISH TO ANSWER
  <button type="submit">Next</button>
</form></body></html>"""


def veteran_planner(action):
    def plan_page(snapshot, facts, feedback=""):
        if "Thank you for applying" in snapshot:
            return {"page_kind": "confirmation", "next": {"kind": "none"}}
        radio = re.search(r'- radio \[ref=([\w-]+)\]\n\s*- text: I AM NOT', snapshot)
        first = re.search(r'- radio \[ref=([\w-]+)\]', snapshot)
        ref = (radio or first).group(1) if action == "check" else first.group(1)
        return {"page_kind": "application_form", "step": "4 of 5",
                "answers": [{"ref": ref, "question": "protected veteran", "action": action,
                             "value": "I AM NOT A PROTECTED VETERAN", "source": "profile.veteran_status"}],
                "next": {"ref": ref_of(snapshot, "button", "Next"), "label": "Next", "kind": "next_step"}}
    return SimpleNamespace(plan_page=plan_page)


@pytest.mark.parametrize("action", ["check", "choose"])
def test_a_radio_button_labelled_by_the_text_after_it_is_answered(page, resume_file, action):
    page.route("https://jobs.example.com/**", lambda route: route.fulfill(
        status=200, content_type="text/html", body=DONE if route.request.url.endswith("/done") else VETERAN))
    page.goto("https://jobs.example.com/apply/4")
    controls = page_agent.parse_snapshot(page.locator("body").aria_snapshot(mode="ai"))
    radios = [c for c in controls if c.role == "radio"]
    assert [c.name for c in radios][1] == "I AM NOT A PROTECTED VETERAN"
    assert radios[0].group.startswith("2. If you believe")
    make_agent(veteran_planner(action), resume_file).run(page)
    assert stored(page, "vet") == "not"


def test_an_answer_that_cannot_be_given_is_reported_not_dropped(page, resume_file):
    serve(page)
    planner = Planner(extra={1: [{"ref": "", "question": "x", "action": "fill", "value": "y", "source": "resume"}]})
    # A choice the page doesn't offer: tried, retried, then reported.
    original = planner.plan_page

    def plan_page(snapshot, facts, feedback=""):
        plan = original(snapshot, facts, feedback)
        for answer in plan.get("answers", []):
            if answer["question"] == "Country":
                answer["value"] = "Atlantis"
        return plan
    outcome = make_agent(SimpleNamespace(plan_page=plan_page), resume_file).run(page)
    assert outcome.kind == "owner_needed"
    assert any("could not set" in r and "Atlantis" in r for r in outcome.reasons)
    assert page.url.endswith("/apply/1")


class Tracker:
    def __init__(self, events=()):
        self._events = list(events)

    def record_event(self, key, kind, message="", **kw):
        self._events.append({"kind": kind, "message": message})

    def events(self, key, limit=100):
        return list(self._events)


def test_a_resume_attached_in_an_earlier_run_counts_at_the_last_step(page, resume_file):
    """Schwab's resume went on at step 1; the run that reached the last step
    started later and was held for "the tailored resume is not attached"."""
    serve(page)
    page.goto("https://jobs.example.com/apply/2")
    planner = Planner()
    original = planner.plan_page

    def no_upload(snapshot, facts, feedback=""):
        plan = original(snapshot, facts, feedback)
        plan["answers"] = [a for a in plan.get("answers", []) if a["action"] != "upload_resume"]
        return plan
    agent = make_agent(SimpleNamespace(plan_page=no_upload), resume_file)
    agent.key = "k"
    agent.tracker = Tracker()
    assert "resume is not attached" in agent.run(page).summary

    page.goto("https://jobs.example.com/apply/2")
    agent = make_agent(SimpleNamespace(plan_page=no_upload), resume_file)
    agent.key = "k"
    agent.tracker = Tracker([{"kind": "resume_attached", "message": resume_file.name}])
    assert agent.run(page).kind == "submitted"


def test_an_already_attached_resume_is_not_uploaded_again(page, resume_file):
    """When a resume is already attached on the page (e.g. from an earlier pass
    or when continuing after an error), attach_documents does not upload it again."""
    agent = make_agent(Planner(), resume_file)
    page.set_content(f'<div><p>Resume: {resume_file.name}</p><button>Select files</button></div>')
    controls = page_agent.parse_snapshot(agent.snapshot(page))
    uploads = agent.attach_documents(page, agent.snapshot(page), controls)
    assert uploads == []
    assert agent.resume_uploaded is True


def test_an_upload_is_recorded_for_later_runs(page, resume_file):
    serve(page)
    agent = make_agent(Planner(), resume_file)
    agent.key, agent.tracker = "k", Tracker()
    assert agent.run(page).kind == "submitted"
    assert {"kind": "resume_attached", "message": resume_file.name} in agent.tracker._events


def test_a_dropdown_that_shows_its_choice_beside_it_is_read_as_answered():
    """ICE's form (a widget kit) puts the chosen value in a box next to the
    control: four answers that were really there were read as missing, and a
    finished application stopped instead of being sent."""
    chosen = page_agent.parse_snapshot('''- generic [ref=e205]:
  - generic [ref=e206]:
    - generic [ref=e776]: Yes.
    - combobox "Would you be willing to relocate to London?" [ref=e209]
  - button "Toggle flyout" [ref=e211]''')
    box = next(c for c in chosen if c.role == "combobox")
    assert box.answer == "Yes." and box.question == "Would you be willing to relocate to London?"

    # The question's own label beside an empty dropdown is not an answer.
    empty = page_agent.parse_snapshot('''- generic [ref=e1]:
  - text: Do you require sponsorship? *
  - combobox "Do you require sponsorship? *" [ref=e3]''')
    assert next(c for c in empty if c.role == "combobox").answer == ""


# --- signing in: Google first, always (the owner's standing instruction) ----------

SIGN_IN = """<html><body><h2>Sign In</h2>
  <button>Sign in with Google</button>
  <button>Sign in with LinkedIn</button>
  <button>Sign in with email</button>
</body></html>"""


def agent_with_fake_login(resume_file, **kw):
    agent = make_agent(Planner(), resume_file, **kw)
    calls = []
    agent.assistant._sign_in_with_google = lambda page, button, email, host, header: calls.append(("google", email)) or True
    agent.assistant.handle_auth_gate = lambda page, email: calls.append(("password", email)) or False
    return agent, calls


def test_google_sign_in_is_chosen_over_email_and_linkedin(page, resume_file):
    page.set_content(SIGN_IN)
    agent, calls = agent_with_fake_login(resume_file)
    controls = page_agent.parse_snapshot(agent.snapshot(page))
    assert agent.sign_in_step(page, controls) is True
    assert [c[0] for c in calls] == ["google"]


def test_google_sign_in_inside_a_frame_is_used(page, resume_file):
    page.set_content(f'<iframe srcdoc=\'{SIGN_IN}\' width="600" height="400"></iframe>')
    page.wait_for_timeout(300)
    agent, calls = agent_with_fake_login(resume_file)
    controls = page_agent.parse_snapshot(agent.snapshot(page))
    assert agent.sign_in_step(page, controls) is True
    assert [c[0] for c in calls] == ["google"]


def test_a_password_sign_in_that_typed_nothing_is_not_counted_as_tried(page, resume_file):
    page.set_content('<h2>Sign In</h2><label>Email <input type="email"></label>'
                     '<label>Password <input type="password"></label><button>Sign In</button>')
    agent, calls = agent_with_fake_login(resume_file)
    controls = page_agent.parse_snapshot(agent.snapshot(page))
    assert agent.sign_in_step(page, controls) is False       # nothing done: the page is planned instead
    assert [c[0] for c in calls] == ["password"]


def test_application_form_with_email_and_header_signin_link_does_not_trigger_login(page, resume_file):
    """An application form with an Email field and a global navbar 'Sign In' link
    (Dayforce 'Apply without an Account') is not mistaken for a login gate."""
    page.set_content('''
        <nav><a href="/login">Sign In</a></nav>
        <h2>Application Information</h2>
        <form>
            <label>First Name <input type="text" id="fn"></label>
            <label>Last Name <input type="text" id="ln"></label>
            <label>Email Address <input type="email" id="em"></label>
            <label>Phone Number <input type="tel" id="ph"></label>
            <button type="submit">Save and Continue</button>
        </form>
    ''')
    agent, calls = agent_with_fake_login(resume_file)
    controls = page_agent.parse_snapshot(agent.snapshot(page))
    assert agent.sign_in_step(page, controls) is False
    assert page.locator("#em").input_value() == ""
    assert calls == []


def test_dedicated_email_first_login_screen_fills_email_and_advances(page, resume_file):
    """A dedicated sign-in screen asking for email first gives the email and advances."""
    page.set_content('''
        <h2>Sign In</h2>
        <form onsubmit="event.preventDefault(); window.submittedEmail = em.value;">
            <label>Email or Username <input type="email" id="em"></label>
            <button type="submit">Next</button>
        </form>
    ''')
    agent, calls = agent_with_fake_login(resume_file)
    expected_email = agent.config.ats_email or agent.profile.email
    controls = page_agent.parse_snapshot(agent.snapshot(page))
    assert agent.sign_in_step(page, controls) is True
    assert page.evaluate("window.submittedEmail") == expected_email


def test_recorded_account_uses_sign_in_instead_of_stale_registration(page, resume_file):
    page.set_content('<button>Create Account</button><input type="password"><input type="password">')
    agent, calls = agent_with_fake_login(resume_file)
    agent.assistant.account_on_record = lambda: True
    agent.assistant._create_account_control = lambda tab: tab.get_by_role("button", name="Create Account")
    agent.assistant._goto_login_page = lambda tab: calls.append(("goto_login", tab.url)) or True
    controls = page_agent.parse_snapshot(agent.snapshot(page))

    assert agent.sign_in_step(page, controls) is True
    assert [call[0] for call in calls] == ["goto_login"]


def test_visible_registration_errors_override_a_transient_account_record(page, resume_file):
    page.set_content('<button>Create Account</button><input type="password"><input type="password">'
                     '<p>Error: Passwords do not match</p>')
    agent, calls = agent_with_fake_login(resume_file)
    agent.assistant.account_on_record = lambda: True
    agent.assistant._create_account_control = lambda tab: tab.get_by_role("button", name="Create Account")
    agent.assistant._goto_login_page = lambda tab: calls.append(("goto_login", tab.url)) or True
    controls = page_agent.parse_snapshot(agent.snapshot(page))

    assert agent.sign_in_step(page, controls) is False
    assert [call[0] for call in calls] == ["password"]


def test_workday_recorded_account_signs_in_instead_of_registering(page, resume_file):
    """An account already on record: sign in, even though a registration form
    is showing beside the sign-in one (the owner's rule)."""
    page.set_content('''<h2>Create Account</h2><form>
        <label>Email <input type="email"></label><label>Password <input type="password"></label>
        <label>Verify New Password <input type="password"></label><button>Create Account</button>
    </form><form data-automation-id="signInForm">
        <label>Password <input type="password"></label><button>Sign In</button>
    </form>''')
    agent, calls = agent_with_fake_login(resume_file)
    agent.assistant.adapter = lambda _tab: WorkdayAdapter()
    agent.assistant.account_on_record = lambda: True
    agent.assistant._create_account_control = lambda tab: tab.get_by_role("button", name="Create Account")
    agent.assistant._goto_login_page = lambda tab: calls.append(("goto_login", tab.url)) or True
    controls = page_agent.parse_snapshot(agent.snapshot(page))

    assert agent.sign_in_step(page, controls) is True
    assert [call[0] for call in calls] == ["goto_login"]


def test_workday_registration_is_not_overridden_by_a_visible_sign_in_form(page):
    page.set_content('''<h2>Create Account</h2><form>
        <label>Password <input type="password"></label>
        <label>Verify New Password <input type="password"></label><button>Create Account</button>
    </form><form data-automation-id="signInForm">
        <label>Password <input type="password"></label><button>Sign In</button>
    </form>''')
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    calls = []
    assistant.adapter = lambda _page: WorkdayAdapter()
    assistant.fill_create_account_form = lambda *_args: calls.append("registration") or False
    assistant.attempt_auto_login = lambda *_args, **_kwargs: calls.append("sign_in") or True

    assert assistant.handle_auth_gate(page, "candidate@example.com") is False
    assert calls == ["registration"]


def test_workday_verification_state_does_not_trigger_password_handling(page):
    page.set_content('''<h2>Verify your identity</h2>
        <label>Verification Code <input type="text"></label>
        <form data-automation-id="signInForm"><input type="password"><button>Sign In</button></form>''')
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    calls = []
    assistant.adapter = lambda _page: WorkdayAdapter()
    assistant.fill_create_account_form = lambda *_args: calls.append("registration") or True
    assistant.attempt_auto_login = lambda *_args, **_kwargs: calls.append("sign_in") or True

    assert assistant.handle_auth_gate(page, "candidate@example.com") is False
    assert calls == []


def test_workday_account_record_requires_candidate_home_or_application(page):
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    assistant.adapter = lambda _page: WorkdayAdapter()

    page.set_content('''<h2>Create Account</h2><form>
        <input type="password"><input type="password"><button>Create Account</button>
    </form>''')
    assert not assistant._account_creation_is_confirmed(page)

    page.set_content('<div data-automation-id="progressBarActiveStep">My Experience</div>')
    assert assistant._account_creation_is_confirmed(page)


def test_workday_registration_error_is_found_without_alert_markup(page):
    page.set_content('<button>Create Account</button><p>Error: Passwords do not match</p>')
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)

    assert assistant._account_form_has_validation_error(page)


def test_linkedin_sign_in_is_never_used(page, resume_file):
    page.set_content(SIGN_IN)
    agent = make_agent(Planner(), resume_file)
    controls = page_agent.parse_snapshot(agent.snapshot(page))
    plan = page_agent.PagePlan(page_kind="sign_in", next_kind="sign_in",
                               next_ref=next(c.ref for c in controls if "LinkedIn" in c.name),
                               next_label="Sign in with LinkedIn")
    moved, _page, why = agent.press_next(page, plan, controls)
    assert moved == "stop" and ("never uses" in why or "never presses" in why)


# --- Harbinger's form (Greenhouse): choices, wording, the cover letter -------------

def test_choices_are_read_even_when_their_text_sits_in_a_child():
    """School, Discipline and Veteran Status were all read as empty and left
    blank: these options carry their words in a child element."""
    offered = page_agent.choices_in('''- listbox [ref=e9]:
  - option [ref=e10]:
    - generic [ref=e11]: Jawaharlal Nehru Technological University
  - option [ref=e12]: Jawaharlal Nehru University''')
    assert offered == [("e10", "Jawaharlal Nehru Technological University"),
                       ("e12", "Jawaharlal Nehru University")]


def test_the_closest_wording_is_taken_but_never_the_opposite():
    veteran = ["I am not a protected veteran",
               "I identify as one or more of the classifications of a protected veteran",
               "I don't wish to answer"]
    assert veteran[page_agent.closest_choice(veteran, "I am not a veteran")] == "I am not a protected veteran"
    # A choice that reverses the meaning is never taken.
    assert page_agent.closest_choice(veteran[1:], "I am not a veteran") is None
    assert page_agent.closest_choice(["No, I do not have a disability"], "Yes, I have a disability") is None


COVER_LETTER_FORM = """<html><body><h1>Apply</h1><p>Step 1 of 1</p>
<form onsubmit="localStorage.cover = cover.files.length ? cover.files[0].name : '';
                localStorage.resume = resume.files.length ? resume.files[0].name : '';
                location.href='/done'; return false;">
  <fieldset><legend>Resume/CV*</legend><input type="file" id="resume" aria-label="Attach"></fieldset>
  <fieldset><legend>Cover Letter*</legend><input type="file" id="cover" aria-label="Attach"></fieldset>
  <button type="submit">Submit Application</button>
</form></body></html>"""


def test_the_cover_letter_is_attached_where_the_form_asks_for_one(page, resume_file, tmp_path):
    """Harbinger's "Cover Letter" section has one control, called "Attach",
    and the cover letter was forgotten again. Since the owner's lazy rule
    (attach only where the form requires a letter), the section here is
    required; test_optional_cover_letter_is_skipped_when_not_required covers
    the optional case."""
    letter = tmp_path / "Yaswanth_Jonnalagadda_Cover_Letter.pdf"
    letter.write_bytes(b"%PDF-1.4 letter")
    page.route("https://jobs.example.com/**", lambda route: route.fulfill(
        status=200, content_type="text/html",
        body=DONE if route.request.url.endswith("/done") else COVER_LETTER_FORM))
    page.goto("https://jobs.example.com/apply/1")

    planner = SimpleNamespace(plan_page=lambda snapshot, facts, feedback="": (
        {"page_kind": "confirmation", "next": {"kind": "none"}} if "Thank you" in snapshot else
        {"page_kind": "application_form", "answers": [],
         "next": {"ref": ref_of(snapshot, "button", "Submit Application"), "label": "Submit Application",
                  "kind": "final_submit"}}))
    agent = make_agent(planner, resume_file)
    agent.cover_letter = lambda: (letter.with_suffix(".txt"), letter)
    outcome = agent.run(page)
    assert outcome.kind == "submitted", outcome.reasons
    assert stored(page, "resume") == resume_file.name
    assert stored(page, "cover") == letter.name          # attached without being told to


def test_a_document_already_attached_is_not_attached_again(page, resume_file):
    filled = COVER_LETTER_FORM.replace('<legend>Cover Letter*</legend>',
                                       '<legend>Cover Letter*</legend><p>Cover_Letter.pdf</p>')
    page.route("https://jobs.example.com/**", lambda route: route.fulfill(
        status=200, content_type="text/html",
        body=DONE if route.request.url.endswith("/done") else filled))
    page.goto("https://jobs.example.com/apply/1")
    asked = []
    agent = make_agent(SimpleNamespace(plan_page=lambda s, f, fb="": (
        {"page_kind": "confirmation", "next": {"kind": "none"}} if "Thank you" in s else
        {"page_kind": "application_form", "answers": [],
         "next": {"ref": ref_of(s, "button", "Submit Application"), "label": "Submit Application",
                  "kind": "final_submit"}})), resume_file)
    agent.cover_letter = lambda: asked.append("written") or None
    agent.run(page)
    assert asked == []                                   # no cover letter written for a section that has one


def test_the_owners_name_is_given_as_they_write_it():
    import config
    profile = config.get_user_profile()
    assert (profile.first_name, profile.middle_name, profile.last_name) == ("Yaswanth Reddy", "", "Jonnalagadda")


NAME_FORM = """<html><body><h1>Apply</h1>
<form onsubmit="return false;">
  <label for="f">First Name *</label><input id="f" value="Yaswanth">
  <label for="m">Middle Name</label><input id="m">
  <label for="l">Last Name *</label><input id="l" value="Reddy Jonnalagadda">
  <label for="c">Company Name</label><input id="c" value="Capital One">
  <label for="p">Preferred First Name</label><input id="p">
</form></body></html>"""


def test_the_name_is_put_right_from_the_profile(page, resume_file):
    """A form filled "Yaswanth" / "Reddy Jonnalagadda" is corrected to the
    owner's own first and last name."""
    page.set_content(NAME_FORM)
    agent = make_agent(Planner(), resume_file)
    controls = page_agent.parse_snapshot(agent.snapshot(page))
    agent.correct_from_profile(page, page_agent.PagePlan(), controls)
    assert page.locator("#f").input_value() == "Yaswanth Reddy"
    assert page.locator("#l").input_value() == "Jonnalagadda"
    assert page.locator("#m").input_value() == ""            # no middle name: left empty
    assert page.locator("#c").input_value() == "Capital One"  # not a name of the owner's
    assert page.locator("#p").input_value() == ""


def test_a_name_the_owner_typed_is_left_alone(page, resume_file):
    page.set_content(NAME_FORM.replace('id="f" value="Yaswanth"', 'id="f" value="Yash"'))
    agent = make_agent(Planner(), resume_file)
    agent.remember_page_state(page)
    page.locator("#f").fill("Yash R.")                        # the owner, while the agent waited
    agent.note_owner_changes(page)
    controls = page_agent.parse_snapshot(agent.snapshot(page))
    agent.correct_from_profile(page, page_agent.PagePlan(), controls)
    assert page.locator("#f").input_value() == "Yash R."


# --- a one-time code emailed to the owner ------------------------------------------

CODE_PAGE = ('<h2>Verify your email</h2><label for="c">Enter verification code sent to email</label>'
             '<input id="c"><button>Verify</button>')
HUMAN_CHECK_PAGE = ('<p>A verification code was sent to you@example.com. To submit your application, enter the '
                    '8-character code to confirm you\'re a human.</p><label for="c">Security code</label>'
                    '<input id="c"><button>Submit application</button>')


DIGIT_BOXES = ("<h2>Verify your email</h2>"
               "<p>The verification code was sent to this email address: you@example.com.</p>" +
               "".join(f'<input type="number" aria-label="Enter verification code digit {i} of six.">'
                       for i in range(1, 7)) +
               "<button onclick=\"document.body.dataset.verified = "
               "[...document.querySelectorAll('input')].map(i => i.value).join('')\">Verify</button>")


def _code_agent(page, resume_file, code="482913"):
    agent = make_agent(Planner(), resume_file)
    agent.assistant.passcode_from_gmail = lambda pg, previous="", wait_seconds=150, length=0: code
    return agent


def test_a_code_for_the_owners_own_account_is_entered(page, resume_file):
    """R+L Carriers asks for a code emailed to the applicant -- the step the
    owner approved on 2026-09-15 for account setup and sign-in."""
    page.set_content(CODE_PAGE)
    agent = _code_agent(page, resume_file)
    controls = page_agent.parse_snapshot(agent.snapshot(page))
    assert agent.complete_account_code(page, controls, agent.snapshot(page)) is True
    assert page.locator("#c").input_value() == "482913"


def test_a_code_split_across_one_box_per_digit_is_typed(page, resume_file):
    """R+L gives the code six boxes, one per digit, that advance themselves."""
    page.set_content(DIGIT_BOXES)
    agent = _code_agent(page, resume_file)
    controls = page_agent.parse_snapshot(agent.snapshot(page))
    assert agent.complete_account_code(page, controls, agent.snapshot(page)) is True
    assert page.evaluate("[...document.querySelectorAll('input')].map(i => i.value).join('')") == "482913"
    assert page.evaluate("document.body.dataset.verified") == "482913"   # Verify was pressed


def test_a_code_asked_for_to_prove_a_human_is_never_entered(page, resume_file):
    """Harbinger's page says the code is there to confirm a human is applying."""
    page.set_content(HUMAN_CHECK_PAGE)
    agent = _code_agent(page, resume_file)
    controls = page_agent.parse_snapshot(agent.snapshot(page))
    assert agent.complete_account_code(page, controls, agent.snapshot(page)) is False
    assert page.locator("#c").input_value() == ""


def test_no_code_is_read_without_permission_to_read_the_mail(page, resume_file):
    import dataclasses
    page.set_content(CODE_PAGE)
    profile = dataclasses.replace(config.get_user_profile(), check_gmail_for_confirmation=False)
    agent = make_agent(Planner(), resume_file, profile=profile)
    agent.assistant.passcode_from_gmail = lambda *a, **k: "482913"
    controls = page_agent.parse_snapshot(agent.snapshot(page))
    assert agent.complete_account_code(page, controls, agent.snapshot(page)) is False
    assert page.locator("#c").input_value() == ""


def test_choices_with_no_reference_belong_to_their_group():
    """R+L's yes/no radios carry no reference of their own -- only their group
    does -- so nothing could be clicked and the question came back to the owner."""
    snapshot = ('- radiogroup "Have you ever been convicted of a crime?" [ref=e974]:\n'
                '  - radio "No"\n  - generic: "No"\n  - radio "Yes"')
    group = next(c for c in page_agent.parse_snapshot(snapshot) if c.holds_choices)
    assert group.ref == "e974" and group.options == ["No", "Yes"]
    assert group.question == "Have you ever been convicted of a crime?"


def test_a_choice_inside_a_group_is_clicked_by_what_it_says(page, resume_file):
    page.set_content("""
      <div role="radiogroup" aria-label="Have you ever been convicted of a crime?">
        <label><input type="radio" name="crime" value="No" aria-label="No"> No</label>
        <label><input type="radio" name="crime" value="Yes" aria-label="Yes"> Yes</label>
      </div>""")
    agent = make_agent(Planner(), resume_file)
    snapshot = agent.snapshot(page)
    group_ref = re.search(r'- radiogroup [^\n]*\[ref=([\w-]+)\]', snapshot).group(1)
    group = page_agent.Control(ref=group_ref, role="radiogroup",
                               name="Have you ever been convicted of a crime?", options=["No", "Yes"])
    answer = page_agent.Answer(group.ref, group.question, "check", "No", "profile.felony_conviction")
    assert agent.do(page, answer, group) is True
    assert page.locator("[value=No]").is_checked()


def test_a_box_a_plan_calls_a_choice_is_still_filled(page, resume_file):
    """ZIP Code is a plain box; the plan called it a choice and it was left blank."""
    page.set_content('<label for="z">ZIP Code</label><input id="z">')
    agent = make_agent(Planner(), resume_file)
    controls = page_agent.parse_snapshot(agent.snapshot(page))
    box = next(c for c in controls if c.role == "textbox")
    assert agent.do(page, page_agent.Answer(box.ref, "ZIP Code", "choose", "22031", "profile.postal_code"), box)
    assert page.locator("#z").input_value() == "22031"


def test_an_add_button_is_not_pressed_over_and_over(page, resume_file):
    """R+L's "Add Experience" was pressed again and again, leaving empty
    entries behind, because the dates could not be filled in."""
    page.set_content('<h1>Work history</h1><button onclick="document.body.dataset.n = '
                     '(+(document.body.dataset.n || 0) + 1)">Add Experience</button>')
    planner = SimpleNamespace(plan_page=lambda s, f, fb="": {
        "page_kind": "application_form", "answers": [],
        "next": {"ref": ref_of(s, "button", "Add Experience"), "label": "Add Experience", "kind": "next_step"}})
    outcome = make_agent(planner, resume_file).run(page)
    assert outcome.kind == "owner_needed"          # it stops rather than pressing forever
    assert int(page.evaluate("document.body.dataset.n")) <= 4   # enough for three jobs, then it stops


def test_a_tick_box_is_clicked_by_the_label_beside_it(page, resume_file):
    """R+L's "Current Job" box carries no reference at all: the only part of it
    the page lets anyone click is the label drawn next to it, and without that
    the entry could never be saved (it kept asking for an end date)."""
    page.set_content(
        '<div><span role="checkbox" aria-checked="false" aria-label="Current Job"></span>'
        '<div role="status" style="cursor:pointer" '
        'onclick="document.body.dataset.ticked = 1">Current Job</div></div>')
    agent = make_agent(Planner(), resume_file)
    controls = page_agent.parse_snapshot(agent.snapshot(page))
    box = next(c for c in controls if c.name == "Current Job")
    assert box.role == "checkbox"
    assert agent.do(page, page_agent.Answer(box.ref, "Current Job", "check", "Yes", "resume"), box)
    assert page.evaluate("document.body.dataset.ticked") == "1"


def test_a_button_that_opens_a_file_dialog_is_given_the_resume(page, resume_file):
    """Workday's "Autofill with Resume" opens the computer's own file dialog.
    Nothing on the page can close that, and the run froze behind it."""
    page.set_content('<h1>Start your application</h1>'
                     '<input id="f" type="file" style="display:none" '
                     'onchange="document.body.dataset.got = this.files[0].name">'
                     '<button onclick="document.getElementById(\'f\').click()">Autofill with Resume</button>')
    planner = SimpleNamespace(plan_page=lambda s, f, fb="": {
        "page_kind": "application_form", "answers": [],
        "next": {"ref": ref_of(s, "button", "Autofill with Resume"),
                 "label": "Autofill with Resume", "kind": "next_step"}})
    make_agent(planner, resume_file).run(page)
    assert page.evaluate("document.body.dataset.got") == resume_file.name


def test_the_agent_may_put_right_a_box_it_filled_in_badly(page, resume_file):
    """Workday's year box was left reading "2012" by the agent's own failed
    attempt, and it then refused to correct it, taking that for the owner's
    own answer. What the owner really typed is protected separately."""
    page.set_content('<label for="y">Year</label><input id="y">')
    agent = make_agent(Planner(), resume_file)
    controls = page_agent.parse_snapshot(agent.snapshot(page))
    box = next(c for c in controls if c.role == "textbox")

    agent.do(page, page_agent.Answer(box.ref, "Year", "fill", "2025", "resume"), box)
    agent.written["Year"] = "2025"
    page.fill("#y", "2012")                       # what the widget was left showing

    assert agent._ours("Year", "2012")            # the agent's own mess, not the owner's
    agent.owner_answers["Year"] = "1999"
    assert agent._owner_gave("Year")              # what the owner set is still his


def test_a_spinbutton_is_typed_into_not_set(page, resume_file):
    """Workday's year box is a spinbutton that keeps its own count: setting its
    text left it reading 2012 however often the right year was written."""
    page.set_content('<label for="y">Year</label>'
                     '<input id="y" role="spinbutton" oninput="document.body.dataset.typed = this.value">')
    agent = make_agent(Planner(), resume_file)
    box = next(c for c in page_agent.parse_snapshot(agent.snapshot(page)) if c.role == "spinbutton")
    assert agent.do(page, page_agent.Answer(box.ref, "Year", "fill", "2025", "resume"), box)
    assert page.evaluate("document.body.dataset.typed") == "2025"


def test_a_page_that_asks_to_be_refreshed_is_refreshed(page, resume_file):
    """Workday answered a press with "Something went wrong. Please refresh the
    page and then try again." -- a passing fault the agent can clear itself."""
    page.set_content('<h1>Something went wrong</h1><p>Please refresh the page and then try again.</p>')
    agent = make_agent(SimpleNamespace(plan_page=lambda s, f, fb="": {
        "page_kind": "other", "answers": [], "next": {"ref": "", "label": "", "kind": "none"}}), resume_file)
    assert agent._refreshed is False
    agent.run(page)
    assert agent._refreshed is True          # it did what the page asked


def test_import_resume_button_is_found_and_uploaded(page, resume_file):
    """Dayforce's '+ Import Resume' button under Resume Upload is recognized
    and the resume is attached even when the section has no required asterisk."""
    page.set_content('''
        <section>
            <h2>Resume Upload</h2>
            <p>Attachment:</p>
            <button id="import-btn">Import Resume</button>
        </section>
    ''')
    agent = make_agent(Planner(), resume_file)
    snapshot = agent.snapshot(page)
    controls = page_agent.parse_snapshot(snapshot)
    button_clicked = []
    agent.do = lambda pg, ans, c: button_clicked.append((ans.action, c.name)) or True

    given = agent.attach_documents(page, snapshot, controls)
    assert len(given) == 1
    assert given[0][0].action == "upload_resume"
    assert given[0][1].name == "Import Resume"
    assert button_clicked == [("upload_resume", "Import Resume")]


def test_known_answer_supplies_mandatory_school_position_and_employer(page, resume_file):
    """School *, Position Title *, Employer Name *, and State/Province * are all
    answered from the profile."""
    agent = make_agent(Planner(), resume_file)

    school_box = page_agent.Control(ref="e1", role="textbox", name="School *")
    pos_box = page_agent.Control(ref="e2", role="textbox", name="Position Title *")
    emp_box = page_agent.Control(ref="e3", role="textbox", name="Employer Name *")
    state_box = page_agent.Control(ref="e4", role="combobox", name="State/Province *")

    assert agent.known_answer(school_box)[0] == "Eastern Illinois University"
    assert agent.known_answer(pos_box)[0] == "Senior Network and Security Engineer"
    assert agent.known_answer(emp_box)[0] == "Capital One"
    assert agent.known_answer(state_box)[0] == "Virginia"


def test_optional_cover_letter_is_skipped_when_not_required(page, resume_file):
    """An optional 'Add Cover Letter' button with no asterisk is skipped
    when the owner has not provided a cover letter."""
    page.set_content('''
        <section>
            <h2>Cover Letter</h2>
            <button>Add Cover Letter</button>
        </section>
    ''')
    agent = make_agent(Planner(), resume_file)
    agent.cover_letter = None
    snapshot = agent.snapshot(page)
    controls = page_agent.parse_snapshot(snapshot)

    given = agent.attach_documents(page, snapshot, controls)
    assert given == []


def test_universal_concept_synonyms_across_diverse_ats_portals(page, resume_file):
    """Verifies that the concept synonym engine recognizes diverse field namings
    used across Workday, Dayforce, Greenhouse, Lever, Taleo, iCIMS, SuccessFactors."""
    agent = make_agent(Planner(), resume_file)

    # Job title variations
    for name in ("Current Role *", "Designation", "Current Position Title (required)", "Role Title", "Headline"):
        ctl = page_agent.Control(ref="t1", role="textbox", name=name)
        assert agent.known_answer(ctl)[0] == "Senior Network and Security Engineer", f"Failed for {name}"

    # Employer variations
    for name in ("Company Name *", "Current Company", "Organization Name", "Current Organization"):
        ctl = page_agent.Control(ref="e1", role="textbox", name=name)
        assert agent.known_answer(ctl)[0] == "Capital One", f"Failed for {name}"

    # School variations
    for name in ("Alma Mater *", "Educational Institution", "University Name (required)", "College Name"):
        ctl = page_agent.Control(ref="s1", role="textbox", name=name)
        assert agent.known_answer(ctl)[0] == "Eastern Illinois University", f"Failed for {name}"

    # Major variations
    for name in ("Course of Study *", "Discipline", "Degree Subject", "Field of Study (required)"):
        ctl = page_agent.Control(ref="m1", role="textbox", name=name)
        assert agent.known_answer(ctl)[0] == "Computer Technology", f"Failed for {name}"

    # Degree variations
    for name in ("Highest Level of Education *", "Degree Level", "Educational Attainment"):
        ctl = page_agent.Control(ref="d1", role="textbox", name=name)
        assert agent.known_answer(ctl)[0] in ("Master's", "Master's Degree"), f"Failed for {name}"

    # State dropdown with abbreviation normalization
    state_ctl = page_agent.Control(
        ref="st1", role="combobox", name="State / Province *",
        options=["Select One", "DC", "MD", "VA"]
    )
    assert agent.known_answer(state_ctl)[0] == "VA"

    # EEO / Work authorization variations
    work_auth = page_agent.Control(ref="w1", role="combobox", name="Are you legally authorized to work in the United States? *")
    assert agent.known_answer(work_auth)[0] == "Yes"

    sponsorship = page_agent.Control(ref="sp1", role="combobox", name="Will you now or in the future require visa sponsorship? *")
    assert agent.known_answer(sponsorship)[0] == "Yes"


def test_recruiting_communications_privacy_modal_is_accepted_and_saved(page, resume_file):
    """Inframark / Dayforce displays a 'Recruiting Communications' modal dialog with
    'I agree to the Privacy Statement' checkbox and a Save button that enables once checked.
    The agent accepts the checkbox and clicks Save to dismiss the modal."""
    page.set_content('''
        <h2>Application Information</h2>
        <div role="dialog" aria-modal="true" aria-label="Recruiting Communications">
            <h3>Recruiting Communications</h3>
            <p>Important Notice Regarding Recruitment Communications from Inframark</p>
            <label><input type="checkbox" id="agree-chk"> I agree to the Privacy Statement</label>
            <button id="cancel-btn">Cancel</button>
            <button id="save-btn" disabled onclick="this.closest('[role=dialog]').remove()">Save</button>
        </div>
        <script>
            document.getElementById('agree-chk').addEventListener('change', function() {
                document.getElementById('save-btn').disabled = !this.checked;
            });
        </script>
    ''')
    agent = make_agent(Planner(), resume_file)
    accepted = agent.assistant.accept_consent_dialog(page)
    assert accepted is True
    assert page.locator("[role=dialog]").count() == 0


def test_known_answer_accepts_privacy_consent_checkbox(page, resume_file):
    agent = make_agent(Planner(), resume_file)
    chk = page_agent.Control(ref="c1", role="checkbox", name="I agree to the Privacy Statement")
    assert agent.known_answer(chk) == ("checked", "profile.accept_application_privacy_prompts")


def test_dayforce_ant_design_dropdown_selection(page, resume_file):
    """Dayforce uses Ant Design selects where the input is obscured by a selector div,
    and options are rendered in a portal at the bottom of body."""
    page.set_content('''
        <div class="ant-form-item">
            <label>Race/Ethnicity</label>
            <div class="ant-select-selector" onclick="document.getElementById('portal-menu').style.display='block'">
                <input type="search" role="combobox" aria-label="Race/Ethnicity" style="opacity:0;width:100%;height:100%;" id="race-input" />
                <span class="ant-select-selection-item" id="race-val"></span>
            </div>
        </div>
        <div id="portal-menu" class="ant-select-dropdown" style="display:none;">
            <div class="ant-select-item ant-select-item-option" onclick="document.getElementById('race-val').textContent='Asian'; this.parentElement.style.display='none'">
                <div class="ant-select-item-option-content">Asian (United States of America)</div>
            </div>
            <div class="ant-select-item ant-select-item-option">
                <div class="ant-select-item-option-content">Two or More Races</div>
            </div>
        </div>
    ''')
    agent = make_agent(Planner(), resume_file)
    controls = page_agent.parse_snapshot(page.locator("body").aria_snapshot(mode="ai"))
    race_ctrl = next(c for c in controls if "Race/Ethnicity" in c.question or c.role == "combobox")
    ok = agent.choose(page, race_ctrl, "Asian", controls)
    assert ok is True
    assert page.locator("#race-val").inner_text() == "Asian"


def test_dayforce_state_province_combobox_select_and_parse(page, resume_file):
    """Dayforce Ant Design selects open on mousedown and options live in a portal dropdown.
    Selecting an option updates the UI and parse_snapshot detects the chosen value."""
    page.set_content('''
        <div class="ant-form-item">
            <label>State/Province *</label>
            <div class="ant-select-selector" onmousedown="document.getElementById('state-menu').style.display='block'">
                <input type="search" role="combobox" aria-label="State/Province *" style="opacity:0;" id="state-input" />
                <span class="ant-select-selection-item" id="state-val"></span>
            </div>
        </div>
        <div id="state-menu" class="ant-select-dropdown" style="display:none;">
            <div class="ant-select-item ant-select-item-option" onclick="document.getElementById('state-val').textContent='Virginia'; this.parentElement.style.display='none'">
                <div class="ant-select-item-option-content">Virginia</div>
            </div>
            <div class="ant-select-item ant-select-item-option">
                <div class="ant-select-item-option-content">Maryland</div>
            </div>
        </div>
    ''')
    agent = make_agent(Planner(), resume_file)
    controls = page_agent.parse_snapshot(page.locator("body").aria_snapshot(mode="ai"))
    state_ctrl = next(c for c in controls if "State/Province" in c.question or c.role == "combobox")
    ok = agent.choose(page, state_ctrl, "Virginia", controls)
    assert ok is True
    assert page.locator("#state-val").inner_text() == "Virginia"
    after_controls = page_agent.parse_snapshot(page.locator("body").aria_snapshot(mode="ai"))
    after_state = next(c for c in after_controls if "State/Province" in c.question or c.role == "combobox")
    assert after_state.answer == "Virginia"


def test_dayforce_veteran_form_answers_matched_and_selected(page, resume_file):
    """Dayforce groups veteran radios under VeteranFormAnswers and labels the negative choice
    'I am not a protected veteran'."""
    page.set_content('''
        <fieldset>
            <legend>VeteranFormAnswers</legend>
            <label><input type="radio" name="VeteranFormAnswers" value="vet"> I identify as one or more of the classifications of protected veteran listed above</label>
            <label><input type="radio" name="VeteranFormAnswers" value="not_vet" id="not-vet-radio"> I am not a protected veteran</label>
            <label><input type="radio" name="VeteranFormAnswers" value="decline"> I do not wish to self-identify</label>
        </fieldset>
    ''')
    agent = make_agent(Planner(), resume_file)
    controls = page_agent.parse_snapshot(page.locator("body").aria_snapshot(mode="ai"))
    vet_ctrl = controls[0]
    val, src = agent.known_answer(vet_ctrl)
    assert "not" in val.lower() and "veteran" in val.lower()
    answer = page_agent.Answer(vet_ctrl.ref, vet_ctrl.question, "choose", val, src)
    ok = agent.do(page, answer, vet_ctrl)
    assert ok is True
    assert page.locator("#not-vet-radio").is_checked()


def test_unanswered_profile_field_prevents_premature_page_advancement(page, resume_file):
    """If an unstarred EEO question like Race/Ethnicity has a known profile answer
    that failed to fill, profile_plan must not silently advance to the next page."""
    agent = make_agent(Planner(), resume_file)
    controls = [
        page_agent.Control(ref="r1", role="combobox", name="Race/Ethnicity"),
        page_agent.Control(ref="g1", role="combobox", name="Gender"),
        page_agent.Control(ref="btn1", role="button", name="Next"),
    ]
    # Simulate that Race/Ethnicity failed to fill and is still open
    plan = agent.profile_plan(controls, required=set(), still_open=["Race/Ethnicity"])
    # Agent must NOT advance (next_kind must not be next_step / open_application)
    assert plan.next_ref == ""
    assert any(item["question"] == "Race/Ethnicity" for item in plan.for_owner)


def test_phone_dial_code_prioritizes_candidate_country(resume_file):
    """When +1 matches multiple country options (e.g. Antigua, Bahamas, United States),
    _pick must choose the option corresponding to the profile's country."""
    agent = make_agent(Planner(), resume_file)
    offered = [
        ("ref1", "🇦🇬 +1 Antigua and Barbuda"),
        ("ref2", "🇧🇸 +1 Bahamas"),
        ("ref3", "🇺🇸 +1 United States"),
    ]
    pick = agent._pick(offered, "+1")
    assert pick == 2
    assert "United States" in offered[pick][1]


def test_date_field_converts_month_year_and_skips_disabled(page, resume_file):
    """Month Year strings like 'December 2022' convert to valid ISO date for <input type=date>,
    and disabled inputs are safely skipped without timing out."""
    page.set_content('''
        <input type="date" id="start-date" />
        <input type="date" id="end-date" disabled />
    ''')
    agent = make_agent(Planner(), resume_file)
    
    # 1. Active date input with Month Year
    start_ctrl = page_agent.Control(ref="s1", role="textbox", name="Start Date")
    # Point agent locate to start-date
    agent.locate = lambda _page, _ref: page.locator("#start-date")
    ans1 = page_agent.Answer("s1", "Start Date", "fill", "February 2025", "profile.start_date")
    assert agent.do(page, ans1, start_ctrl) is True
    assert page.locator("#start-date").input_value() == "2025-02-01"

    # 2. Disabled date input (e.g. End Date when current job is Yes)
    end_ctrl = page_agent.Control(ref="e1", role="textbox", name="End Date", disabled=True)
    agent.locate = lambda _page, _ref: page.locator("#end-date")
    ans2 = page_agent.Answer("e1", "End Date", "fill", "December 2022", "profile.end_date")
    assert agent.do(page, ans2, end_ctrl) is True







# --- the options confirm what a location question asks (RFC-001, M1) ----------

def _somewhere_profile(**place):
    """A made-up owner, so these tests hold for anyone's profile."""
    import dataclasses
    return dataclasses.replace(config.UserProfile(), full_name="Alex Example", first_name="Alex",
                               last_name="Example", **place)


def test_a_region_question_over_countries_is_answered_with_the_country(resume_file):
    """23 September: "Country/Region of Residence" was answered "Virginia"
    from a list of countries, and the field kept the site's default."""
    agent = make_agent(Planner(), resume_file,
                       profile=_somewhere_profile(country="United States", state="Virginia", city="Fairfax"))
    countries = ["- Select -", "Aaland Islands", "Afghanistan", "Albania", "Algeria", "Andorra", "United States"]
    for label in ("Country/Region of Residence *", "Region of Residence *"):
        control = page_agent.Control(ref="c", role="combobox", name=label, options=countries)
        assert agent.known_answer(control)[0] == "United States", label


def test_the_same_question_is_answered_for_an_owner_anywhere(resume_file):
    agent = make_agent(Planner(), resume_file,
                       profile=_somewhere_profile(country="India", state="Telangana", city="Hyderabad"))
    countries = ["- Select -", "Afghanistan", "Iceland", "India", "Indonesia", "United States"]
    control = page_agent.Control(ref="c", role="combobox", name="Region of Residence *", options=countries)
    assert agent.known_answer(control)[0] == "India"
