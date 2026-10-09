"""Regressions for observed application-entry, CAPTCHA and confirmation stops."""
import json
from types import SimpleNamespace

import pytest

import apply
import browser_automation
import safety
import web_ui
from browser_automation import JobApplicationAssistant
from submission_guard import SubmissionGuardV0


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        instance = p.chromium.launch(headless=True)
        yield instance
        instance.close()


@pytest.fixture
def page(browser):
    context = browser.new_context()
    result = context.new_page()
    yield result
    context.close()


@pytest.fixture
def assistant():
    result = JobApplicationAssistant.__new__(JobApplicationAssistant)
    result.values = safety.AgentValues()
    result._profile = SimpleNamespace(check_gmail_for_confirmation=True)
    result.raise_window = lambda page: None
    result.pending_attestations = lambda page: []
    return result


@pytest.mark.parametrize("text", [
    "Automating security checks in the CI/CD pipeline.",
    "Perform security checks and review the results.",
    "Experience developing security check tooling is required.",
])
def test_job_responsibilities_are_not_a_captcha(page, text):
    page.set_content(f"<h1>Security Engineer</h1><p>{text}</p>")
    assert safety.captcha_visible(page) is False


@pytest.mark.parametrize("body", [
    "<h1>Security Check</h1>",
    "<p>Please complete the security check before continuing.</p>",
    "<p>Please verify you are human before continuing.</p>",
    '<iframe title="challenge" src="about:blank" width="300" height="300"></iframe>',
])
def test_actual_challenge_evidence_still_requires_the_owner(page, body):
    page.set_content(body)
    assert safety.captcha_visible(page) is True


@pytest.mark.parametrize("body", [
    "<h2>Thank you!</h2><p>Your application was submitted successfully.</p>",
    "<p>Your application has been successfully submitted.</p>",
    "<div role='status'>We’ve received your application.</div>",
    "<h2>Thank you for submitting your application!</h2>",
])
def test_visible_receipt_wording_does_not_require_email(page, assistant, body):
    page.set_content(body)
    assert assistant.submission_confirmed(page) is True


@pytest.mark.parametrize("body", [
    "<p hidden>Your application was submitted successfully.</p>",
    "<p>If your application was submitted successfully, you will receive an email.</p>",
    "<p>Application received after the deadline will not be reviewed.</p>",
    "<p>Your application was submitted successfully.</p><button>Submit Application</button>",
    "<p>Your application was submitted successfully.</p><input><input><input>",
])
def test_templates_advice_and_unfinished_forms_are_not_receipts(page, assistant, body):
    page.set_content(body)
    assert assistant.submission_confirmed(page) is False


def test_application_entry_follows_the_link_without_dispatching_submit_handlers(page, assistant):
    requests = []
    def serve(route):
        requests.append((route.request.url, route.request.method))
        body = ("<h1>Engineer</h1><h2>Responsibilities</h2><p>Build reliable systems.</p>"
                '<a href="/application" onclick="window.badClick=true;return false">Apply Now</a>')
        if route.request.url.endswith("/application"):
            body = '<h1>Application</h1><input aria-label="Name"><button>Submit Application</button>'
        route.fulfill(status=200, content_type="text/html", body=body)
    page.route("https://employer.example/**", serve)
    page.goto("https://employer.example/job")
    assistant._submission_guard = SubmissionGuardV0(page.context)
    target = assistant.click_apply_button(page)
    assert target.url == "https://employer.example/application"
    assert requests[-1] == (target.url, "GET")
    assert assistant._submission_guard.denials(target) == []
    target.get_by_role("button", name="Submit Application").click()
    assert assistant._submission_guard.denial_count(target) == 1


def test_apply_on_a_completed_form_remains_guarded(page, assistant):
    page.set_content('<h1>Review your application</h1><form onsubmit="window.sent=true;return false">'
                     '<button type="submit">Apply Now</button></form>')
    assistant._submission_guard = SubmissionGuardV0(page.context)
    assistant.click_apply_button(page)
    assert page.evaluate("window.sent") is None
    assert assistant._submission_guard.denial_count(page) == 1


def setup_wait(tmp_path, assistant, monkeypatch):
    signal = tmp_path / "_signal_synthetic.txt"
    signal.with_name("_job_synthetic.json").write_text(json.dumps({"company": "Example", "title": "Engineer"}))
    clock = {"seconds": 0.0}
    monkeypatch.setattr(browser_automation.time, "sleep", lambda seconds: clock.update(seconds=clock["seconds"] + seconds))
    assistant._seen_application_form = True
    return signal, clock


def test_page_receipt_finishes_wait_without_opening_gmail(page, assistant, tmp_path, monkeypatch):
    signal, clock = setup_wait(tmp_path, assistant, monkeypatch)
    page.set_content("<h2>Thank you!</h2><p>Your application was submitted successfully.</p>")
    checks = []
    assistant.gmail_shows_confirmation = lambda *args: checks.append(clock["seconds"])
    assert assistant._wait_for_signal(signal, page=page, timeout_seconds=80) == "submitted_by_user"
    assert checks == []


def test_email_fallback_has_a_grace_period_and_bounded_retries(page, assistant, tmp_path, monkeypatch):
    signal, clock = setup_wait(tmp_path, assistant, monkeypatch)
    page.set_content("<h1>Processing your application</h1>")
    checks = []
    assistant.gmail_shows_confirmation = lambda *args: checks.append(clock["seconds"])
    with pytest.raises(TimeoutError):
        assistant._wait_for_signal(signal, page=page, timeout_seconds=500, left_form_seconds=600)
    assert len(checks) == 3
    assert checks[0] >= 45
    assert checks[1] - checks[0] >= 120
    assert checks[2] - checks[1] >= 120


def test_email_fallback_stops_when_the_form_reappears(page, assistant, tmp_path, monkeypatch):
    signal, clock = setup_wait(tmp_path, assistant, monkeypatch)
    page.set_content("<h1>Loading</h1>")
    checks = []
    def sleep(seconds):
        clock["seconds"] += seconds
        if clock["seconds"] >= 10:
            page.set_content('<h1>Application</h1><button>Submit Application</button>')
    monkeypatch.setattr(browser_automation.time, "sleep", sleep)
    assistant.gmail_shows_confirmation = lambda *args: checks.append(clock["seconds"])
    with pytest.raises(TimeoutError):
        assistant._wait_for_signal(signal, page=page, timeout_seconds=70)
    assert checks == []


@pytest.mark.parametrize("outcome", [3, 4])
def test_a_deliberate_skip_is_not_a_failed_command(monkeypatch, outcome):
    monkeypatch.setattr(apply, "get_app_config", lambda: None)
    monkeypatch.setattr(apply, "get_user_profile", lambda: SimpleNamespace(full_name="Example", email="synthetic@example.com"))
    monkeypatch.setattr(apply, "run_one", lambda *args, **kwargs: outcome)
    monkeypatch.setattr(apply.sys, "argv", ["apply.py", "https://employer.example/job"])
    assert apply.main() == outcome


def test_cockpit_reports_a_waiting_worker_instead_of_processing(monkeypatch, tmp_path):
    from job_tracker import JobTracker
    tracker = JobTracker(tmp_path / "apps.db")
    url = "https://employer.example/job"
    tracker.create(dedup_key="synthetic", title="Engineer", company="Example", url=url)
    tracker.update_status("synthetic", "needs_user_review", notes="Owner action is required")
    tracker.update_last_page("synthetic", url)
    monkeypatch.setattr(web_ui, "get_tracker", lambda: tracker)
    monkeypatch.setattr(web_ui, "_LEDGER_DB_PATH", tmp_path / "ledger.db")
    monkeypatch.setattr(web_ui, "_running_url", lambda: url)
    monkeypatch.setattr(web_ui, "waiting_runs", lambda folder: [{"label": "Example", "signal": "synthetic"}])
    monkeypatch.setattr(web_ui.handoff, "get_active_handoff", lambda: None)
    response = web_ui.app.test_client().get("/api/cockpit-state")
    state = response.get_json()
    assert state["is_waiting"] is True
    assert state["stage"] == "Waiting for you"
    assert "processing" not in state["current_action"].lower()


def test_social_destinations_are_rejected_even_when_labelled_apply(page, assistant):
    page.set_content('<h1>Engineer</h1><h2>Requirements</h2><p>Build systems.</p>'
                     '<a href="https://www.facebook.com/sharer/sharer.php">Apply Now</a>')
    assert assistant.follow_application_entry(page, page.locator("a")) is False
    assert len(page.context.pages) == 1


@pytest.mark.parametrize("url, blocked", [
    ("https://www.facebook.com/share", True),
    ("https://m.facebook.com/share", True),
    ("https://x.com/share", True),
    ("https://careers.citrix.com/job", False),
    ("https://employer.example/job", False),
])
def test_social_host_matching_does_not_block_unrelated_employers(url, blocked):
    import job_sources
    assert bool(job_sources.job_board(url)) is blocked


def test_cockpit_continue_creates_the_answer_file(monkeypatch, tmp_path):
    (tmp_path / "_waiting_synthetic.txt").write_text("waiting")
    monkeypatch.setattr(web_ui, "_RUNS_FILE", tmp_path / "_runs.json")
    monkeypatch.setattr(web_ui, "_running_url", lambda: "https://employer.example/job")
    monkeypatch.setitem(web_ui.app.config, "TESTING", True)
    response = web_ui.app.test_client().post("/api/cockpit/resume")
    assert response.status_code == 200
    assert (tmp_path / "_signal_synthetic.txt").read_text() == "continue"


@pytest.mark.parametrize("code, label", [
    (0, "finished"), (3, "finished -- already submitted"),
    (4, "skipped -- sponsorship not available"), (1, "failed (exit 1)"),
])
def test_run_outcomes_keep_skip_and_failure_distinct(code, label):
    import run_outcomes
    assert run_outcomes.display_state(code) == label


def test_automatic_evidence_wait_respects_the_mail_preference(page, assistant, monkeypatch):
    page.set_content("<h1>Processing</h1>")
    assistant._profile.check_gmail_for_confirmation = False
    assistant.employer = "Example"
    checks = []
    monkeypatch.setattr(browser_automation.time, "sleep", lambda seconds: None)
    assistant.gmail_shows_confirmation = lambda *args: checks.append(True)
    assert assistant.wait_for_submission_evidence(page, timeout_seconds=180) is None
    assert checks == []


def test_a_page_receipt_ends_the_browser_context(page, assistant, tmp_path, monkeypatch):
    signal, _ = setup_wait(tmp_path, assistant, monkeypatch)
    page.set_content("<p>Your application was submitted successfully.</p>")
    assistant._context = page.context
    assistant._playwright = None
    assert assistant._wait_for_signal(signal, page=page, timeout_seconds=80) == "submitted_by_user"
    assistant.__exit__(None, None, None)
    assert page.is_closed()


def test_an_automatic_submit_that_left_the_form_visible_does_not_read_mail(page, assistant, monkeypatch):
    page.set_content('<h1>Fix the required field</h1><button>Submit Application</button>')
    assistant.employer = "Example"
    checks = []
    monkeypatch.setattr(browser_automation.time, "sleep", lambda seconds: None)
    assistant.gmail_shows_confirmation = lambda *args: checks.append(True)
    assert assistant.wait_for_submission_evidence(page, timeout_seconds=180) is None
    assert checks == []


def test_an_old_false_failed_skip_is_reconciled_from_the_application(monkeypatch, tmp_path):
    from job_tracker import JobTracker
    tracker = JobTracker(tmp_path / "apps.db")
    url = "https://employer.example/job"
    tracker.create(dedup_key="synthetic", title="Engineer", company="Example", url=url)
    tracker.update_status("synthetic", "skipped", notes="No sponsorship")
    monkeypatch.setattr(web_ui, "get_tracker", lambda: tracker)
    monkeypatch.setattr(web_ui, "_RUNS_FILE", tmp_path / "_runs.json")
    monkeypatch.setattr(web_ui, "_RUNS", {url: {"state": "failed (exit 1)", "log": "", "pid": None, "started": None}})
    assert web_ui._current_runs()[url]["state"] == "finished -- skipped"
