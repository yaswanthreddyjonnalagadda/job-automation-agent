"""Posting widgets must not masquerade as the application or its review step."""
import pytest
from playwright.sync_api import sync_playwright
from browser_automation import JobApplicationAssistant

@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        yield b
        b.close()

@pytest.fixture
def page(browser):
    page = browser.new_page()
    yield page
    page.close()

@pytest.fixture
def assistant():
    return JobApplicationAssistant.__new__(JobApplicationAssistant)

POSTING = """<main><h1>Network Engineer</h1><h2>Responsibilities</h2>
<p>Qualifications and requirements for this job.</p>
<a href="https://ats.example.invalid/application/123">Apply Now</a></main>"""

def test_posting_ignores_search_and_talent_community_fields(page, assistant):
    page.set_content('<header><input type="search"><input><select><option>Remote</option></select></header>'
        + POSTING + '<footer><form><h2>Join Talent Community</h2>'
        + '<input required>' * 5 + '<button>Join Talent Community</button></form></footer>')
    assert assistant.on_job_description(page)

def test_active_search_container_with_hidden_review_text_is_not_review(page, assistant):
    page.set_content('<form class="search-form location-pin-active"><input type="search">'
        '<select hidden><option>Review</option></select><button>Go</button></form>' + POSTING)
    assert not assistant.is_review_step(page)

@pytest.mark.parametrize("marker", [
    '<div aria-current="step">Review</div>',
    '<div data-automation-id="progressBarActiveStep">Review Application</div>',
    '<li class="active">Review</li>',
])
def test_real_visible_review_marker_is_retained(page, assistant, marker):
    page.set_content(marker)
    assert assistant.is_review_step(page)

def test_hidden_review_markers_and_submit_templates_are_not_review(page, assistant):
    page.set_content('<div hidden aria-current="step">Review</div><button hidden>Submit</button>' + POSTING)
    assert not assistant.is_review_step(page)

def test_application_fields_in_main_prevent_posting_classification(page, assistant):
    page.set_content(POSTING.replace('</main>', '<form>' + '<input>' * 4 + '<button>Apply</button></form></main>'))
    assert not assistant.on_job_description(page)

def test_application_already_filled_is_never_reclassified_as_posting(page, assistant):
    page.set_content(POSTING)
    assistant._form_filled_this_run = True
    assert not assistant.on_job_description(page)

def test_posting_entry_uses_get_without_relaxing_final_submission_guard(page, assistant):
    from submission_guard import SubmissionGuardV0
    requests = []
    def route(request):
        requests.append(request.request.method)
        request.fulfill(content_type='text/html', body='''<form onsubmit="window.sent=true;return false">
            <label>First name<input></label><button>Submit Application</button></form>''')
    page.route('https://ats.example.invalid/**', route)
    page.set_content('<header><form class="location-pin-active"><select hidden><option>Review</option></select>'
        '<input><input><input><input></form></header>' + POSTING
        + '<footer><form><input><input><input><input><button>Join Talent Community</button></form></footer>')
    page.locator('a').evaluate("e => e.onclick = () => { window.entryClicked = true; return false; }")
    guard = SubmissionGuardV0(page.context)
    assistant._submission_guard = guard
    guard.activate(page)
    assert assistant.follow_application_entry(page, page.get_by_role('link', name='Apply Now'))
    assert page.url == 'https://ats.example.invalid/application/123'
    assert requests == ['GET']
    assert page.evaluate('window.entryClicked') is None
    guard.activate(page)
    page.get_by_role('button', name='Submit Application').click()
    assert page.evaluate('window.sent') is None
    assert guard.denial_count(page) == 1

def test_talent_community_form_inside_main_is_not_the_job_application(page, assistant):
    widget = '<form><h2>Sign up for job alerts</h2>' + '<input required>' * 5 + '<button>Join Talent Community</button></form>'
    page.set_content(POSTING.replace('</main>', widget + '</main>'))
    assert assistant.on_job_description(page)

def test_talent_wording_does_not_hide_a_mixed_application_form(page, assistant):
    widget = '<form><h2>Join Talent Community</h2>' + '<input>' * 4 + '<button>Join Talent Community</button><button>Submit Application</button></form>'
    page.set_content(POSTING.replace('</main>', widget + '</main>'))
    assert not assistant.on_job_description(page)


@pytest.mark.parametrize("href", ["#apply", "javascript:void(0)"])
def test_non_navigation_apply_does_not_hide_page_fields(page, assistant, href):
    page.set_content(POSTING.replace('https://ats.example.invalid/application/123', href)
        + '<form>' + '<input>' * 4 + '<button>Submit Application</button></form>')
    assert not assistant.on_job_description(page)
