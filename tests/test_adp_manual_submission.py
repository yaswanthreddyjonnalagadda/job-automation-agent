"""A manual ADP submission must end the review wait and allow browser cleanup."""
import json
from types import SimpleNamespace

import pytest

from browser_automation import JobApplicationAssistant
from job_tracker import JobTracker

POSTING = 'https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?cid=tenant&ccId=center&jobId=123'
RECEIPT = 'https://workforcenow.adp.com/mascsr/applicant/mdf/recruitment/postLogin.html?cid=tenant&ccId=center&jobId=123&jobId=123&requisitionId=req-abc'
TITLE = 'Security Engineer'
METADATA = {'itemID':'req-abc','requisitionTitle':TITLE,'customFieldGroup':{'stringFields':[
    {'nameCode':{'codeValue':'ExternalJobID'},'stringValue':'123'}]}}


@pytest.fixture(scope='module')
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        yield browser
        browser.close()


@pytest.fixture
def page(browser):
    context = browser.new_context()
    page = context.new_page()
    yield page
    context.close()


def receipt_html(status='<span>Application Submitted</span>'):
    return ('<main><header><h1>Security Engineer</h1></header><article><p>'
            + 'Responsibilities and qualifications. ' * 180
            + '</p><p>Contact us if you need assistance to complete this application.</p></article>'
            + '<div><div><div>' + status + '</div></div></div></main>')


def prepare(page, tmp_path, monkeypatch, *, html=None, metadata=None, current=RECEIPT, api_status=200):
    def route(r):
        api = '/job-requisitions/' in r.request.url
        r.fulfill(status=api_status if api else 200,
                  content_type='application/json' if api else 'text/html',
                  body=json.dumps(METADATA if metadata is None else metadata) if api else
                       (receipt_html() if html is None else html))
    api_requests = []
    def get(url, **kwargs):
        api_requests.append('GET')
        return SimpleNamespace(ok=200 <= api_status < 300,
                               json=lambda:METADATA if metadata is None else metadata)
    monkeypatch.setattr(page.request, 'get', get)
    page.route('**/*', route)
    page.goto(current)
    tracker = JobTracker(tmp_path / 'tracker.db')
    tracker.create(dedup_key='application', url=POSTING, title=TITLE, company='Example')
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    assistant.tracker = tracker
    assistant.application_key = 'application'
    assistant._profile = SimpleNamespace(check_gmail_for_confirmation=False)
    assistant._api_requests = api_requests
    return assistant


def test_manual_receipt_after_long_job_description_is_confirmed(page, tmp_path, monkeypatch):
    assistant = prepare(page, tmp_path, monkeypatch)
    assert assistant.submission_confirmed(page, TITLE)


@pytest.mark.parametrize('kwargs', [
    {'html':receipt_html('<span hidden>Application Submitted</span>')},
    {'html':receipt_html('<div style="opacity:0"><span>Application Submitted</span></div>')},
    {'html':receipt_html('<div aria-hidden=true><span>Application Submitted</span></div>')},
    {'html':receipt_html('<span>Application Submitted after the deadline</span>')},
    {'html':receipt_html()+'<label>Full Name<input required></label>'},
    {'html':receipt_html()+'<button>Submit</button>'},
    {'html':receipt_html('<span>Application Submitted</span><span>Draft</span>')},
    {'html':'<main><h1>Security Engineer</h1></main><section><h2>Other Job</h2><span>Application Submitted</span></section>'},
    {'metadata':{**METADATA,'itemID':'other'}},
    {'api_status':503},
    {'current':RECEIPT+'&jobId=other'},
    {'current':RECEIPT.replace('cid=tenant','cid=other')},
    {'current':RECEIPT.replace('workforcenow.adp.com','workforcenow.adp.com.evil.example')},
])
def test_unverified_or_active_application_does_not_confirm(page, tmp_path, monkeypatch, kwargs):
    assistant = prepare(page, tmp_path, monkeypatch, **kwargs)
    assert not assistant.submission_confirmed(page, TITLE)


def test_manual_receipt_ends_the_flow_and_closes_browser_context(page, tmp_path, monkeypatch):
    import apply_flow
    import browser_automation
    import config
    import page_agent

    assistant = prepare(page, tmp_path, monkeypatch)
    tracker = assistant.tracker
    assistant._context, assistant._playwright = page.context, None
    monkeypatch.setattr(assistant, 'raise_window', lambda p: None)
    monkeypatch.setattr(assistant, 'is_review_step', lambda p: False)
    outcome = SimpleNamespace(kind='owner_needed', page=page,
        summary='the site says this application was already sent',
        reasons=['the site says this application was already sent'])
    agent = SimpleNamespace(run=lambda p:outcome, tab=lambda p:p, notes=[], pages_read=1,
        uncertain_action_labels=lambda:[], remember_page_state=lambda p:None,
        note_owner_changes=lambda p:None, _ensure_state=lambda:None,
        forget_sign_in_attempts=lambda **k:None)
    monkeypatch.setattr(page_agent, 'PageAgent', lambda *a, **k:agent)
    for name in ('store_materials','remember_progress','write_recovery_checkpoint','save_stop_page','refresh_answer_bank'):
        monkeypatch.setattr(apply_flow, name, lambda *a, **k:None)
    polls = []
    def poll(seconds):
        polls.append(seconds)
        if len(polls)>3:
            pytest.fail('Manual submission was not detected; the browser would stay open')
    monkeypatch.setattr(browser_automation.time, 'sleep', poll)
    signal = tmp_path / '_signal_Example_Engineer.txt'
    signal.with_name('_job_Example_Engineer.json').write_text(json.dumps({'title':TITLE,'company':'Example','url':POSTING}))
    job = SimpleNamespace(title=TITLE, company='Example', url=POSTING)
    requests = assistant._api_requests
    page.on('request', lambda r:requests.append(r.method))
    try:
        apply_flow.run_page_agent(assistant, page, SimpleNamespace(),
            SimpleNamespace(generate_cover_letters=False), config.UserProfile(),
            SimpleNamespace(), job, tracker, 'application', tmp_path,
            tmp_path/'Resume.pdf', SimpleNamespace(timeout=30), signal)
        assert tracker.get('application').status == 'submitted'
        assert 'Submitted by you' in tracker.get('application').notes
        assert tracker.get_submission_effect_state('application') is None
        assert polls == [2.0]
        assert requests and all(method=='GET' for method in requests)
    finally:
        assistant.__exit__(None, None, None)
    assert page.is_closed()


def test_receipt_is_rechecked_after_the_identity_request(page, tmp_path, monkeypatch):
    assistant = prepare(page, tmp_path, monkeypatch)
    def changed(url, **kwargs):
        page.set_content(receipt_html('<span>Draft</span>'))
        return SimpleNamespace(ok=True, json=lambda:METADATA)
    monkeypatch.setattr(page.request, 'get', changed)
    assert not assistant.submission_confirmed(page, TITLE)
