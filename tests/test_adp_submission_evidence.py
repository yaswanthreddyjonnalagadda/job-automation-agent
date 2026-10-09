"""Final-step evidence must survive a portal's route and panel changes."""
import importlib
import json
from types import SimpleNamespace

import pytest

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


POSTING = "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?cid=tenant&ccId=center&jobId=123"
CURRENT = "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/postLogin.html?cid=tenant&ccId=center&jobId=123&requisitionId=req-abc"


@pytest.mark.parametrize("change,accepted", [
    ({}, True),
    ({"itemID": "other"}, False),
    ({"requisitionTitle": "Other role"}, False),
    ({"customFieldGroup": {"stringFields": []}}, False),
])
def test_adp_redirect_identity_requires_requisition_metadata(page, monkeypatch, change, accepted):
    metadata = {"itemID": "req-abc", "requisitionTitle": "Security Engineer",
                "customFieldGroup": {"stringFields": [
                    {"nameCode": {"codeValue": "ExternalJobID"}, "stringValue": "123"}]}}
    metadata.update(change)
    monkeypatch.setattr(page.request, "get", lambda *a, **k: SimpleNamespace(ok=True, json=lambda: metadata))
    page.route("**/*", lambda r: r.fulfill(
        content_type="application/json" if "/job-requisitions/" in r.request.url else "text/html",
        body=json.dumps(metadata) if "/job-requisitions/" in r.request.url else "<h1>Application</h1>"))
    page.goto(CURRENT)
    adapter = importlib.import_module("sites.adp").ADPAdapter()
    result = adapter.submission_posting_url(page, POSTING, "Security Engineer")
    assert result == (POSTING if accepted else None)


@pytest.mark.parametrize("current", [
    CURRENT.replace("cid=tenant", "cid=other"),
    CURRENT.replace("ccId=center", "ccId=other"),
    CURRENT.replace("jobId=123", "jobId=456"),
    CURRENT.replace("requisitionId=req-abc", "requisitionId="),
    CURRENT.replace("workforcenow.adp.com", "workforcenow.adp.com.evil.example"),
    CURRENT + "&jobId=456",
])
def test_adp_identity_refuses_other_jobs_tenants_and_hosts(page, current):
    page.route("**/*", lambda r: r.fulfill(content_type="text/html", body="<h1>Application</h1>"))
    page.goto(current)
    adapter = importlib.import_module("sites.adp").ADPAdapter()
    assert adapter.submission_posting_url(page, POSTING, "Security Engineer") is None


@pytest.mark.parametrize("plain_rows", [False, True, "hidden"])
def test_review_attachment_is_read_and_final_panel_restored(page, plain_rows):
    html = """
      <ol><li><button onclick="document.getElementById('panel').innerHTML='<a href=/resume>Resume_Example.pdf</a>'">Review Your Application</button></li>
      <li><button onclick="document.getElementById('panel').innerHTML='<button type=button>Submit</button>'">Self-Attest &amp; Submit</button></li></ol>
      <h1>Review Your Application</h1><h2>Self-Attest &amp; Submit</h2>
      <main id=panel><button type=button>Submit</button></main>
    """
    if plain_rows:
        html = html.replace("<li><button onclick=", "<li onclick=").replace("</button></li>", "</li>")
        html = html.replace(">Review Your Application</li>", ">5 Review Your Application</li>")
        html = html.replace(">Self-Attest &amp; Submit</li>", ">6 Self-Attest &amp; Submit</li>")
    if plain_rows == "hidden":
        html = html.replace("</li>", "<button hidden>Details</button></li>")
    page.route("**/*", lambda r: r.fulfill(content_type="text/html", body=html))
    page.goto(CURRENT)
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    adapter = importlib.import_module("sites.adp").ADPAdapter()
    assert assistant.attached_document_names(page) == []
    assert adapter.submission_documents(assistant, page) == ["Resume_Example.pdf"]
    assert page.get_by_role("button", name="Submit", exact=True).is_visible()
    assert page.get_by_role("link", name="Resume_Example.pdf").count() == 0


def test_missing_review_panel_does_not_claim_attachment(page):
    page.set_content("<button type=button>Submit</button>")
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    adapter = importlib.import_module("sites.adp").ADPAdapter()
    assert adapter.submission_documents(assistant, page) is None


@pytest.mark.parametrize("allowed,checked,approved", [
    (True, True, True), (False, True, False), (True, False, False),
])
def test_signature_checkbox_approval_uses_explicit_profile_permission(allowed, checked, approved):
    field = {"ref": "signature-consent", "label": "Yes, I agree to sign electronically.",
             "type": "checkbox", "value": "checked" if checked else "", "required": True}
    profile = SimpleNamespace(sign_attestations=allowed)
    values = safety.approved_values([field], profile, {}, {})
    assert safety.compare_fields([field], values)[0].matches is approved


def test_signing_permission_does_not_approve_unrelated_checkbox_or_typed_claim():
    fields = [{"label": "Subscribe to the newsletter", "type": "checkbox", "value": "checked"},
              {"label": "I certify this claim", "type": "text", "value": "checked"}]
    assert safety.approved_values(fields, SimpleNamespace(sign_attestations=True), {}, {}) == {}


def test_electronic_signing_word_order_is_still_an_attestation():
    assert safety.is_attestation("Yes, I agree to sign electronically.")
    assert safety.is_attestation("Privacy notice: I agree to sign electronically.")
    assert not safety.is_attestation("Sign up for job alerts")


def test_confirmed_auto_submit_returns_without_waiting_for_an_owner_signal(page, tmp_path, monkeypatch):
    import apply_flow
    import config
    import page_agent
    outcome = SimpleNamespace(kind="owner_needed", page=page, summary="Ready for review", reasons=["last step"])
    reading_agent = SimpleNamespace(run=lambda p: outcome, tab=lambda p: p, notes=[], pages_read=1,
                                    uncertain_action_labels=lambda: [])
    monkeypatch.setattr(page_agent, "PageAgent", lambda *a, **k: reading_agent)
    for name in ("store_materials", "remember_progress", "write_recovery_checkpoint"):
        monkeypatch.setattr(apply_flow, name, lambda *a, **k: None)
    monkeypatch.setattr(apply_flow, "hand_over", lambda *a, **k: "submitted")

    def must_not_wait(*args, **kwargs):
        pytest.fail("confirmed auto-submit must finish, allowing browser cleanup")

    assistant = SimpleNamespace(is_review_step=lambda p: True, save_progress=lambda p: None,
                                wait_for_signal=must_not_wait)
    job = SimpleNamespace(title="Engineer", company="Example", url=POSTING)
    apply_flow.run_page_agent(assistant, page, SimpleNamespace(), SimpleNamespace(generate_cover_letters=False),
                             config.UserProfile(full_name="Example Applicant"), SimpleNamespace(), job,
                             SimpleNamespace(), "key", tmp_path, tmp_path / "Resume.pdf",
                             SimpleNamespace(timeout=1), tmp_path / "signal")


def test_review_resume_reactivates_automation_before_reading_again(page, tmp_path, monkeypatch):
    import apply_flow
    import config
    import page_agent
    active = []
    outcomes = iter([
        SimpleNamespace(kind="owner_needed", page=page, summary="Ready for review", reasons=["last step"]),
        SimpleNamespace(kind="submitted", page=page, summary="Confirmed", reasons=[]),
    ])

    def read(p):
        result = next(outcomes)
        if result.kind == "submitted":
            assert active == [True]
        return result

    reading_agent = SimpleNamespace(run=read, tab=lambda p: p, notes=[], pages_read=1,
        uncertain_action_labels=lambda: [], note_owner_changes=lambda p: None,
        _ensure_state=lambda: None, forget_sign_in_attempts=lambda **k: None)
    monkeypatch.setattr(page_agent, "PageAgent", lambda *a, **k: reading_agent)
    for name in ("store_materials", "remember_progress", "write_recovery_checkpoint", "delete_screenshots"):
        monkeypatch.setattr(apply_flow, name, lambda *a, **k: None)
    monkeypatch.setattr(apply_flow, "hand_over", lambda *a, **k: "ready_to_submit")
    monkeypatch.setattr(apply_flow, "load_latest_code", lambda a, assistant, *rest: assistant)
    monkeypatch.setattr(apply_flow.diagnostics, "capture_safe_screenshot", lambda *a, **k: None)
    assistant = SimpleNamespace(is_review_step=lambda p: True, save_progress=lambda p: None,
        wait_for_signal=lambda *a, **k: "reload_code", resume_automation=lambda p: active.append(True))
    job = SimpleNamespace(title="Engineer", company="Example", url=POSTING)
    apply_flow.run_page_agent(assistant, page, SimpleNamespace(), SimpleNamespace(generate_cover_letters=False),
        config.UserProfile(full_name="Example Applicant"), SimpleNamespace(), job,
        SimpleNamespace(update_status=lambda *a, **k: None), "key", tmp_path,
        tmp_path / "Resume.pdf", SimpleNamespace(timeout=1), tmp_path / "signal")


def test_signing_checkbox_revealing_a_known_required_name_is_reread(page, tmp_path):
    import config
    import page_agent
    page.set_content("""<h1>Self-Attest &amp; Submit</h1>
      <label><input id=consent type=checkbox required
         onchange="document.getElementById('signature').innerHTML='<label>Please type your full name.<input id=fullname required></label>'">
        Yes, I agree to sign electronically.</label>
      <div id=signature></div><button type=button onclick="window.sent=true">Submit</button>""")
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    assistant.values = safety.AgentValues()
    profile = config.UserProfile(full_name="Example Applicant", sign_attestations=True)
    agent = page_agent.PageAgent(assistant, SimpleNamespace(),
        SimpleNamespace(form_answer_mode="profile", auto_submit=False, ats_email=""), profile,
        SimpleNamespace(raw_text=""), SimpleNamespace(title="Engineer", company="Example", url=POSTING),
        job_dir=tmp_path)
    outcome = agent.run(page)
    assert page.locator("#fullname").input_value() == profile.full_name, (
        outcome.reasons, [(c.role, c.question, agent.known_answer(c), agent._attestation_answer(c))
                          for c in page_agent.parse_snapshot(agent.snapshot(page)) if c.role == "textbox"])
    assert not page.evaluate("Boolean(window.sent)")
    assert not any("still blank" in reason for reason in outcome.reasons)


@pytest.mark.parametrize("question", ["Please type your full name.", "Enter your legal name", "Provide complete name"])
def test_imperative_full_name_prompt_resolves_to_profile(question):
    import concept_matcher
    assert concept_matcher.match_concept(question=question) == "FULL_NAME"


@pytest.mark.parametrize("question", ["Please enter your employer full name", "Please type your supervisor legal name"])
def test_other_peoples_names_are_not_the_applicants_full_name(question):
    import concept_matcher
    assert concept_matcher.match_concept(question=question) != "FULL_NAME"
