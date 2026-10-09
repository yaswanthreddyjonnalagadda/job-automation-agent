"""Static final-review answers and server-bound Workday job identity."""
from types import SimpleNamespace

import pytest

import safety
from browser_automation import JobApplicationAssistant
from sites.workday import WorkdayAdapter
from test_dropdown_reading import browser, page

POSTING = 'https://employer.wd3.myworkdayjobs.com/en-US/External/job/Test-State/Network-Engineer_R123-1'
CURRENT = POSTING.replace('/Test-State/', '/Test%2C-State/') + '/apply/applyManually'
QUESTION = 'Will you now or in the future require visa sponsorship?*'
HTML = f'''<h2 data-automation-id="jobTitleHeading">Network Engineer</h2>
<li data-automation-id="progressBarActiveStep">current step 5 of 5 Review</li>
<div data-automation-id="applyFlowReviewPage">
<h3>My Information</h3>
<div><h4>Name</h4><div><span>Alex Example</span></div></div>
<div role="group"><div><div><label>City</label><div><span>Fairfax</span></div></div></div></div>
<h3>Application Questions</h3><div role="group">
<div><div><div data-automation-id="richText"><p><b>{QUESTION[:-1]}</b><abbr>*</abbr></p></div></div><div><span>Yes</span></div></div>
</div></div><button data-automation-id="pageFooterNextButton">Submit</button>'''


def setup(page, html=HTML, current=CURRENT):
    page.route('**/*', lambda r: r.fulfill(content_type='text/html', body=html))
    page.goto(current)
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    assistant.values = safety.AgentValues()
    return assistant


def test_review_reads_static_answers_and_retains_exact_question_approval(page):
    assistant = setup(page)
    assistant.values.record(page, 'aria:' + QUESTION, 'Yes', 'profile.requires_visa_sponsorship')
    fields = assistant.read_back_fields(page)
    by_label = {f['label']: f for f in fields}
    assert by_label['Name']['value'] == 'Alex Example'
    assert by_label['City']['value'] == 'Fairfax'
    assert by_label[QUESTION]['value'] == 'Yes'
    assert by_label[QUESTION]['ref'] == 'aria:' + QUESTION
    assert all(f['required'] for f in fields)  # summary omits original required metadata
    approved = safety.approved_values(fields, SimpleNamespace(full_name='Alex Example', city='Fairfax'), {}, assistant.values.records)
    assert all(c.matches for c in safety.compare_fields(fields, approved))


def metadata(current=CURRENT):
    return {'jobPostingInfo': {'title': 'Network Engineer', 'jobReqId': 'R123',
            'externalUrl': POSTING.replace('/en-US', '')}}


def test_location_slug_redirect_requires_server_job_identity(page, monkeypatch):
    setup(page)
    calls = []
    def get(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(ok=True, json=lambda: metadata())
    monkeypatch.setattr(page.request, 'get', get)
    assert WorkdayAdapter().submission_posting_url(page, POSTING, 'Network Engineer') == POSTING
    assert calls[0][0] == 'https://employer.wd3.myworkdayjobs.com/wday/cxs/employer/External/job/Test-State/Network-Engineer_R123-1'
    assert calls[0][1]['max_redirects'] == 0


@pytest.mark.parametrize('change', [
    {'title': 'Other Engineer'}, {'jobReqId': 'R999'}, {'externalUrl': CURRENT.removesuffix('/apply/applyManually')},
    {'externalUrl': CURRENT.replace('/External/', '/Other/').removesuffix('/apply/applyManually')},
    {'externalUrl': CURRENT.replace('employer.wd3', 'another.wd3').removesuffix('/apply/applyManually')},
    {'externalUrl': ''},
])
def test_identity_refuses_conflicting_metadata(page, monkeypatch, change):
    setup(page)
    data = metadata(); data['jobPostingInfo'].update(change)
    monkeypatch.setattr(page.request, 'get', lambda *a, **k: SimpleNamespace(ok=True, json=lambda: data))
    assert WorkdayAdapter().submission_posting_url(page, POSTING, 'Network Engineer') is None


@pytest.mark.parametrize('current', [CURRENT.replace('/External/', '/Other/'),
    CURRENT.replace('R123', 'R999'), CURRENT.replace('https:', 'http:'),
    CURRENT.replace('myworkdayjobs.com', 'myworkdayjobs.com.evil.example')])
def test_identity_rejects_wrong_route_before_network(page, monkeypatch, current):
    setup(page, current=current)
    def unexpected(*a, **k):
        pytest.fail('Wrong job/tenant/host must not trigger a metadata request')
    monkeypatch.setattr(page.request, 'get', unexpected)
    assert WorkdayAdapter().submission_posting_url(page, POSTING, 'Network Engineer') is None


@pytest.mark.parametrize('extra', ['<div><span>Unlabelled answer</span></div>',
    '<div><label>City</label><div><span>Other City</span></div><span>Ambiguous</span></div>'])
def test_unrecognized_review_content_refuses_instead_of_empty_evidence(page, extra):
    assistant = setup(page, HTML.replace('</div><button', extra + '</div><button'))
    fields = assistant.read_back_fields(page)
    assert any(f['required'] and not f['value'] for f in fields)


def test_review_values_are_reread_and_changed_answers_do_not_match_old_approval(page):
    assistant = setup(page)
    assistant.values.record(page, 'aria:' + QUESTION, 'Yes', 'profile.requires_visa_sponsorship')
    page.locator('[data-automation-id=richText]').locator('..').locator('..').locator('span').evaluate("e=>e.textContent='No'")
    fields = assistant.read_back_fields(page)
    approved = safety.approved_values(fields, SimpleNamespace(), {}, assistant.values.records)
    comparison = next(c for c in safety.compare_fields(fields, approved) if c.label == QUESTION)
    assert not comparison.matches and comparison.required


def test_repeated_labels_cannot_reuse_one_approval_for_distinct_entries(page):
    extra = '<div><label>City</label><div><span>Elsewhere</span></div></div>'
    assistant = setup(page, HTML.replace('</div><button', extra + '</div><button'))
    assistant.values.record(page, 'aria:City', 'Fairfax', 'profile.city')
    cities = [f for f in assistant.read_back_fields(page) if 'City' in f['label']]
    assert len(cities) == 2
    assert len({f['label'] for f in cities}) == 2
    assert all(f['ref'] != 'aria:City' for f in cities)


def test_final_page_handoff_does_not_claim_verified_setting_is_off(page, monkeypatch):
    from page_agent import PageAgent
    agent = PageAgent.__new__(PageAgent)
    agent.config = SimpleNamespace(auto_submit_verified_only=True)
    monkeypatch.setattr(agent, '_tailored_resume_missing', lambda p: False)
    message = agent.submit_gate(page, [])
    assert 'verified submission checks' in message
    assert 'off' not in message


@pytest.mark.parametrize('extra', [
    '<div>Unlabelled text<span></span></div>',
    '<input aria-label="New required answer" required value="Unverified">',
    '<div role="checkbox" aria-checked="true">New attestation</div>',
    '<iframe></iframe>',
])
def test_dynamic_or_unlabelled_review_content_remains_held(page, extra):
    assistant = setup(page, HTML.replace('</div><button', extra + '</div><button'))
    fields = assistant.read_back_fields(page)
    assert any(f['required'] and not f['value'] for f in fields)


def test_empty_attachment_label_is_not_an_unanswered_application_field(page):
    attachment = '''<div data-automation-id="formField-"><label style="display:block;height:20px"></label><div>
      <div data-automation-id="attachments-FileUpload"><div data-automation-id="file-upload-item-name">Resume_Example.pdf</div></div>
    </div></div>'''
    assistant = setup(page, HTML.replace('</div><button', attachment + '</div><button'))
    fields = assistant.read_back_fields(page)
    assert {f['label'] for f in fields} == {'Name', 'City', QUESTION}


@pytest.mark.parametrize('change', ['title', 'url', 'review'])
def test_identity_rechecks_page_after_metadata_response(page, monkeypatch, change):
    setup(page)
    def get(*args, **kwargs):
        if change == 'title':
            page.locator('[data-automation-id=jobTitleHeading]').evaluate("e=>e.textContent='Other role'")
        elif change == 'url':
            page.evaluate("history.replaceState({}, '', location.pathname.replace('R123', 'R999'))")
        else:
            page.locator('[data-automation-id=applyFlowReviewPage]').evaluate("e=>e.hidden=true")
        return SimpleNamespace(ok=True, json=lambda: metadata())
    monkeypatch.setattr(page.request, 'get', get)
    assert WorkdayAdapter().submission_posting_url(page, POSTING, 'Network Engineer') is None


def test_hidden_review_answer_cannot_supply_visible_evidence(page):
    assistant = setup(page)
    page.locator('[data-automation-id=richText]').locator('..').locator('..').evaluate("e=>e.style.opacity='0'")
    fields = assistant.read_back_fields(page)
    assert any(f['required'] and not f['value'] for f in fields)


def test_hidden_value_inside_visible_question_is_not_approved(page):
    assistant = setup(page)
    page.locator('[data-automation-id=richText]').locator('..').locator('..').locator('span').evaluate("e=>e.style.opacity='0'")
    fields = assistant.read_back_fields(page)
    assert any(f['required'] and not f['value'] for f in fields)
