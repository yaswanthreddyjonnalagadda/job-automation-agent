"""A reopened page must be this application, and an action whose result nobody saw is never taken again.

KBI Biopharma (Workday), 28 September: "Continuing where the last run left it" reopened the careers home
(/en-US/KBI_Biopharma/), which has no form and no posting. The first check accepted any visible input as the
application and carried on when the page could not be read; the review of 1 October asked for the application's
identity to be checked, and for an interrupted Submit to become "outcome unknown -- check first".

Local pages served on example.com only; no sign-in, no network.
"""
from types import SimpleNamespace

import pytest

import checkpoint


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


@pytest.fixture(autouse=True)
def folder(tmp_path, monkeypatch):
    monkeypatch.setattr(checkpoint, "FOLDER", tmp_path / "checkpoints")


JOB = SimpleNamespace(url="https://jobs.example.com/acme/job/Network-Engineer_R1", title="Network Engineer",
                      company="Acme")
CAREERS_HOME = """<header><button>Sign In</button><nav><button>Search for Jobs</button></nav></header>
<footer><a href="#">Privacy Policy</a></footer>"""


def serve(page, body, url="https://jobs.example.com/acme/"):
    page.route("https://**/*", lambda route: route.fulfill(status=200, content_type="text/html",
                                                           body=f"<html><body>{body}</body></html>"))
    page.goto(url)


def not_posting(_page):
    return False


def test_a_careers_home_is_not_the_application(page):
    checkpoint.note_application("k", JOB)
    serve(page, CAREERS_HOME)
    assert not checkpoint.reconcile("k", page, not_posting).is_the_application


def test_an_unrelated_form_on_the_same_site_is_not_the_application(page):
    checkpoint.note_application("k", JOB)
    serve(page, CAREERS_HOME + '<label>Search <input type="text"></label><label>Subscribe <input></label>')
    assert checkpoint.reconcile("k", page, not_posting).verdict == checkpoint.DIFFERENT


@pytest.mark.parametrize("body", [
    "<h2>Network Engineer</h2><label>Email* <input type=email></label>",
    "<ol aria-label='Application Progress'><li>current step 2 of 7</li></ol><label>City <input></label>",
])
def test_the_job_or_a_step_counter_shows_the_application(page, body):
    checkpoint.note_application("k", JOB)
    serve(page, CAREERS_HOME + body)
    assert checkpoint.reconcile("k", page, not_posting).verdict == checkpoint.SAME_APPLICATION


def test_the_last_verified_page_with_its_form_counts(page):
    checkpoint.note_application("k", JOB)
    checkpoint.verified("k", "https://jobs.example.com/acme/apply/step3")
    serve(page, '<label>Degree <input></label>', url="https://jobs.example.com/acme/apply/step3")
    assert checkpoint.reconcile("k", page, not_posting).verdict == checkpoint.SAME_APPLICATION


def test_another_site_is_never_the_application(page):
    checkpoint.note_application("k", JOB)
    serve(page, "<h2>Network Engineer</h2><input>", url="https://elsewhere.example.org/x")
    assert checkpoint.reconcile("k", page, not_posting).verdict == checkpoint.DIFFERENT


def test_the_posting_counts(page):
    checkpoint.note_application("k", JOB)
    serve(page, CAREERS_HOME)
    assert checkpoint.reconcile("k", page, lambda p: True).verdict == checkpoint.POSTING


def test_a_page_that_cannot_be_read_is_never_taken_for_the_application():
    class Broken:
        url = "https://jobs.example.com/acme/"

        def wait_for_timeout(self, ms):
            raise RuntimeError("target closed")
    checkpoint.note_application("k", JOB)
    assert checkpoint.reconcile("k", Broken(), not_posting).verdict == checkpoint.UNREADABLE


def test_a_submit_whose_result_was_not_seen_stays_unresolved_until_the_owner_checks():
    checkpoint.begin("k", "submit", "Network Engineer at Acme")
    pending = checkpoint.unresolved("k")
    assert pending and pending["action"] == "submit"
    assert "will not press Submit again" in checkpoint.what_to_check(pending, JOB)
    checkpoint.owner_checked("k")
    assert checkpoint.unresolved("k") is None
    assert checkpoint.load("k")["history"][-1]["outcome"] == "checked by the owner"


def test_a_submit_seen_through_leaves_nothing_pending():
    checkpoint.begin("k", "submit")
    checkpoint.finish("k", "submit", "submitted: confirmation page")
    assert checkpoint.unresolved("k") is None


def test_every_record_names_the_code_version_it_ran():
    checkpoint.verified("k", "https://jobs.example.com/acme/apply")
    assert checkpoint.load("k")["verified"]["code"] == checkpoint.code_version() != ""


def test_a_new_run_keeps_the_sites_the_application_was_already_worked_on(page):
    """Waystar, 2 October: each run's note_application overwrote the record, dropping the employer's Workday host the
    last run had verified, and the resume called the Workday page "not where this application was"."""
    key = "waystar-1"
    checkpoint.note_application(key, JOB)
    checkpoint.verified(key, "https://acme.wd1.myworkdayjobs.com/en-US/Acme/job/x/apply", "Create Account/Sign In")
    checkpoint.note_application(key, JOB)                     # the next run starts
    serve(page, "<h2>Network Engineer</h2><p>current step 1 of 6</p>",
          url="https://acme.wd1.myworkdayjobs.com/en-US/Acme/job/x/apply")
    assert checkpoint.reconcile(key, page, not_posting).verdict == checkpoint.SAME_APPLICATION
