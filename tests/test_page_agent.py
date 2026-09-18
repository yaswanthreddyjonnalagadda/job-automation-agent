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
  <fieldset><legend>Cover Letter</legend><input type="file" id="cover" aria-label="Attach"></fieldset>
  <button type="submit">Submit Application</button>
</form></body></html>"""


def test_the_cover_letter_is_attached_where_the_form_asks_for_one(page, resume_file, tmp_path):
    """Harbinger's "Cover Letter" section has one control, called "Attach",
    and the cover letter was forgotten again."""
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
    filled = COVER_LETTER_FORM.replace('<legend>Cover Letter</legend>',
                                       '<legend>Cover Letter</legend><p>Cover_Letter.pdf</p>')
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


def _code_agent(page, resume_file, done=True):
    agent = make_agent(Planner(), resume_file)
    calls = []
    agent.assistant.complete_emailed_passcode = lambda pg: calls.append("read the mail") or done
    return agent, calls


def test_a_code_for_the_owners_own_account_is_entered(page, resume_file):
    """R+L Carriers asks for a code emailed to the applicant -- the step the
    owner approved on 2026-09-15 for account setup and sign-in."""
    page.set_content(CODE_PAGE)
    agent, calls = _code_agent(page, resume_file)
    controls = page_agent.parse_snapshot(agent.snapshot(page))
    assert agent.complete_account_code(page, controls, agent.snapshot(page)) is True
    assert calls == ["read the mail"]


def test_a_code_asked_for_to_prove_a_human_is_never_entered(page, resume_file):
    """Harbinger's page says the code is there to confirm a human is applying."""
    page.set_content(HUMAN_CHECK_PAGE)
    agent, calls = _code_agent(page, resume_file)
    controls = page_agent.parse_snapshot(agent.snapshot(page))
    assert agent.complete_account_code(page, controls, agent.snapshot(page)) is False
    assert calls == []


def test_no_code_is_read_without_permission_to_read_the_mail(page, resume_file):
    import dataclasses
    page.set_content(CODE_PAGE)
    profile = dataclasses.replace(config.get_user_profile(), check_gmail_for_confirmation=False)
    agent = make_agent(Planner(), resume_file, profile=profile)
    calls = []
    agent.assistant.complete_emailed_passcode = lambda pg: calls.append("read") or True
    controls = page_agent.parse_snapshot(agent.snapshot(page))
    assert agent.complete_account_code(page, controls, agent.snapshot(page)) is False
    assert calls == []
