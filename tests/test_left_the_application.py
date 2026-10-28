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
        return page_agent.Outcome(kind, page, ["the page did not change after three tries"])

    def open_job_page(url):
        visits.append(url)
        if reopen is not None:
            raise reopen
        return page

    helper = assistant(open_job_page=open_job_page, open_embedded_form=lambda p: p,
                       click_apply_button=lambda p: p, dismiss_apply_chooser=lambda p: p,
                       save_progress=lambda *a: None, raise_window=lambda *a: None)
    fake = SimpleNamespace(run=run, tab=lambda p: p, uncertain_action_labels=lambda: [], notes=[], pages_read=1,
                           note_owner_changes=lambda *a: None, _ensure_state=lambda: None,
                           forget_sign_in_attempts=lambda **kw: None, remember_page_state=lambda *a: None)
    monkeypatch.setattr(page_agent, "PageAgent", lambda *a, **kw: fake)
    monkeypatch.setattr(apply_flow, "RuntimeEvent", None)
    for name in ["store_materials", "remember_progress", "write_recovery_checkpoint", "delete_screenshots",
                 "refresh_answer_bank", "save_stop_page", "dump_forensic_failure"]:
        monkeypatch.setattr(apply_flow, name, lambda *a, **kw: None)
    monkeypatch.setattr(apply_flow, "load_latest_code", lambda agent, assistant, *a: assistant)
    tracker = Mock()
    job = SimpleNamespace(title="Senior Network Engineer", company="Walmart", url="https://careers.example.test/jobs/R-1")
    apply_flow.run_page_agent(helper, page, None, SimpleNamespace(generate_cover_letters=False), None, None, job,
                              tracker, "job-1", tmp_path, tmp_path / "resume.pdf", SimpleNamespace(timeout=1),
                              tmp_path / "signal.txt")
    blocked = [c for c in tracker.update_status.call_args_list if c.args[1] == apply_flow.STATUS_BLOCKED_VALIDATION_LOOP]
    return calls["runs"], visits, bool(blocked), job.url


def test_a_loop_on_the_careers_home_goes_back_to_the_job_and_carries_on(monkeypatch, tmp_path, page):
    page.set_content(WALMART_HOME)
    runs, visits, blocked, url = run_flow(monkeypatch, tmp_path, page, ["blocked_validation_loop", "submitted"])
    assert (runs, visits, blocked) == (2, [url], False)


def test_a_loop_on_a_page_that_holds_the_application_is_still_reported(monkeypatch, tmp_path, page):
    page.set_content(APPLICATION_FORM)
    runs, visits, blocked, _ = run_flow(monkeypatch, tmp_path, page, ["blocked_validation_loop"])
    assert (runs, visits, blocked) == (1, [], True)


def test_going_back_is_bounded_so_a_site_that_keeps_sending_it_home_ends_with_the_owner(monkeypatch, tmp_path, page):
    page.set_content(WALMART_HOME)
    runs, visits, blocked, url = run_flow(monkeypatch, tmp_path, page, ["blocked_validation_loop"])
    assert runs == apply_flow.MAX_RETURNS_TO_JOB + 1
    assert visits == [url] * apply_flow.MAX_RETURNS_TO_JOB and blocked


def test_a_job_page_that_cannot_be_reopened_ends_with_the_owner_not_a_crash(monkeypatch, tmp_path, page):
    page.set_content(WALMART_HOME)
    runs, visits, blocked, _ = run_flow(monkeypatch, tmp_path, page, ["blocked_validation_loop"],
                                        reopen=RuntimeError("navigation failed"))
    assert (runs, blocked) == (1, True) and len(visits) == 1
