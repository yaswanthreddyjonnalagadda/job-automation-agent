"""
Verified auto-submit: eligibility, exact matching, refusal on any uncertainty,
evidence-based confirmation, and recovery after the user clears a CAPTCHA.

Nothing here touches an employer site: pages are local HTML and the tracker is
a stand-in.

    venv\\Scripts\\python -m pytest tests -q
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import apply_flow  # noqa: E402
import safety  # noqa: E402
from browser_automation import JobApplicationAssistant  # noqa: E402


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
    a = JobApplicationAssistant.__new__(JobApplicationAssistant)
    a.values = safety.AgentValues()
    return a


class Profile:
    full_name = "Yaswanth Reddy Jonnalagadda"
    email = "me@example.com"
    city = "Fairfax"
    state = "Virginia"


class Tracker:
    """Records what the flow asked it to store."""

    def __init__(self, docs_match=True):
        self.status = None
        self.notes = None
        self.events = []
        self._docs_match = docs_match

    def update_status(self, key, status, notes=None):
        self.status, self.notes = status, notes

    def record_event(self, key, kind, message="", screenshot_path="", html_path="", payload=None):
        self.events.append((kind, message, payload))

    def document_matches(self, key, kind, path):
        return self._docs_match

    def get(self, key):
        class Record:
            title, company = "Network Engineer", "Example Corp"
            url = "https://jobs.example.com/apply/42"
        return Record()


class Job:
    title, company, url = "Network Engineer", "Example Corp", "https://jobs.example.com/apply/42"


def serve(page, body: str, url: str = "https://jobs.example.com/apply/42"):
    """Puts a local page at a real URL, so URL checks behave as they do live."""
    page.route("**/*", lambda route: route.fulfill(status=200, content_type="text/html", body=body))
    page.goto(url)


BASE = {"title": "Network Engineer", "company": "Example Corp", "url": "https://jobs.example.com/apply/42"}


def decision(**overrides):
    args = dict(
        enabled=True, job=dict(BASE), tracked=dict(BASE), report={},
        form_fields=[{"label": "City", "value": "Fairfax", "required": True, "source": "agent"}],
        approved={"City": "Fairfax"},
        uploaded_documents=["Resume_Example.pdf"], expected_documents=["Resume_Example.pdf"],
        documents_verified=True,
    )
    args.update(overrides)
    return safety.evaluate_auto_submit(**args)


# ---------------------------------------------------------------- eligibility
def test_everything_matching_is_eligible():
    result = decision()
    assert result.eligible and result.reasons == []
    assert [c.matches for c in result.field_comparisons] == [True]


def test_disabled_setting_alone_blocks_it():
    result = decision(enabled=False)
    assert not result.eligible
    assert any("AUTO_SUBMIT_VERIFIED_ONLY" in r for r in result.reasons)


def test_the_setting_is_off_by_default():
    from config import AppConfig
    import os
    assert os.getenv("AUTO_SUBMIT_VERIFIED_ONLY") in (None, "", "false", "False")
    assert AppConfig().auto_submit_verified_only is False


# ---------------------------------------------------------------- exact matching only
@pytest.mark.parametrize("on_form,approved,expected", [
    ("Fairfax", "Fairfax", True),
    ("  fairfax ", "Fairfax", True),
    ("(571) 354-5212", "571-354-5212", True),
    ("Asian (Not Hispanic or Latino)", "Asian", True),
    ("Fairfax County", "Fairfax", False),
    ("United States Minor Outlying Islands", "United States", False),
    ("", "Fairfax", False),
    ("Fairfax", "", False),
])
def test_values_match_is_exact(on_form, approved, expected):
    assert safety.values_match(on_form, approved) is expected


def test_a_field_with_no_approved_value_blocks_submission():
    result = decision(
        form_fields=[{"label": "How many people did you manage?", "value": "5", "required": True}],
        approved={},
    )
    assert not result.eligible
    assert any("no approved value" in r for r in result.reasons)


def test_a_mismatched_field_blocks_submission():
    result = decision(form_fields=[{"label": "City", "value": "Richmond", "required": True}])
    assert not result.eligible
    assert any("Richmond" in r and "Fairfax" in r for r in result.reasons)


# ---------------------------------------------------------------- refusal on uncertainty
@pytest.mark.parametrize("report_key,value", [
    ("required_still_blank", ["State"]),
    ("errors_shown", ["State is required"]),
    ("warnings_shown", ["Some sections are incomplete"]),
    ("ambiguous_choices", ["'Race': wanted 'Asian', offered ['Other']"]),
    ("unsupported_questions", ["Describe your ideal team"]),
    ("attestations_pending", ["Typed Signature"]),
    ("identity_checks", ["a one-time code step"]),
    ("unanswered_questions", ["Preferred start date"]),
])
def test_any_uncertainty_refuses(report_key, value):
    result = decision(report={report_key: value})
    assert not result.eligible and result.reasons


def test_a_captcha_refuses():
    assert not decision(report={"captcha": True}).eligible


def test_a_different_job_or_url_refuses():
    assert not decision(job={**BASE, "title": "Senior Network Engineer"}).eligible
    assert not decision(job={**BASE, "url": "https://jobs.example.com/apply/99"}).eligible
    # tracking parameters are not a difference
    assert decision(job={**BASE, "url": BASE["url"] + "?utm_source=LinkedIn"}).eligible


def test_wrong_or_unverified_documents_refuse():
    assert not decision(uploaded_documents=["Someone_Else.pdf"]).eligible
    assert not decision(documents_verified=False).eligible
    assert not decision(uploaded_documents=[]).eligible


# ---------------------------------------------------------------- the flow
def test_hand_over_records_evidence_and_does_not_submit_by_default(page, tmp_path, monkeypatch):
    page.set_content("<label for=c>City</label><input id=c value='Fairfax'><button>Submit application</button>")

    class Assistant:
        raised = False
        values = safety.AgentValues()
        def validate_application(self, page, resume_name=""):
            return {"required_still_blank": [], "errors_shown": [], "unanswered_questions": [],
                    "attestations_pending": [], "warnings_shown": [], "ambiguous_choices": [],
                    "unsupported_questions": [], "identity_checks": [], "attached_documents": [],
                    "captcha": False, "resume_attached": True, "page_url": page.url}
        def read_back_fields(self, page):
            return [{"ref": "c", "label": "City", "value": "Fairfax", "required": True}]
        def raise_window(self, page):
            Assistant.raised = True
        def click_verified_submit(self, page):
            raise AssertionError("must not submit while the setting is off")

    tracker = Tracker()
    summary = tmp_path / "review_summary.json"
    summary.write_text("{}", encoding="utf-8")

    class Config:
        auto_submit_verified_only = False

    status = apply_flow.hand_over(Assistant(), page, tracker, "key", Job(), tmp_path, "Resume.pdf",
                                  summary, config=Config(), profile=Profile(),
                                  documents={"resume": tmp_path / "Resume.pdf"})

    assert status == "ready_to_submit" and tracker.status == "ready_to_submit"
    assert Assistant.raised is True
    audit = [e for e in tracker.events if e[0] == "auto_submit"]
    assert audit and audit[0][2]["eligible"] is False
    assert any("AUTO_SUBMIT_VERIFIED_ONLY" in r for r in audit[0][2]["reasons"])
    evidence = list(tmp_path.glob("evidence_*/*"))
    assert {p.name for p in evidence} >= {"page.png", "page.html", "comparison.json"}
    comparison = json.loads(next(p for p in evidence if p.name == "comparison.json").read_text(encoding="utf-8"))
    assert comparison["field_comparisons"][0]["label"] == "City"


def test_submission_is_recorded_only_on_evidence(page, tmp_path):
    class Assistant:
        def click_verified_submit(self, page):
            return True
        def wait_for_submission_evidence(self, page, job_title="", timeout_seconds=0):
            return None            # the site said nothing

    tracker = Tracker()
    ok = apply_flow.submit_verified(Assistant(), page, tracker, "key", Job(), tmp_path,
                                    safety.AutoSubmitDecision(eligible=True))
    assert ok is False and tracker.status == "needs_user_review"

    class Confirming(Assistant):
        def wait_for_submission_evidence(self, page, job_title="", timeout_seconds=0):
            return "the page confirmed it"

    tracker2 = Tracker()
    assert apply_flow.submit_verified(Confirming(), page, tracker2, "key", Job(), tmp_path,
                                      safety.AutoSubmitDecision(eligible=True)) is True
    assert tracker2.status == "submitted"


# ---------------------------------------------------------------- recovery after the user helps
def test_automation_resumes_after_the_user_clears_a_captcha(agent, page):
    page.set_content("<p>Please verify you are human</p><script>setTimeout(() => "
                     "{ document.body.innerHTML = '<label for=c>City</label><input id=c>' }, 1500)</script>")
    assert safety.captcha_visible(page) is True
    assert agent.wait_out_captcha(page, timeout_seconds=30) is True   # waits, never solves
    assert safety.captcha_visible(page) is False
    assert agent.set_value(page, "[id='c']", "Fairfax", "City") is True  # ordinary automation resumes


def test_the_agent_never_touches_the_captcha_itself(agent, page):
    page.set_content("<iframe title='reCAPTCHA challenge' style='width:300px;height:400px'></iframe>"
                     "<button onclick='window.clicked=1'>Verify</button>")
    agent.wait_out_captcha(page, timeout_seconds=6)
    assert page.evaluate("() => !!window.clicked") is False


# ---------------------------------------------------------------- end to end, on a local form
def test_verified_auto_submit_end_to_end(page, tmp_path, agent):
    """With the setting ON and everything matching, the agent submits and then
    records submitted only because the page confirmed it."""
    serve(page,
          "<h2>Apply</h2>"
          "<label for=c>City *</label><input id=c required>"
          "<p>Attached: Resume_Example.pdf</p>"
          "<button id=go onclick=\"document.body.innerHTML='<h1>Application Received!</h1>'\">"
          "Submit application</button>")
    agent.employer = "Example Corp"
    agent.set_value(page, "[id='c']", "Fairfax", "City *", source="profile:city")

    class Tracker2(Tracker):
        def document_matches(self, key, kind, path):
            return True

    tracker = Tracker2()
    summary = tmp_path / "review_summary.json"
    summary.write_text("{}", encoding="utf-8")
    resume = tmp_path / "Resume_Example.pdf"
    resume.write_bytes(b"%PDF-1.4")

    class Config:
        auto_submit_verified_only = True

    status = apply_flow.hand_over(agent, page, tracker, "key", Job(), tmp_path, resume.name, summary,
                                  config=Config(), profile=Profile(), documents={"resume": resume})

    assert status == "submitted", tracker.notes
    assert tracker.status == "submitted" and "confirmed" in (tracker.notes or "")
    assert page.locator("text=Application Received!").count() == 1
    audit = [e for e in tracker.events if e[0] == "auto_submit"]
    assert audit and audit[0][2]["eligible"] is True


def test_verified_auto_submit_stops_when_one_field_is_unapproved(page, tmp_path, agent):
    """Same page, but a field holds something no approved source supports."""
    serve(page,
          "<label for=c>City *</label><input id=c required>"
          "<label for=q>Notice period *</label><input id=q value='2 weeks' required>"
          "<p>Attached: Resume_Example.pdf</p>"
          "<button onclick='window.submitted=1'>Submit application</button>")
    agent.employer = "Example Corp"
    agent.set_value(page, "[id='c']", "Fairfax", "City *", source="profile:city")

    tracker = Tracker()
    summary = tmp_path / "review_summary.json"
    summary.write_text("{}", encoding="utf-8")
    resume = tmp_path / "Resume_Example.pdf"
    resume.write_bytes(b"%PDF-1.4")

    class Config:
        auto_submit_verified_only = True

    status = apply_flow.hand_over(agent, page, tracker, "key", Job(), tmp_path, resume.name, summary,
                                  config=Config(), profile=Profile(), documents={"resume": resume})

    # The form itself is complete and clean, so it is handed over for the user
    # to submit -- auto-submit simply refused to do it.
    assert status == "ready_to_submit"
    assert page.evaluate("() => !!window.submitted") is False
    audit = [e for e in tracker.events if e[0] == "auto_submit"][0][2]
    assert any("Notice period" in r for r in audit["reasons"])
