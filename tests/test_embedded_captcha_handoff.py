"""Mutual of Enumclaw: the handoff receives the outer tab, not the ATS frame.

All pages and challenge placeholders are local fixtures. Nothing solves a CAPTCHA.
"""
import json

import pytest

from browser_automation import JobApplicationAssistant


HOST = "https://jobs-tenant.icims.com"
PROFILE = """<h2>Create your profile</h2>
<input type=password><input type=password><input type=submit value='Submit Profile'>
<iframe src='/captcha-placeholder' title='hCaptcha challenge' width=300 height=100></iframe>"""
APPLICATION = "<h2>Candidate Profile</h2><label>Mobile Phone<input></label>"


@pytest.fixture
def page():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        def serve(route):
            path = route.request.url.removeprefix(HOST)
            body = {
                "/outer": '<iframe id=icims_content_iframe name=icims_content_iframe src="/profile"></iframe>',
                "/profile": PROFILE,
                "/candidate": APPLICATION,
                "/captcha-placeholder": "<p>Local challenge placeholder</p>",
            }.get(path, "")
            route.fulfill(content_type="text/html", body=body)
        context.route("**/*", serve)
        page = context.new_page()
        page.goto(HOST + "/outer")
        yield page
        context.close()
        browser.close()


def assistant(page, transition=None):
    a = JobApplicationAssistant.__new__(JobApplicationAssistant)
    a._profile = None
    a.raise_window = lambda _: None
    a.pending_attestations = lambda _: []
    a.find_submit_button = lambda _: None
    a.submission_confirmed = lambda *_: False
    polls = 0
    def poll(seconds, _page):
        nonlocal polls
        polls += 1
        if polls == 2 and transition:
            transition()
        page.wait_for_timeout(20)
    a._sleep_for_phone = poll
    return a


@pytest.mark.parametrize("transition", ["navigate", "replace", "same_url"])
def test_outer_tab_handoff_observes_application_frame_progress(page, tmp_path, transition):
    def owner_finished():
        if transition == "replace":
            page.evaluate("""() => {
                const old = document.querySelector('iframe');
                const next = old.cloneNode(); next.src = '/candidate'; old.replaceWith(next);
            }""")
            page.frame_locator("#icims_content_iframe").get_by_text("Candidate Profile").wait_for()
        elif transition == "navigate":
            page.frame(name="icims_content_iframe").goto(HOST + "/candidate")
        else:
            # The next form may render at the same URL and retain a widget.
            frame = page.frame(name="icims_content_iframe")
            frame.evaluate("() => document.querySelectorAll('input').forEach(e => e.remove())")
            frame.locator("h2").evaluate("e => e.textContent = 'Candidate Profile'")
    a = assistant(page, owner_finished)
    # Account creation currently returns owner_needed, so for_captcha defaults False.
    assert a._wait_for_signal(tmp_path / "signal", poll_seconds=1, timeout_seconds=6,
                              page=page) == "refresh"
    assert page.url == HOST + "/outer"


def test_embedded_challenge_waits_without_pressing_profile_submit(page, tmp_path):
    frame = page.frame(name="icims_content_iframe")
    frame.evaluate("() => { document.querySelector('input[type=submit]').onclick = () => window.pressed = true; }")
    with pytest.raises(TimeoutError):
        assistant(page)._wait_for_signal(tmp_path / "signal", poll_seconds=1,
                                         timeout_seconds=4, page=page)
    assert frame.evaluate("Boolean(window.pressed)") is False


def test_temporarily_missing_frame_does_not_mean_challenge_finished(page, tmp_path):
    def detach():
        page.locator("#icims_content_iframe").evaluate("e => e.remove()")
    with pytest.raises(TimeoutError):
        assistant(page, detach)._wait_for_signal(tmp_path / "signal", poll_seconds=1,
                                                 timeout_seconds=4, page=page)


def test_stop_evidence_preserves_validity_and_guard_reason_without_private_text(page, tmp_path):
    import diagnostics
    from submission_guard import SubmissionGuardV0
    frame = page.frame(name="icims_content_iframe")
    frame.set_content("""<form>
      <input type=email required value='PRIVATE-INVALID-EMAIL'>
      <input type=password required value='PRIVATE-PASSWORD'>
      <input required aria-label='PRIVATE-LABEL'>
      <input type=submit value='PRIVATE-BUTTON' disabled>
      <div role=alert>PRIVATE-SERVER-TEXT</div>
    </form>""")
    guard = SubmissionGuardV0(page.context)
    guard.activate(page)
    frame.evaluate("document.querySelector('form').submit()")
    target = tmp_path / 'stopped_20261019_000000.state.json'
    assert diagnostics.capture_form_state(page, target)
    encoded = target.read_text()
    assert 'PRIVATE' not in encoded
    item = next(x for x in json.loads(encoded)['frames'] if x.get('fields'))
    assert item['readable']
    assert item['fields'][0]['failures'] == ['typeMismatch']
    assert item['fields'][1]['password'] and item['fields'][1]['filled']
    assert item['fields'][2]['failures'] == ['valueMissing']
    assert item['disabled_submits'] == 1
    assert item['visible_alerts'] == 1
    assert item['guard_denials'] == {'javascript_form_submit': 1}


def test_stop_evidence_never_inspects_challenge_contents(page, tmp_path):
    import diagnostics
    target = tmp_path / 'stopped_20261019_000001.state.json'
    assert diagnostics.capture_form_state(page, target)
    report = json.loads(target.read_text())
    # Outer document and application only; no CAPTCHA-frame inspection.
    assert len(page.frames) == 3
    assert len(report['frames']) == 2
