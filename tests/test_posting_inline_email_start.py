"""A posting that opens its application from an inline 'Email address' box and 'Start application' button (f284).

Color Health on CareerPuck, 31 October 2026: the posting showed an 'Apply' button, which the submission guard rightly
refused (an input is visible, so the page is not a plain posting), and beside it an 'Email address' box with a
'Start application ->' button -- the site's own way in. The agent pressed Apply twice, was refused twice and stopped.
A job-alert box ('Submit', 'Subscribe') is not this and is never filled.
"""
from types import SimpleNamespace

import page_agent
from submission_guard import SubmissionGuardV0
from test_run_recovery_regressions import assistant, browser, page  # noqa: F401

POSTING = ('<h1>Senior IT Systems Engineer</h1><button aria-label="Apply to Senior IT Systems Engineer">Apply</button>'
           '<section><label for="e">Email address</label><input id="e" type="text">'
           '<button id="go" onclick="document.body.dataset.started=document.getElementById(\'e\').value">'
           'Start application &rarr;</button></section>')
ALERT = ('<h1>Engineer</h1><button aria-label="Apply to Engineer">Apply</button>'
         '<section><h2>Get job alerts</h2><label for="e">Email address</label><input id="e" type="email">'
         '<button onclick="document.body.dataset.started=\'alert\'">Subscribe</button></section>')


def agent_on(assistant, page, tmp_path, html):
    page.set_content(html)
    assistant._submission_guard = SubmissionGuardV0(page.context)
    return page_agent.PageAgent(
        assistant, SimpleNamespace(), SimpleNamespace(), SimpleNamespace(email="me@example.com", accept_application_privacy_prompts=False),
        SimpleNamespace(raw_text=""), SimpleNamespace(url=page.url), resume_file=None, job_dir=tmp_path)


def press_apply(agent, page):
    controls = page_agent.parse_snapshot(agent.snapshot(page))
    entry = next(c for c in controls if c.name.startswith("Apply"))
    plan = page_agent.PagePlan(page_kind="job_description", next_kind="open_application", next_label="Apply", next_ref=entry.ref)
    return agent.press_next(page, plan, controls)


def test_refused_apply_uses_the_inline_start_application_box(assistant, page, tmp_path):
    agent = agent_on(assistant, page, tmp_path, POSTING)
    outcome, _, _ = press_apply(agent, page)
    assert outcome == "moved"
    assert page.evaluate("document.body.dataset.started") == "me@example.com"


def test_a_job_alert_box_is_never_filled_or_pressed(assistant, page, tmp_path):
    agent = agent_on(assistant, page, tmp_path, ALERT)
    outcome, _, _ = press_apply(agent, page)
    assert outcome == "stop"
    assert page.evaluate("document.body.dataset.started") is None
