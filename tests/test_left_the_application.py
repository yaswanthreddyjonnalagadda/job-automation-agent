"""When the site sends the browser somewhere that is neither the application nor its posting, the run goes back (f271).

Walmart, 27 October 2026: after the emailed code was accepted the site showed its careers home page. The run read it as a
step of the application and pressed the home page's picture-carousel "Next" until the loop guard stopped it for the owner.
Two parts: shows_the_application must not take the header's search box for an application field, and the main loop goes
back to the job -- a bounded number of times -- when the loop guard trips on a page that holds no application.
"""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import apply_flow
import page_agent


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


WALMART_HOME = """<header><input type="text" aria-label="Search for jobs, check application status">
<button>Search</button><nav><button>Career areas</button><button>Brands</button></nav></header>
<main><h2>Next move, yours.</h2><button aria-label="Next">Next</button><h2>Trending roles</h2>
<a href="#">Cashiers wanted.</a></main><footer><a href="#">Privacy</a></footer>"""

APPLICATION_FORM = """<header><input type="text" aria-label="Search for jobs"></header>
<main><label>First name <input id="first"></label><button>Next</button></main>"""


def assistant(posting=False, review=False, **extra):
    return SimpleNamespace(on_job_description=lambda p: posting, is_review_step=lambda p: review, **extra)


def test_a_header_search_box_is_not_an_application_field(page):
    page.set_content(WALMART_HOME)
    assert apply_flow.shows_the_application(assistant(), page) is False


def test_a_real_form_still_shows_the_application_beside_a_header_search_box(page):
    page.set_content(APPLICATION_FORM)
    assert apply_flow.shows_the_application(assistant(), page) is True


@pytest.mark.parametrize("markup,posting,review,left", [
    (WALMART_HOME, False, False, True),          # the careers home
    (WALMART_HOME, True, False, False),          # the job's own posting
    (WALMART_HOME, False, True, False),          # a review step has no fields either, and is the owner's
    (APPLICATION_FORM, False, False, False),     # the application itself
])
def test_left_the_application_is_judged_by_what_the_page_shows(page, markup, posting, review, left):
    page.set_content(markup)
    assert apply_flow.left_the_application(assistant(posting, review), page) is left


def run_flow(monkeypatch, tmp_path, page, outcomes, reopen=None):
    """Drive the real run_page_agent loop; `outcomes` are the kinds agent.run returns, in order."""
    visits = []
    calls = {"runs": 0}

    def run(p):
        kind = outcomes[min(calls["runs"], len(outcomes) - 1)]
        calls["runs"] += 1
        if kind == "stalled":          # what PageAgent really returns when its loop guard trips (f271)
            return page_agent.Outcome("owner_needed", page, ["the page did not change after three tries"], stalled=True)
        return page_agent.Outcome(kind, page, ["the page did not change after three tries"])

    def open_job_page(url):
        visits.append(url)
        if reopen is not None:
            raise reopen
        return page

    handed = {"count": 0}

    def hand_over(*args, **kwargs):
        handed["count"] += 1

    helper = assistant(open_job_page=open_job_page, open_embedded_form=lambda p: p,
                       click_apply_button=lambda p: p, dismiss_apply_chooser=lambda p: p,
                       save_progress=lambda *a: None, raise_window=lambda *a: None,
                       submission_step_finality=lambda p: "UNKNOWN", human_handoff=hand_over,
                       wait_for_signal=lambda *a, **kw: "quit", resume_automation=lambda *a: None)
    fake = SimpleNamespace(run=run, tab=lambda p: p, uncertain_action_labels=lambda: [], notes=[], pages_read=1,
                           note_owner_changes=lambda *a: None, _ensure_state=lambda: None,
                           forget_sign_in_attempts=lambda **kw: None, remember_page_state=lambda *a: None)
    monkeypatch.setattr(page_agent, "PageAgent", lambda *a, **kw: fake)
    monkeypatch.setattr(apply_flow, "RuntimeEvent", None)
    for name in ["store_materials", "remember_progress", "write_recovery_checkpoint", "delete_screenshots",
                 "refresh_answer_bank", "save_stop_page", "dump_forensic_failure"]:
        monkeypatch.setattr(apply_flow, name, lambda *a, **kw: None)
    monkeypatch.setattr(apply_flow, "load_latest_code", lambda agent, assistant, *a: assistant)
    monkeypatch.setattr(apply_flow, "hand_over", hand_over)
    tracker = Mock()
    job = SimpleNamespace(title="Senior Network Engineer", company="Walmart", url="https://careers.example.test/jobs/R-1")
    apply_flow.run_page_agent(helper, page, None, SimpleNamespace(generate_cover_letters=False), None, None, job,
                              tracker, "job-1", tmp_path, tmp_path / "resume.pdf", SimpleNamespace(timeout=1),
                              tmp_path / "signal.txt")
    return calls["runs"], visits, handed["count"], job.url


def test_a_loop_on_the_careers_home_goes_back_to_the_job_and_carries_on(monkeypatch, tmp_path, page):
    page.set_content(WALMART_HOME)
    runs, visits, handed, url = run_flow(monkeypatch, tmp_path, page, ["stalled", "submitted"])
    assert (runs, visits, handed) == (2, [url], 0)


def test_a_loop_on_a_page_that_holds_the_application_is_still_reported(monkeypatch, tmp_path, page):
    page.set_content(APPLICATION_FORM)
    runs, visits, handed, _ = run_flow(monkeypatch, tmp_path, page, ["stalled"])
    assert (runs, visits, handed) == (1, [], 1)           # handed to the owner, as before


def test_going_back_is_bounded_so_a_site_that_keeps_sending_it_home_ends_with_the_owner(monkeypatch, tmp_path, page):
    page.set_content(WALMART_HOME)
    runs, visits, handed, url = run_flow(monkeypatch, tmp_path, page, ["stalled"])
    assert runs == apply_flow.MAX_RETURNS_TO_JOB + 1
    assert visits == [url] * apply_flow.MAX_RETURNS_TO_JOB and handed == 1


def test_a_job_page_that_cannot_be_reopened_ends_with_the_owner_not_a_crash(monkeypatch, tmp_path, page):
    page.set_content(WALMART_HOME)
    runs, visits, handed, _ = run_flow(monkeypatch, tmp_path, page, ["stalled"],
                                       reopen=RuntimeError("navigation failed"))
    assert (runs, handed) == (1, 1) and len(visits) == 1


def test_a_hand_over_that_is_not_a_stall_is_never_taken_for_one(monkeypatch, tmp_path, page):
    """A CAPTCHA or a question for the owner on a page with no form is the owner's, not a page to leave."""
    page.set_content(WALMART_HOME)
    runs, visits, handed, _ = run_flow(monkeypatch, tmp_path, page, ["owner_needed"])
    assert (runs, visits, handed) == (1, [], 1)


def test_an_outcome_is_not_a_stall_unless_the_loop_guard_says_so():
    assert page_agent.Outcome("owner_needed", None, ["x"]).stalled is False


def _real_agent():
    import config
    import safety
    from browser_automation import JobApplicationAssistant

    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    assistant.values = safety.AgentValues()
    job = SimpleNamespace(title="Senior Network Engineer", company="Walmart", url="https://careers.example.test/jobs/R-1")
    return page_agent.PageAgent(assistant, SimpleNamespace(), SimpleNamespace(auto_submit=False, ats_email=""),
                                config.UserProfile(email="o@example.com"), SimpleNamespace(raw_text="Network engineer"),
                                job, resume_file=None)


def _press_next_once(agent, page):
    snapshot = agent.snapshot(page)
    controls = page_agent.parse_snapshot(snapshot)
    plan = page_agent.PagePlan(page_kind="application_form", next_kind="next_step", next_label="Next",
                               next_ref=next(c.ref for c in controls if c.name == "Next"))
    return agent.press_next(page, plan, controls)


def test_the_real_loop_guard_marks_its_stop_as_a_stall(page):
    """f271, through the real PageAgent: Next pressed three times on a page that never changes. This is the signal
    apply_flow recovers on; an earlier version of the recovery watched for a kind this path never produced."""
    page.set_content('<label for="a">Answer *</label><input id="a"><button id="n">Next</button>')   # Next does nothing
    agent = _real_agent()
    results = [_press_next_once(agent, page) for _ in range(3)]
    assert results[-1][0] == "stop" and "three tries" in results[-1][2]
    assert agent.stalled_on_page is True
