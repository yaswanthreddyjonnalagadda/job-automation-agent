"""What the owner says is final, and a site's confirmation is recognised.

Aristocrat, 29 September: the Review page's Submit was pressed; the confirmation ("We've Received Your
Application!") sat over a form to make a Candidate Home account and was read as a sign-in page, so nothing was
recorded; the owner marked it submitted and pressed Close browser, and the run -- taking "close" for "continue"
-- went on reading pages and set the status back to "needs you".
"""
import re
import inspect
from types import SimpleNamespace

import pytest

import application_status
import apply_flow
import web_ui
from job_tracker import JobTracker
from page_agent import RECEIVED_TEXT, STILL_TO_DO_TEXT


# --- Close browser ends the run --------------------------------------------------------------------------------

def test_every_decision_the_dashboard_sends_is_one_the_flow_knows():
    sent = set(re.search(r'decision in \{([^}]*)\}', inspect.getsource(web_ui.send_signal)).group(1)
               .replace('"', "").replace(" ", "").split(","))
    flow = inspect.getsource(apply_flow)
    handled = set(apply_flow.RUN_ENDS) | {d for d in sent if f'"{d}"' in flow}
    assert "close" in apply_flow.RUN_ENDS
    assert sent <= handled, sent - handled


# --- a run never moves a finished application back ----------------------------------------------------------------

@pytest.mark.parametrize("current, new, by_owner, allowed", [
    ("submitted", "needs_user_review", False, False),
    ("offer", "form_filled", False, False),
    ("submitted", "interviewing", False, True),
    ("needs_user_review", "submitted", False, True),
    ("submitted", "needs_user_review", True, True),        # the owner can always correct it
])
def test_who_may_change_a_status(current, new, by_owner, allowed):
    assert application_status.may_replace(current, new, by_owner) is allowed


def test_the_tracker_keeps_the_owners_submitted(tmp_path):
    tracker = JobTracker(tmp_path / "t.db")
    tracker.create(dedup_key="k", title="Network Engineer", company="Acme")
    tracker.update_status("k", "submitted", by_owner=True)
    tracker.update_status("k", "needs_user_review", notes="the run stopped")
    assert tracker.get("k").status == "submitted"


# --- marking it submitted ends the run working on it -----------------------------------------------------------

def test_marking_the_live_application_submitted_stops_its_run(tmp_path, monkeypatch):
    import profile_setup
    tracker = JobTracker(tmp_path / "t.db")
    tracker.create(dedup_key="k", title="Network Engineer", company="Acme", url="https://jobs.example.com/1")
    app_id = tracker.get("k").id
    ended = []
    monkeypatch.setattr(web_ui, "get_tracker", lambda: tracker)
    monkeypatch.setattr(profile_setup, "needs_setup", lambda: False)
    monkeypatch.setattr(web_ui, "_RUNS_FILE", tmp_path / "_runs.json")
    monkeypatch.setattr(web_ui, "_running_url", lambda: "https://jobs.example.com/1")
    monkeypatch.setattr(web_ui, "_end_any_run", lambda: ended.append(1) or 1)
    monkeypatch.setitem(web_ui._RUNS, "https://jobs.example.com/1", {"state": "running", "log": "", "pid": 5})
    web_ui.app.config["TESTING"] = True
    client = web_ui.app.test_client()
    client.post(f"/application/{app_id}/status", data={"status": "submitted"})
    assert ended == [1] and tracker.get("k").status == "submitted"
    assert "marked it submitted" in web_ui._RUNS["https://jobs.example.com/1"]["state"]


# --- the confirmation under an offer to make an account ----------------------------------------------------------

@pytest.mark.parametrize("text, confirmed", [
    ("Congratulations! We've Received Your Application! Thank you for your interest. If you have not already, "
     "we encourage you to create a Candidate Home account in Workday to enable you to track your progress.", True),
    ("Your application has been submitted. Create an account to see its status.", True),
    ("Thank you for applying! Create an account to complete your application.", False),
    ("We've received your application details. Sign in and then submit your application.", False),
])
def test_a_confirmation_that_offers_an_account_is_still_a_confirmation(text, confirmed):
    assert (bool(RECEIVED_TEXT.search(text)) and not STILL_TO_DO_TEXT.search(text)) is confirmed


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        yield b
        b.close()


WORKDAY_CONFIRMATION = """<html><body><div role="dialog"><h2>Congratulations!</h2>
  <p>We've Received Your Application! Thank you for your interest. If you have not already, we encourage you to
     create a Candidate Home account in Workday to enable you to track your progress.</p>
  <label>Email <input type="email"></label><label>Password <input type="password"></label>
  <button>Create Account</button></div></body></html>"""


@pytest.mark.parametrize("body, confirmed", [
    (WORKDAY_CONFIRMATION, True),
    (WORKDAY_CONFIRMATION.replace("We've Received Your Application!", "Thank you for applying!")
     .replace("to track your progress", "to complete your application"), False),
])
def test_the_agent_reads_workdays_confirmation(browser, body, confirmed):
    import config
    from page_agent import PageAgent
    context = browser.new_context()
    page = context.new_page()
    page.set_content(body)
    job = SimpleNamespace(title="Engineer", company="Acme", url="https://jobs.example.com/1")
    agent = PageAgent(SimpleNamespace(values=None), SimpleNamespace(), SimpleNamespace(auto_submit=False, ats_email=""),
                      config.UserProfile(email="jane@example.com"), SimpleNamespace(raw_text="x"), job,
                      resume_file=None)
    assert agent._site_confirms(page) is confirmed
    context.close()


# --- an AI's "I don't know" is never typed ---------------------------------------------------------------------

@pytest.mark.parametrize("text, unknown", [
    ("Not provided in the resume", True), ("N/A", True), ("Not specified.", True),
    ("The resume does not mention a work phone", True), ("I cannot determine this", True),
    ("Fairfax, VA", False), ("Information Technology", False), ("703-555-0100", False), ("Yes", False),
])
def test_a_non_answer_is_recognised(text, unknown):
    from claude_integration import is_non_answer
    assert is_non_answer(text) is unknown


def test_the_agent_does_not_type_a_non_answer_but_may_choose_a_none_option(caplog):
    import logging
    import config
    from page_agent import Answer, Control, PageAgent
    job = SimpleNamespace(title="Engineer", company="Acme", url="https://jobs.example.com/1")
    agent = PageAgent(SimpleNamespace(values=None), SimpleNamespace(), SimpleNamespace(auto_submit=False, ats_email=""),
                      config.UserProfile(email="jane@example.com"), SimpleNamespace(raw_text="x"), job,
                      resume_file=None)
    agent.tab = lambda page: page
    caplog.set_level(logging.INFO, logger="page_agent")
    phone = Control(ref="e1", role="textbox", name="Work Phone")
    assert agent.do(None, Answer("e1", "Work Phone", "fill", "Not provided in the resume", "ai"), phone) is False
    assert "NOT TYPED" in caplog.text
    caplog.clear()
    choice = Control(ref="e2", role="combobox", name="Disability", options=["Yes", "No", "N/A"])
    try:
        agent.do(None, Answer("e2", "Disability", "choose", "N/A", "ai"), choice)
    except Exception:
        pass                                    # the choice itself needs a page; only the check is tested here
    assert "NOT TYPED" not in caplog.text       # a choice takes a list's own option: "N/A" can be the right one


# --- the owner sent it while the run was busy ---------------------------------------------------------------------

def test_a_confirmation_on_the_page_after_an_error_records_the_application_submitted(browser, tmp_path):
    """Secunetics, 29 September: the owner submitted, the page said 'Your application was submitted
    successfully', the run stopped on an error, and the application was recorded as needing the owner."""
    import config
    from page_agent import PageAgent
    context = browser.new_context()
    page = context.new_page()
    page.set_content("<h2>Thank You</h2><p>Your application was submitted successfully</p>")
    tracker = JobTracker(tmp_path / "t.db")
    tracker.create(dedup_key="k", title="Network Firewall Engineer", company="Acme")
    job = SimpleNamespace(title="Engineer", company="Acme", url="https://jobs.example.com/1")
    agent = PageAgent(SimpleNamespace(values=None), SimpleNamespace(), SimpleNamespace(auto_submit=False, ats_email=""),
                      config.UserProfile(email="jane@example.com"), SimpleNamespace(raw_text="x"), job,
                      resume_file=None)
    assert apply_flow.confirmed_after_all(agent, page, tracker, "k")
    assert tracker.get("k").status == "submitted"
    page.set_content("<h2>Apply</h2><label>Email <input></label>")
    tracker.update_status("k", "needs_user_review", by_owner=True)
    assert not apply_flow.confirmed_after_all(agent, page, tracker, "k")      # a form is not a confirmation
    context.close()
