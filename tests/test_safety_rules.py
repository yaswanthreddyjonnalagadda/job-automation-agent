"""
The rules the assistant must never break.

These are the specification's "must never" list, one test each, so a later
change to the form handling can't quietly reintroduce any of them. They use
local HTML pages, so no employer site is ever touched.

    venv\\Scripts\\python -m pytest tests -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import safety  # noqa: E402
from browser_automation import JobApplicationAssistant  # noqa: E402
from sites import adapter_for  # noqa: E402


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        yield b
        b.close()


@pytest.fixture
def page(browser):
    pg = browser.new_page()
    yield pg
    pg.close()


@pytest.fixture
def agent():
    """The assistant without its browser session (no network, no profile)."""
    a = JobApplicationAssistant.__new__(JobApplicationAssistant)
    a.values = safety.AgentValues()
    return a


# ---------------------------------------------------------------- never submit
def test_the_agent_has_no_way_to_click_submit():
    assert not hasattr(JobApplicationAssistant, "click_submit")
    assert hasattr(JobApplicationAssistant, "refuse_to_submit")


def test_submit_labels_are_recognised():
    for label in ("Submit", "Submit application", "Apply", "Save and Submit", "Send application"):
        assert safety.is_submit_label(label), label
    for label in ("Continue", "Save and Continue", "Next", "Read more", "Apply Manually"):
        assert not safety.is_submit_label(label), label


def test_wizard_navigation_never_presses_a_submit_button(agent, page):
    page.set_content("<button>Save and Submit</button><button>Submit application</button>")
    assert agent._wizard_button(page) is None
    assert agent.has_next_step(page) is False
    page.set_content("<button>Save and Continue</button>")
    assert agent._wizard_button(page) is not None


def test_upload_dialog_never_clicks_a_page_submit_button(agent, page, tmp_path):
    f = tmp_path / "resume.pdf"
    f.write_bytes(b"%PDF-1.4")
    page.set_content("<form><input type=file><button onclick='window.submitted=1'>Submit application</button></form>")
    assert agent.upload_in_dialog(page, f) is False       # no dialog: does nothing
    assert page.evaluate("() => !!window.submitted") is False


# ---------------------------------------------------------------- never sign
def test_attestation_wording_is_recognised():
    assert safety.is_attestation("By typing my name below, I certify that the information is true and complete")
    assert safety.is_attestation("Typed Signature")
    assert safety.is_attestation("I certify under penalty of perjury")
    assert not safety.is_attestation("I agree to the Data Privacy Statement")
    assert not safety.is_attestation("Are you legally authorized to work in the United States?")


def test_signature_fields_are_left_for_the_user(agent, page):
    page.set_content("<label for=sig>Typed Signature</label><input id=sig>")
    assert agent.set_value(page, "[id='sig']", "Yaswanth Jonnalagadda", "Typed Signature") is False
    assert page.locator("#sig").input_value() == ""


def test_attestation_checkboxes_are_left_for_the_user(agent, page):
    page.set_content("<input type=checkbox id=c><label for=c>I certify the information is true and complete</label>")
    assert agent._check_box_by_label(page, "I certify the information is true and complete") is False
    assert page.locator("#c").is_checked() is False


def test_pending_attestations_are_reported(agent, page):
    page.set_content("""<label for=sig>Typed Signature</label><input id=sig>
        <label for=name>First name</label><input id=name>""")
    pending = agent.pending_attestations(page)
    assert any("Typed Signature" in p for p in pending)
    assert not any("First name" in p for p in pending)


# ---------------------------------------------------------------- never overwrite the user
def test_a_users_answer_is_never_overwritten(agent, page):
    page.set_content("<label for=city>City</label><input id=city value='Richmond'>")
    assert agent.set_value(page, "[id='city']", "Fairfax", "City") is False
    assert page.locator("#city").input_value() == "Richmond"


def test_the_agent_may_correct_its_own_answer(agent, page):
    page.set_content("<label for=city>City</label><input id=city>")
    assert agent.set_value(page, "[id='city']", "Fairfax", "City") is True
    assert agent.set_value(page, "[id='city']", "Arlington", "City") is True
    assert page.locator("#city").input_value() == "Arlington"


def test_site_prefilled_values_count_as_the_users(agent, page):
    # A value the site parsed from the resume was never written by the agent.
    page.set_content("<label for=e>Email</label><input id=e value='someone@example.com'>")
    assert agent.set_value(page, "[id='e']", "me@example.com", "Email") is False


# ---------------------------------------------------------------- never guess
def test_people_managed_is_left_blank_when_the_profile_is_silent(agent):
    class Profile:
        people_managed = ""
    agent._profile = Profile()
    control = {"id": "x", "question": "Number of People Managed", "value": "", "listbox": ""}
    assert agent._experience_answer(None, control) is None


def test_field_of_study_is_never_swapped_for_another_subject(agent, page):
    class Profile:
        education = (("Bachelor's", "Electrical and Electronics Engineering", "JNTU Hyderabad", "2019"),)

    page.set_content("""<div class=entry>
        <label for=school>School Name</label><input id=school value='JNTU Hyderabad'>
        <label for=field>Field of Study</label><input id=field role=combobox>
      </div>""")
    control = {"id": "field", "question": "Field of Study (choose other if not available)",
               "value": "", "listbox": ""}
    candidates = agent._education_answer(page, control, Profile())
    assert candidates[0] == "Electrical and Electronics Engineering"
    assert all("computer" not in c.lower() for c in candidates), candidates
    assert candidates[-1] == "Other"  # the list's own "not available" choice


def test_placeholder_options_are_never_chosen():
    assert JobApplicationAssistant._best_option(["No Selection", "Yes", "No"], ["No"]) == 2
    assert JobApplicationAssistant._best_option(["- Select -", "Male", "Female"], ["Male"]) == 1
    assert JobApplicationAssistant._best_option(["No Selection"], ["No"]) is None


# ---------------------------------------------------------------- never type a Google password
def test_passwords_are_refused_on_identity_providers():
    assert safety.password_allowed("https://career2.successfactors.eu/careers") is True
    for url in ("https://accounts.google.com/signin", "https://www.linkedin.com/login",
                "https://login.microsoftonline.com/x", "https://appleid.apple.com/auth"):
        assert safety.password_allowed(url) is False, url


# ---------------------------------------------------------------- never bypass a CAPTCHA
def test_captcha_is_detected_not_solved(agent, page):
    page.set_content("<p>Please verify you are human before continuing</p>")
    assert safety.captcha_visible(page) is True
    report = agent.validate_application(page)
    assert report["captcha"] is True
    status, message = safety.handover_status(report)
    assert status == "needs_user_review" and "CAPTCHA" in message


# ---------------------------------------------------------------- never claim unsupported facts
def test_invented_numbers_and_certifications_are_caught():
    resume = "Reduced findings 70% across 60+ AWS accounts. CCNA certified."
    assert safety.unsupported_claims(resume, "Reduced findings 70% across 60+ AWS accounts") == []
    flagged = safety.unsupported_claims(resume, "Cut incidents 92%, CCIE certified")
    assert "92%" in flagged and "CCIE" in flagged


# ---------------------------------------------------------------- hand-over and verification
def test_a_clean_form_is_ready_to_submit_and_a_dirty_one_is_not():
    filled = {"fields_filled": 9, "documents_attached": 1, "form_reached": True}
    assert safety.handover_status(filled)[0] == "ready_to_submit"
    assert safety.handover_status({**filled, "required_still_blank": ["State"]})[0] == "needs_user_review"
    assert safety.handover_status({**filled, "attestations_pending": ["Typed Signature"]})[0] == "needs_user_review"


def test_an_unverified_submission_is_never_recorded_as_submitted():
    assert safety.verification_status("confirmation email: ...")[0] == "submitted"
    status, note = safety.verification_status(None)
    assert status == "needs_user_review" and "no confirmation" in note


def test_validation_reports_blanks_and_errors(agent, page):
    page.set_content("""<label for=s>State *</label><input id=s required>
        <div role=alert>State is required</div>""")
    report = agent.validate_application(page)
    assert report["required_still_blank"] and "State is required" in report["errors_shown"]


# ---------------------------------------------------------------- confirmation detection
def test_confirmation_page_and_portal_list_are_recognised(agent, page):
    page.set_content("<h1>Application Received!</h1><p>Thanks for applying</p>")
    assert agent.submission_confirmed(page) is True
    page.set_content("""<div class=card><h3>Engineer NOC I</h3><span>Applied on Sep 15, 2026</span></div>""")
    assert agent.submission_confirmed(page, "Engineer NOC I") is True
    page.set_content("""<div class=card><h3>Engineer NOC I</h3><span>Draft</span><span>Continue application</span></div>""")
    assert agent.submission_confirmed(page, "Engineer NOC I") is False
    page.set_content("<h1>Apply</h1><button>Submit application</button>")
    assert agent.submission_confirmed(page, "Engineer NOC I") is False


# ---------------------------------------------------------------- confirmation false positives
# Pass 4, Finding 6 (adversarial review, 7 October 2026): a loose "phrase in body" substring
# search over the WHOLE page matched incidental text on an unrelated FAQ/error page -- e.g.
# "Application received after 5pm will be reviewed next day" -- and marked a failed/misdirected
# run CONFIRMED. The fix requires the phrase to BE a short heading/alert element's whole text,
# not merely appear somewhere in a longer sentence anywhere on the page.
@pytest.mark.parametrize("body", [
    "<h2>Application received after 5pm will be reviewed next day</h2>"
    "<p>Please allow 2 business days for a response.</p>",
    "<h3>Status inquiry for previously submitted application</h3>"
    "<p>Enter your confirmation number below to check your status.</p>",
    "<h1>Frequently Asked Questions</h1>"
    "<p>Your application submitted after the deadline will not be considered.</p>",
    "<h2>Need help?</h2><p>If your application was sent to the wrong department, contact HR.</p>",
])
def test_incidental_mentions_on_an_unrelated_page_are_not_a_confirmation(agent, page, body):
    page.set_content(f"<html><body>{body}</body></html>")
    assert agent.submission_confirmed(page) is False


@pytest.mark.parametrize("body", [
    "<h1>Thank You for Applying!</h1>",
    "<h1>Thank you, Jane, for applying to the Software Engineer role at Acme!</h1>",
    "<h2>Your application has been submitted</h2>",
    "<div role=alert>Application submitted.</div>",
    "<h2>We've received your application</h2>",
    "<h1>Application was successfully submitted</h1>",
])
def test_a_real_confirmation_heading_is_still_recognised(agent, page, body):
    page.set_content(f"<html><body>{body}</body></html>")
    assert agent.submission_confirmed(page) is True


# ---------------------------------------------------------------- hidden confirmation templates
# P0-B1 hardening follow-up, 7 October 2026: innerText measurably still returned a display:none
# element's text in this runtime (verified directly, not assumed from the specification) -- a
# conditionally-rendered SPA "success" template, present in the DOM but hidden until an API call
# resolves, could trigger a false CONFIRMED through the exact wording match alone. Policy:
# display:none, the hidden attribute, visibility:hidden/collapse, and full opacity are all
# treated as not-visible (self or an ancestor); off-screen positioning and zero-size-but-
# displayed elements are deliberately not specially detected (documented limitation, not a
# silent gap -- too fragile to distinguish from legitimately positioned real content without
# false-negating it). Fail-closed direction is preserved: every case below is a miss (UNCERTAIN),
# never a wrongly-accepted confirmation.
@pytest.mark.parametrize("body", [
    "<h1 style='display:none'>Thank you for applying!</h1>",
    "<div role='alert' style='display:none'>Application submitted.</div>",
    "<div role='status' style='display:none'>Application submitted.</div>",
    "<div style='display:none'><h1>Thank you for applying!</h1></div>",   # hidden ancestor
    "<h2 hidden>Thank you for applying!</h2>",
    "<div hidden><h2>Thank you for applying!</h2></div>",                 # hidden attribute on ancestor
    "<h2 style='visibility:hidden'>Thank you for applying!</h2>",
    "<h2 style='opacity:0'>Thank you for applying!</h2>",
    # SPA shape: a toggled ancestor hides an otherwise-plainly-displayed child template.
    "<div style='display:none'><h1 style='display:block'>Thank you for applying!</h1></div>",
])
def test_a_hidden_confirmation_template_is_not_a_confirmation(agent, page, body):
    page.set_content(f"<html><body>{body}</body></html>")
    assert agent.submission_confirmed(page) is False


def test_a_success_template_becomes_a_confirmation_once_actually_shown(agent, page):
    """The SPA case this hardening is really about: a template already in the DOM, hidden until
    a state change reveals it (React/Vue toggling display rather than mounting/unmounting).
    Before the toggle it must not confirm; after it, the same element must."""
    page.set_content(
        "<html><body><h1 id='s' style='display:none'>Thank you for applying!</h1></body></html>"
    )
    assert agent.submission_confirmed(page) is False
    page.evaluate("document.getElementById('s').style.display = 'block'")
    assert agent.submission_confirmed(page) is True


# ---------------------------------------------------------------- site adapters stay separate
def test_each_platform_gets_its_own_adapter():
    assert adapter_for("https://career2.successfactors.eu/careers?company=igt").name == "successfactors"
    assert adapter_for("https://bbinsurance.wd1.myworkdayjobs.com/Careers").name == "workday"
    assert adapter_for("https://jobs.cbts.com/careers/job/1443151919604").name == "eightfold"
    assert adapter_for("https://jobs.example.com/roles/42").name == "generic"


def test_generic_form_logic_holds_no_company_names():
    source = (Path(__file__).resolve().parents[1] / "browser_automation.py").read_text(encoding="utf-8")
    for company in ("igt.com", "cbts.com", "ashbyhq", "quantum-health", "bbinsurance"):
        assert company not in source.lower(), company


# ---------------------------------------------------------------- never submit twice
def test_a_previously_submitted_job_is_refused(monkeypatch):
    import apply
    # This test supplies a fake Postgres tracker; do not depend on the local
    # .env selecting that backend rather than the default SQLite tracker.
    monkeypatch.setenv("TRACKER", "postgres")

    class Record:
        title, company, updated_at = "Engineer NOC I", "IGT", "2026-09-15"
        dedup_key = "abc"

    class Tracker:
        def find_submitted(self, url="", company="", title=""):
            return Record() if "igt" in (url + company).lower() else None

    monkeypatch.setitem(sys.modules, "db", type("db", (), {"get_tracker": staticmethod(lambda: Tracker())}))
    assert apply.already_submitted({"url": "https://jobs.igt.com/IGT/job/1?utm_source=LinkedIn",
                                    "company": "IGT", "title": "Engineer NOC I"}) is True
    assert apply.already_submitted({"url": "https://jobs.example.com/1", "company": "Example",
                                    "title": "Network Engineer"}) is False


def test_the_same_posting_with_tracking_parameters_is_the_same_job():
    from db import _strip_tracking
    a = _strip_tracking("https://jobs.igt.com/IGT/job/Austin-1363958457/?utm_source=LinkedIn")
    b = _strip_tracking("https://jobs.igt.com/IGT/job/Austin-1363958457")
    assert a == b


# ---------------------------------------------------------------- the hand-over itself
# (the hand-over is covered in detail by tests/test_auto_submit.py)


# ---------------------------------------------------------------- the basics still work
def test_field_detection_matches_profile_fields(agent, page):
    """Catches a refactor that removes the field-hint tables: a real run died
    with NameError: _FIELD_HINT_PATTERNS on a live Greenhouse form."""
    page.set_content("""<label for=fn>First Name</label><input id=fn>
        <label for=em>Email</label><input id=em>
        <label for=ci>City</label><input id=ci>
        <label for=pz>Zip Code</label><input id=pz>""")
    matched = {f.matched_profile_key for f in agent.detect_form_fields(page)}
    assert {"first_name", "email", "city", "postal_code"} <= matched


def test_every_module_imports_cleanly():
    """A missing name anywhere in the package fails here, not mid-application."""
    import importlib
    for name in ("apply", "apply_flow", "browser_automation", "job_sources", "safety",
                 "web_ui", "db", "job_tracker", "sites", "sites.workday", "sites.successfactors",
                 "sites.eightfold", "sites.greenhouse", "sites.lever", "sites.ashby"):
        importlib.import_module(name)


def test_no_underscore_name_is_used_before_it_exists():
    """Every private name browser_automation.py uses is actually defined
    somewhere in it (module level, nested, or imported). A refactor that
    deletes a helper table fails here instead of mid-application: a live
    Greenhouse run died with NameError: _FIELD_HINT_PATTERNS."""
    import ast
    import browser_automation as ba

    tree = ast.parse(Path(ba.__file__).read_text(encoding="utf-8"))
    defined = set(dir(ba))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(node.name)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                defined.update(a.arg for a in node.args.args + node.args.kwonlyargs)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            defined.add(node.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            defined.update((a.asname or a.name).split(".")[0] for a in node.names)

    used = {n.id for n in ast.walk(tree)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
            and n.id.startswith("_") and not n.id.startswith("__")}
    assert not (used - defined), f"used but never defined: {sorted(used - defined)}"


def test_an_untouched_application_is_never_ready_to_submit():
    """A Google posting needed a sign-in the agent will not do, so it reached
    no form, filled nothing and attached nothing -- and reported the
    application ready to submit. An empty page has no blanks and no errors,
    which is not the same as being finished."""
    status, message = safety.handover_status(
        {"required_still_blank": [], "errors_shown": [], "form_reached": False,
         "fields_filled": 0, "documents_attached": 0}
    )
    assert status == "needs_user_review"
    assert "never reached" in message


def test_a_form_with_nothing_filled_in_is_never_ready_to_submit():
    status, _ = safety.handover_status(
        {"required_still_blank": [], "errors_shown": [], "form_reached": True,
         "fields_filled": 0, "documents_attached": 0}
    )
    assert status == "needs_user_review"


def test_a_filled_form_with_nothing_outstanding_is_ready():
    status, _ = safety.handover_status(
        {"required_still_blank": [], "errors_shown": [], "form_reached": True,
         "fields_filled": 12, "documents_attached": 1}
    )
    assert status == "ready_to_submit"


def test_a_job_that_will_not_sponsor_a_visa_is_recognised():
    """Casey's said it only inside its application, on page nine: "This
    position requires authorization to work in the U.S. without the need for
    employment-based visa sponsorship". The user is on an H-1B."""
    for said in (
        "This position requires authorization to work in the U.S. without the need for "
        "employment-based visa sponsorship.",
        "We are unable to sponsor visas for this role.",
        "Sponsorship is not available.",
        "No visa sponsorship is offered.",
        "U.S. citizens only.",
    ):
        assert safety.no_sponsorship_statement(said), said


def test_a_job_that_sponsors_or_says_nothing_is_not_skipped():
    for said in (
        "Visa sponsorship is available for the right candidate.",
        "Must be authorized to work in the United States.",
        "We offer competitive pay and benefits.",
    ):
        assert not safety.no_sponsorship_statement(said), said



# ---------------------------------------------------------------- sponsorship answers vs the profile
# Schwab's (iCIMS) questions page opened with "No" already chosen for the
# sponsorship question, carried over from an earlier application, and the
# application was submitted automatically with it.

SCHWAB_SPONSORSHIP = ("Do you now, or will you in the future, require sponsorship (e.g., H-1B visa, EAD, etc.) "
                      "to work (or continue to work) legally for the company in the United States?*")
SCHWAB_OPT = "If you will require sponsorship, do you currently hold Optional Practical Training (OPT)?*"
SCHWAB_AUTHORIZED = "Are you currently authorized to work in the U.S.? (If applying to non-US position, select N/A)*"


def _needs_sponsorship():
    from types import SimpleNamespace
    return SimpleNamespace(requires_visa_sponsorship=True, legally_eligible_to_work="Yes")


def test_a_carried_over_no_to_sponsorship_is_a_conflict():
    fields = [{"label": SCHWAB_AUTHORIZED, "value": "Yes"},
              {"label": SCHWAB_SPONSORSHIP, "value": "No"},
              {"label": SCHWAB_OPT, "value": "N/A"}]
    conflicts = safety.legal_answer_conflicts(fields, _needs_sponsorship())
    assert len(conflicts) == 1 and "you need sponsorship" in conflicts[0]


def test_the_right_sponsorship_answer_is_not_a_conflict():
    fields = [{"label": SCHWAB_AUTHORIZED, "value": "Yes"},
              {"label": SCHWAB_SPONSORSHIP, "value": "Yes"},
              {"label": SCHWAB_OPT, "value": "No"},  # a conditional follow-up, not the question itself
              {"label": "Will you now or in the future require immigration sponsorship to work for Casey's?",
               "value": "Yes, I will require sponsorship"}]
    assert safety.legal_answer_conflicts(fields, _needs_sponsorship()) == []


def test_other_wordings_of_the_wrong_answer_are_caught():
    profile = _needs_sponsorship()
    for value in ("No, I do not require sponsorship", "I will not require sponsorship", "", "N/A",
                  "-- Make a Selection --"):
        assert safety.legal_answer_conflicts([{"label": SCHWAB_SPONSORSHIP, "value": value}], profile), value
    assert safety.legal_answer_conflicts([{"label": SCHWAB_AUTHORIZED, "value": "No"}], profile)



# ---------------------------------------------------------------- log redaction
def test_redaction_never_breaks_a_log_line():
    """Redacting the template "Could not read ATS_PASSWORD: %s" used to drop
    the %s while its argument stayed attached, so the line failed to format."""
    import logging

    lines = []

    class Keep(logging.Handler):
        def emit(self, record):
            lines.append(record.getMessage())

    log = logging.getLogger("test.redaction")
    log.propagate = False
    log.addHandler(Keep())
    safety.install_log_redaction("test.redaction")
    safety.install_log_redaction("test.redaction")   # a second install adds nothing
    log.warning("Could not read ATS_PASSWORD: %s", "no .env here")
    log.warning("Reached %s on %s", "someone@example.com", "(555) 010-0000")
    assert lines[0].startswith("Could not read ATS_PASSWORD=***")
    assert "someone@example.com" not in lines[1] and "010-0000" not in lines[1]
    assert sum(isinstance(f, safety.RedactingFilter) for f in log.filters) == 1
