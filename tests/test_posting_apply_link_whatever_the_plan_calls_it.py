"""A posting's Apply link is followed with a plain GET, whatever kind of step the plan calls it (f170).

Marathon's Workday posting: Apply is `<a role="button" href=".../apply">`. The plan named it a next step (or even the
final one), so the agent clicked it, the submission guard -- rightly -- refused a click on a button labelled Apply,
and the run stopped with 'final submission is only available through the verified gateway' on every attempt.
Following the link's address opens the application and submits nothing, so it never needed the click.
"""
from types import SimpleNamespace

import pytest

import page_agent
from submission_guard import SubmissionGuardV0
from test_run_recovery_regressions import assistant, browser, page  # noqa: F401


def agent_on_posting(assistant, page, tmp_path):
    def serve(route):
        body = ('<h1>Engineer</h1><h2>Responsibilities</h2><p>Build systems.</p>'
                '<a role="button" data-automation-id="adventureButton" href="/job/apply?source=x"'
                ' onclick="window.clicked=true;return false">Apply</a>')
        if "/apply" in route.request.url:
            body = '<h1>Application</h1><button>Submit Application</button>'
        route.fulfill(status=200, content_type="text/html", body=body)
    page.route("https://employer.example/**", serve)
    page.goto("https://employer.example/job")
    assistant._submission_guard = SubmissionGuardV0(page.context)
    return page_agent.PageAgent(
        assistant, SimpleNamespace(), SimpleNamespace(), SimpleNamespace(accept_application_privacy_prompts=False),
        SimpleNamespace(raw_text=""), SimpleNamespace(url=page.url), resume_file=None, job_dir=tmp_path)


@pytest.mark.parametrize("kind", ["open_application", "next_step", "final_submit"])
def test_the_apply_link_is_followed_not_clicked(assistant, page, tmp_path, kind):
    agent = agent_on_posting(assistant, page, tmp_path)
    controls = page_agent.parse_snapshot(agent.snapshot(page))
    entry = next(c for c in controls if c.name == "Apply")
    plan = page_agent.PagePlan(page_kind="job_description", next_kind=kind, next_label="Apply", next_ref=entry.ref)
    outcome, _, _ = agent.press_next(page, plan, controls)
    assert outcome == "moved"
    assert page.url.endswith("/job/apply?source=x")
    assert page.evaluate("window.clicked") is None
    assert assistant._submission_guard.denial_count(page) == 0


FOOTER = ("<p>If you need a reasonable accommodation, a Human Resources representative will review your request "
          "and contact you to discuss it.</p>")
POSTING = ('<h1>Engineer</h1><h2>Responsibilities</h2><p>Build systems. Qualifications and requirements.</p>'
           '<a role="button" href="/job/apply">Apply</a>')


def test_a_posting_footer_that_says_review_your_request_is_still_a_posting(assistant, page):
    """f170: Marathon's footer 'will review your request' read as the application's review step, so the posting
    was not recognised and its Apply link was clicked instead of followed."""
    page.set_content(POSTING + FOOTER)
    assert assistant.on_job_description(page)


@pytest.mark.parametrize("text", ["Review your application before you submit it.", "Review and submit",
                                  "Please review your answers.", "Review your information"])
def test_the_applications_own_review_wording_is_still_not_a_posting(assistant, page, text):
    page.set_content(POSTING + f"<p>{text}</p>")
    assert not assistant.on_job_description(page)
